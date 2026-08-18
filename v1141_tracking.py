"""Ambiguity-aware tracker patch for V11.7.1.

1m OHLC cannot reveal intrabar event order. If the same candle touches both
entry and invalidation, or both SL and TP2 after activation, the observation is
marked AMBIGUOUS instead of being treated as a clean win/loss.

These rows remain in the audit history but adaptive learning excludes them.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

import app.tracker as core


def evaluate_ambiguous(row,df):
    entry=float(row["entry"]); stop=float(row["stop"])
    risk=abs(entry-stop)
    if risk<=0 or df.empty:
        return None,0,0
    long=row["side"]=="LONG"
    mfe=mae=0.0
    cost_r=core._cost_r(row)

    for _,c in df.iterrows():
        high=float(c.high); low=float(c.low)
        if long:
            mfe=max(mfe,(high-entry)/risk)
            mae=max(mae,(entry-low)/risk)
            sl=low<=stop
            tp=high>=float(row["tp2"])
        else:
            mfe=max(mfe,(entry-low)/risk)
            mae=max(mae,(high-entry)/risk)
            sl=high>=stop
            tp=low<=float(row["tp2"])

        if sl and tp:
            # No inventing event order from an OHLC candle.
            return ("AMBIGUOUS_SL_TP",entry,0.0,core._iso(c.close_time)),mfe,mae
        if sl:
            return ("SL",stop,-1.0-cost_r,core._iso(c.close_time)),mfe,mae
        if tp:
            return ("TP2",float(row["tp2"]),2.0-cost_r,core._iso(c.close_time)),mfe,mae
    return None,mfe,mae


async def update_one_ambiguous(row,semaphore=None,preloaded=None):
    start=core._utc(row.get("last_checked_at") or row["created_at"])
    if preloaded is None:
        async with semaphore:
            df=await core.get_klines_since(
                row["symbol"],"1m",int(start.timestamp()*1000)+1,1500
            )
    else:
        boundary=start+timedelta(milliseconds=1)
        df=preloaded[preloaded.open_time>=boundary] if not preloaded.empty else preloaded

    events=[]
    created=core._utc(row["created_at"])
    now=pd.Timestamp.now(tz="UTC")
    entry_expiry=1 if row.get("timeframe")=="15M" else core.ENTRY_EXPIRY_HOURS
    max_age=4 if row.get("timeframe")=="15M" else core.SIGNAL_MAX_AGE_HOURS

    if row["status"] in ("SENT","WAITING","OPEN"):
        entry_deadline=created+timedelta(hours=entry_expiry)
        waiting_df=core._until(df,entry_deadline)
        entry=float(row["entry"])
        hit=(waiting_df[(waiting_df.low<=entry)&(waiting_df.high>=entry)]
             if not waiting_df.empty else waiting_df)
        invalid=(
            waiting_df[waiting_df.low<=float(row["stop"])]
            if row["side"]=="LONG"
            else waiting_df[waiting_df.high>=float(row["stop"])]
        ) if not waiting_df.empty else waiting_df

        if not invalid.empty and not hit.empty and invalid.index[0]==hit.index[0]:
            idx=hit.index[0]
            closed_at=core._iso(waiting_df.loc[idx].close_time)
            core.close_signal(
                row["id"],"AMBIGUOUS_ENTRY_STOP",entry,0.0,closed_at,0,0
            )
            return [("CLOSED",row["id"],row["symbol"],
                     "AMBIGUOUS_ENTRY_STOP",row.get("source_chat_id"))]

        if not invalid.empty and (hit.empty or invalid.index[0]<hit.index[0]):
            closed_at=core._iso(df.loc[invalid.index[0]].close_time)
            core.close_signal(
                row["id"],"INVALIDATED",float(row["stop"]),0,closed_at,0,0
            )
            return [("CLOSED",row["id"],row["symbol"],
                     "INVALIDATED",row.get("source_chat_id"))]

        if hit.empty:
            if now>=entry_deadline:
                closed_at=entry_deadline.isoformat()
                core.close_signal(row["id"],"ENTRY_EXPIRED",entry,0,closed_at,0,0)
                return [("CLOSED",row["id"],row["symbol"],
                         "ENTRY_EXPIRED",row.get("source_chat_id"))]
            if not waiting_df.empty:
                core.checkpoint(row["id"],core._iso(waiting_df.iloc[-1].close_time),0,0)
            return []

        first_index=hit.index[0]
        activated_at=core._iso(waiting_df.loc[first_index].close_time)
        core.activate_signal(row["id"],activated_at)
        row["status"]="ACTIVE"; row["activated_at"]=activated_at
        df=df.loc[first_index:]
        events.append(("ACTIVE",row["id"],row["symbol"],
                       "ENTRY",row.get("source_chat_id")))

    activated=core._utc(row.get("activated_at") or row["created_at"])
    trade_deadline=activated+timedelta(hours=max_age)
    evaluation_df=core._until(df,trade_deadline)

    outcome,mfe,mae=evaluate_ambiguous(row,evaluation_df)
    mfe=max(float(row.get("max_favorable_r") or 0),mfe)
    mae=max(float(row.get("max_adverse_r") or 0),mae)

    if outcome:
        result,price,pnl_r,closed_at=outcome
        core.close_signal(row["id"],result,price,pnl_r,closed_at,mfe,mae)
        events.append(("CLOSED",row["id"],row["symbol"],
                       result,row.get("source_chat_id")))
        return events

    if now>=trade_deadline:
        if not evaluation_df.empty:
            price=float(evaluation_df.iloc[-1].close)
        elif not df.empty:
            price=float(df.iloc[0].open)
        else:
            price=float(row["entry"])
        entry=float(row["entry"]); risk=abs(entry-float(row["stop"]))
        pnl_r=((price-entry) if row["side"]=="LONG" else (entry-price))/risk-core._cost_r(row)
        closed_at=trade_deadline.isoformat()
        core.close_signal(row["id"],"EXPIRED",price,pnl_r,closed_at,mfe,mae)
        events.append(("CLOSED",row["id"],row["symbol"],
                       "EXPIRED",row.get("source_chat_id")))
        return events

    if not evaluation_df.empty:
        core.checkpoint(
            row["id"],core._iso(evaluation_df.iloc[-1].close_time),mfe,mae
        )
    return events


def install():
    core.evaluate=evaluate_ambiguous
    core.update_one=update_one_ambiguous
