import asyncio
import pandas as pd
from .config import SIGNAL_MAX_AGE_HOURS,ENTRY_EXPIRY_HOURS
from .db import (open_signals,checkpoint,close_signal,activate_signal,
    activate_paper_trades,settle_paper_trades)
from .market import get_klines_since

def _iso(value):
    return pd.Timestamp(value).isoformat()

def evaluate(row,df):
    entry=float(row["entry"]); stop=float(row["stop"])
    risk=abs(entry-stop)
    if risk<=0 or df.empty: return None,0,0
    long=row["side"]=="LONG"; mfe=mae=0.0
    for _,c in df.iterrows():
        high=float(c.high); low=float(c.low)
        if long:
            mfe=max(mfe,(high-entry)/risk); mae=max(mae,(entry-low)/risk)
            # Conservative: if stop and target are touched in one candle, stop wins.
            if low<=stop: return ("SL",stop,-1.0,_iso(c.close_time)),mfe,mae
            if high>=float(row["tp2"]): return ("TP2",float(row["tp2"]),2.0,_iso(c.close_time)),mfe,mae
        else:
            mfe=max(mfe,(entry-low)/risk); mae=max(mae,(high-entry)/risk)
            if high>=stop: return ("SL",stop,-1.0,_iso(c.close_time)),mfe,mae
            if low<=float(row["tp2"]): return ("TP2",float(row["tp2"]),2.0,_iso(c.close_time)),mfe,mae
    return None,mfe,mae

async def update_one(row,semaphore):
    start=pd.Timestamp(row.get("last_checked_at") or row["created_at"])
    start=start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    async with semaphore:
        df=await get_klines_since(row["symbol"],"1m",int(start.timestamp()*1000)+1,1500)
    events=[]
    created=pd.Timestamp(row["created_at"])
    created=created.tz_localize("UTC") if created.tzinfo is None else created.tz_convert("UTC")
    now=pd.Timestamp.now(tz="UTC")
    if row["status"] in ("WAITING","OPEN"):
        entry=float(row["entry"]); hit=df[(df.low<=entry)&(df.high>=entry)] if not df.empty else df
        if hit.empty:
            if now-created>=pd.Timedelta(hours=ENTRY_EXPIRY_HOURS):
                closed_at=_iso(df.iloc[-1].close_time) if not df.empty else now.isoformat()
                close_signal(row["id"],"ENTRY_EXPIRED",entry,0,closed_at,0,0)
                settled=settle_paper_trades(row["id"])
                return [("CLOSED",row["id"],row["symbol"],"ENTRY_EXPIRED",settled)]
            if not df.empty: checkpoint(row["id"],_iso(df.iloc[-1].close_time),0,0)
            return []
        first_index=hit.index[0]; activated_at=_iso(df.loc[first_index].close_time)
        activate_signal(row["id"],activated_at); activate_paper_trades(row["id"],activated_at)
        row["status"]="ACTIVE"; row["activated_at"]=activated_at
        df=df.loc[first_index:]; events.append(("ACTIVE",row["id"],row["symbol"],"ENTRY",[]))
    outcome,mfe,mae=evaluate(row,df)
    mfe=max(float(row.get("max_favorable_r") or 0),mfe)
    mae=max(float(row.get("max_adverse_r") or 0),mae)
    if outcome:
        result,price,pnl_r,closed_at=outcome
        close_signal(row["id"],result,price,pnl_r,closed_at,mfe,mae)
        settled=settle_paper_trades(row["id"])
        events.append(("CLOSED",row["id"],row["symbol"],result,settled)); return events
    activated=pd.Timestamp(row.get("activated_at") or row["created_at"])
    activated=activated.tz_localize("UTC") if activated.tzinfo is None else activated.tz_convert("UTC")
    if not df.empty and now-activated>=pd.Timedelta(hours=SIGNAL_MAX_AGE_HOURS):
        price=float(df.iloc[-1].close); entry=float(row["entry"]); risk=abs(entry-float(row["stop"]))
        pnl_r=((price-entry) if row["side"]=="LONG" else (entry-price))/risk
        closed_at=_iso(df.iloc[-1].close_time)
        close_signal(row["id"],"EXPIRED",price,pnl_r,closed_at,mfe,mae)
        settled=settle_paper_trades(row["id"])
        events.append(("CLOSED",row["id"],row["symbol"],"EXPIRED",settled)); return events
    if not df.empty:
        checkpoint(row["id"],_iso(df.iloc[-1].close_time),mfe,mae)
    return events

async def update_outcomes():
    rows=open_signals()
    semaphore=asyncio.Semaphore(5)
    results=await asyncio.gather(*(update_one(r,semaphore) for r in rows),return_exceptions=True)
    return [event for batch in results if isinstance(batch,list) for event in batch]
