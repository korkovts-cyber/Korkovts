import asyncio
import logging
from datetime import timedelta

import pandas as pd

from .config import ENTRY_EXPIRY_HOURS, ROUND_TRIP_COST_PCT, SIGNAL_MAX_AGE_HOURS
from .db import activate_signal, checkpoint, close_signal, open_signals
from .market import get_klines_since

log=logging.getLogger(__name__)

def _iso(value):
    return pd.Timestamp(value).isoformat()

def _utc(value):
    stamp=pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")

def _until(df,deadline):
    if df.empty:
        return df
    return df[df.close_time<=deadline]

def _cost_r(row):
    entry=float(row["entry"]); risk=abs(entry-float(row["stop"]))
    return entry*(ROUND_TRIP_COST_PCT/100)/risk if risk>0 else 0.0

def evaluate(row,df):
    entry=float(row["entry"]); stop=float(row["stop"])
    risk=abs(entry-stop)
    if risk<=0 or df.empty: return None,0,0
    long=row["side"]=="LONG"; mfe=mae=0.0; cost_r=_cost_r(row)
    for _,c in df.iterrows():
        high=float(c.high); low=float(c.low)
        if long:
            mfe=max(mfe,(high-entry)/risk); mae=max(mae,(entry-low)/risk)
            # Conservative: if stop and target are touched in one candle, stop wins.
            if low<=stop: return ("SL",stop,-1.0-cost_r,_iso(c.close_time)),mfe,mae
            if high>=float(row["tp2"]): return ("TP2",float(row["tp2"]),2.0-cost_r,_iso(c.close_time)),mfe,mae
        else:
            mfe=max(mfe,(entry-low)/risk); mae=max(mae,(high-entry)/risk)
            if high>=stop: return ("SL",stop,-1.0-cost_r,_iso(c.close_time)),mfe,mae
            if low<=float(row["tp2"]): return ("TP2",float(row["tp2"]),2.0-cost_r,_iso(c.close_time)),mfe,mae
    return None,mfe,mae

async def update_one(row,semaphore=None,preloaded=None):
    start=_utc(row.get("last_checked_at") or row["created_at"])
    if preloaded is None:
        async with semaphore:
            df=await get_klines_since(row["symbol"],"1m",int(start.timestamp()*1000)+1,1500)
    else:
        boundary=start+timedelta(milliseconds=1)
        df=preloaded[preloaded.open_time>=boundary] if not preloaded.empty else preloaded
    events=[]
    created=_utc(row["created_at"])
    now=pd.Timestamp.now(tz="UTC")
    entry_expiry=1 if row.get("timeframe")=="15M" else ENTRY_EXPIRY_HOURS
    max_age=4 if row.get("timeframe")=="15M" else SIGNAL_MAX_AGE_HOURS
    if row["status"] in ("SENT","WAITING","OPEN"):
        entry_deadline=created+timedelta(hours=entry_expiry)
        waiting_df=_until(df,entry_deadline)
        entry=float(row["entry"])
        hit=(waiting_df[(waiting_df.low<=entry)&(waiting_df.high>=entry)]
             if not waiting_df.empty else waiting_df)
        invalid=(waiting_df[waiting_df.low<=float(row["stop"])] if row["side"]=="LONG"
                 else waiting_df[waiting_df.high>=float(row["stop"])]) if not waiting_df.empty else waiting_df
        # If the setup is invalidated before price ever reaches the advertised
        # entry, it is not a loss and must never be activated later.
        if not invalid.empty and (hit.empty or invalid.index[0]<hit.index[0]):
            closed_at=_iso(df.loc[invalid.index[0]].close_time)
            close_signal(row["id"],"INVALIDATED",float(row["stop"]),0,closed_at,0,0)
            return [("CLOSED",row["id"],row["symbol"],"INVALIDATED",row.get("source_chat_id"))]
        if hit.empty:
            if now>=entry_deadline:
                closed_at=entry_deadline.isoformat()
                close_signal(row["id"],"ENTRY_EXPIRED",entry,0,closed_at,0,0)
                return [("CLOSED",row["id"],row["symbol"],"ENTRY_EXPIRED",row.get("source_chat_id"))]
            if not waiting_df.empty:
                checkpoint(row["id"],_iso(waiting_df.iloc[-1].close_time),0,0)
            return []
        first_index=hit.index[0]; activated_at=_iso(waiting_df.loc[first_index].close_time)
        activate_signal(row["id"],activated_at)
        row["status"]="ACTIVE"; row["activated_at"]=activated_at
        df=df.loc[first_index:]
        events.append(("ACTIVE",row["id"],row["symbol"],"ENTRY",row.get("source_chat_id")))
    activated=_utc(row.get("activated_at") or row["created_at"])
    trade_deadline=activated+timedelta(hours=max_age)
    evaluation_df=_until(df,trade_deadline)
    outcome,mfe,mae=evaluate(row,evaluation_df)
    mfe=max(float(row.get("max_favorable_r") or 0),mfe)
    mae=max(float(row.get("max_adverse_r") or 0),mae)
    if outcome:
        result,price,pnl_r,closed_at=outcome
        close_signal(row["id"],result,price,pnl_r,closed_at,mfe,mae)
        events.append(("CLOSED",row["id"],row["symbol"],result,row.get("source_chat_id"))); return events
    if now>=trade_deadline:
        if not evaluation_df.empty:
            price=float(evaluation_df.iloc[-1].close)
        elif not df.empty:
            # The first candle after expiry is only a price proxy; its high/low
            # must not turn an already expired signal into a win or a loss.
            price=float(df.iloc[0].open)
        else:
            price=float(row["entry"])
        entry=float(row["entry"]); risk=abs(entry-float(row["stop"]))
        pnl_r=((price-entry) if row["side"]=="LONG" else (entry-price))/risk-_cost_r(row)
        closed_at=trade_deadline.isoformat()
        close_signal(row["id"],"EXPIRED",price,pnl_r,closed_at,mfe,mae)
        events.append(("CLOSED",row["id"],row["symbol"],"EXPIRED",row.get("source_chat_id"))); return events
    if not evaluation_df.empty:
        checkpoint(row["id"],_iso(evaluation_df.iloc[-1].close_time),mfe,mae)
    return events

async def update_outcomes():
    rows=open_signals()
    if not rows:
        return []
    semaphore=asyncio.Semaphore(5)
    grouped={}
    for row in rows:
        grouped.setdefault(row["symbol"],[]).append(row)
    async def load(symbol,symbol_rows):
        starts=[pd.Timestamp(row.get("last_checked_at") or row["created_at"]) for row in symbol_rows]
        earliest=min(value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
                     for value in starts)
        async with semaphore:
            frame=await get_klines_since(symbol,"1m",int(earliest.timestamp()*1000)+1,1500)
        return symbol,frame
    groups=list(grouped.items())
    loaded=await asyncio.gather(*(load(symbol,symbol_rows) for symbol,symbol_rows in groups),
                                return_exceptions=True)
    frames={}
    for (symbol,_),item in zip(groups,loaded):
        if isinstance(item,Exception):
            log.error("outcome history load failed for %s: %s",symbol,item,
                      exc_info=(type(item),item,item.__traceback__))
        else:
            frames[item[0]]=item[1]
    tracked=[row for row in rows if row["symbol"] in frames]
    results=await asyncio.gather(*(update_one(row,preloaded=frames[row["symbol"]])
                                   for row in tracked),return_exceptions=True)
    events=[]
    for row,batch in zip(tracked,results):
        if isinstance(batch,Exception):
            log.error("outcome evaluation failed for signal=%s symbol=%s: %s",
                      row["id"],row["symbol"],batch,
                      exc_info=(type(batch),batch,batch.__traceback__))
        else:
            events.extend(batch)
    return events
