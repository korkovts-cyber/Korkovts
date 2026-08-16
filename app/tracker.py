import asyncio
import pandas as pd
from .config import SIGNAL_MAX_AGE_HOURS,ENTRY_EXPIRY_HOURS,ROUND_TRIP_COST_PCT
from .db import open_signals,checkpoint,close_signal,activate_signal
from .market import get_klines_since

def _iso(value):
    return pd.Timestamp(value).isoformat()

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
    start=pd.Timestamp(row.get("last_checked_at") or row["created_at"])
    start=start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    if preloaded is None:
        async with semaphore:
            df=await get_klines_since(row["symbol"],"1m",int(start.timestamp()*1000)+1,1500)
    else:
        boundary=start+pd.Timedelta("1ms")
        df=preloaded[preloaded.open_time>=boundary] if not preloaded.empty else preloaded
    events=[]
    created=pd.Timestamp(row["created_at"])
    created=created.tz_localize("UTC") if created.tzinfo is None else created.tz_convert("UTC")
    now=pd.Timestamp.now(tz="UTC")
    entry_expiry=1 if row.get("timeframe")=="15M" else ENTRY_EXPIRY_HOURS
    max_age=4 if row.get("timeframe")=="15M" else SIGNAL_MAX_AGE_HOURS
    if row["status"] in ("SENT","WAITING","OPEN"):
        entry=float(row["entry"]); hit=df[(df.low<=entry)&(df.high>=entry)] if not df.empty else df
        invalid=(df[df.low<=float(row["stop"])] if row["side"]=="LONG"
                 else df[df.high>=float(row["stop"])]) if not df.empty else df
        # If the setup is invalidated before price ever reaches the advertised
        # entry, it is not a loss and must never be activated later.
        if not invalid.empty and (hit.empty or invalid.index[0]<hit.index[0]):
            closed_at=_iso(df.loc[invalid.index[0]].close_time)
            close_signal(row["id"],"INVALIDATED",float(row["stop"]),0,closed_at,0,0)
            return [("CLOSED",row["id"],row["symbol"],"INVALIDATED",row.get("source_chat_id"))]
        if hit.empty:
            if now-created>=pd.Timedelta(f"{entry_expiry}h"):
                closed_at=_iso(df.iloc[-1].close_time) if not df.empty else now.isoformat()
                close_signal(row["id"],"ENTRY_EXPIRED",entry,0,closed_at,0,0)
                return [("CLOSED",row["id"],row["symbol"],"ENTRY_EXPIRED",row.get("source_chat_id"))]
            if not df.empty: checkpoint(row["id"],_iso(df.iloc[-1].close_time),0,0)
            return []
        first_index=hit.index[0]; activated_at=_iso(df.loc[first_index].close_time)
        activate_signal(row["id"],activated_at)
        row["status"]="ACTIVE"; row["activated_at"]=activated_at
        df=df.loc[first_index:]; events.append(("ACTIVE",row["id"],row["symbol"],"ENTRY",row.get("source_chat_id")))
    outcome,mfe,mae=evaluate(row,df)
    mfe=max(float(row.get("max_favorable_r") or 0),mfe)
    mae=max(float(row.get("max_adverse_r") or 0),mae)
    if outcome:
        result,price,pnl_r,closed_at=outcome
        close_signal(row["id"],result,price,pnl_r,closed_at,mfe,mae)
        events.append(("CLOSED",row["id"],row["symbol"],result,row.get("source_chat_id"))); return events
    activated=pd.Timestamp(row.get("activated_at") or row["created_at"])
    activated=activated.tz_localize("UTC") if activated.tzinfo is None else activated.tz_convert("UTC")
    if not df.empty and now-activated>=pd.Timedelta(f"{max_age}h"):
        price=float(df.iloc[-1].close); entry=float(row["entry"]); risk=abs(entry-float(row["stop"]))
        pnl_r=((price-entry) if row["side"]=="LONG" else (entry-price))/risk-_cost_r(row)
        closed_at=_iso(df.iloc[-1].close_time)
        close_signal(row["id"],"EXPIRED",price,pnl_r,closed_at,mfe,mae)
        events.append(("CLOSED",row["id"],row["symbol"],"EXPIRED",row.get("source_chat_id"))); return events
    if not df.empty:
        checkpoint(row["id"],_iso(df.iloc[-1].close_time),mfe,mae)
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
    loaded=await asyncio.gather(*(load(symbol,symbol_rows) for symbol,symbol_rows in grouped.items()),
                                return_exceptions=True)
    frames={item[0]:item[1] for item in loaded if isinstance(item,tuple)}
    results=await asyncio.gather(*(update_one(row,preloaded=frames[row["symbol"]])
        for row in rows if row["symbol"] in frames),return_exceptions=True)
    return [event for batch in results if isinstance(batch,list) for event in batch]
