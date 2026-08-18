"""Forward outcome journal for Spot BUY signals."""
from __future__ import annotations

from datetime import datetime,timezone
import asyncio
import logging
import pandas as pd

from spot_db import open_rows,update_metrics
from spot_market import klines,klines_range

log=logging.getLogger(__name__)


def _ret(price,entry):
    return (float(price)/float(entry)-1)*100 if entry else 0.0


async def update_one(row):
    frame=await klines(row["symbol"],"1h",1000)
    if frame is None or frame.empty:
        return False
    anchor=pd.Timestamp(row.get("delivered_at") or row["created_at"])
    if anchor.tzinfo is None: anchor=anchor.tz_localize("UTC")
    else: anchor=anchor.tz_convert("UTC")

    # Full 1H bars are safe only from the first hourly boundary after delivery.
    # The delivery hour itself is reconstructed from closed 1m bars whose open
    # is after the signal, so pre-signal highs/lows can never create fake TP/MFE.
    full_start=anchor.ceil("h")
    if anchor==anchor.floor("h"):
        full_start=anchor
    data=frame[frame["open_time"]>=full_start].copy()
    partial=None
    partial_done=bool(row.get("partial_hour_processed"))
    now=pd.Timestamp.now(tz="UTC")
    if not partial_done and full_start>anchor:
        end_ms=int(full_start.timestamp()*1000)-1
        partial=await klines_range(
            row["symbol"],"1m",int(anchor.timestamp()*1000),end_ms,1000
        )
        if partial is not None and not partial.empty:
            partial=partial[partial["open_time"]>=anchor].copy()
    if data.empty and (partial is None or partial.empty):
        return False

    entry=float(row["entry_price"]); invalid=float(row["invalidation"])
    tp1=float(row["tp1"]); tp2=float(row["tp2"]); tp3=float(row["tp3"])
    metrics={}
    if not partial_done and full_start<=now:
        metrics["partial_hour_processed"]=1

    # Lifecycle metrics stop at the first terminal event. This prevents later
    # post-close price action from inflating MFE/MAE or changing the trade result.
    # If invalidation and a TP are touched inside the same 1H/1m bar, ordering is
    # unknowable and is recorded explicitly as ambiguous rather than guessed.
    if str(row.get("state") or "OPEN")!="CLOSED":
        parts=[]
        if partial is not None and not partial.empty:
            parts.append(partial[["open_time","close_time","high","low"]])
        if not data.empty:
            parts.append(data[["open_time","close_time","high","low"]])
        bars=(pd.concat(parts,ignore_index=True)
              .drop_duplicates(subset=["open_time"],keep="last")
              .sort_values("open_time")) if parts else pd.DataFrame()

        max_high=entry; min_low=entry
        hit1=bool(row.get("tp1_hit")); hit2=bool(row.get("tp2_hit")); hit3=bool(row.get("tp3_hit"))
        first_tp1_at=row.get("first_tp1_at")
        first_invalidation_at=row.get("first_invalidation_at")
        terminal=None; terminal_at=None
        for bar in bars.itertuples(index=False):
            high=float(bar.high); low=float(bar.low)
            max_high=max(max_high,high); min_low=min(min_low,low)
            bar1=high>=tp1; bar2=high>=tp2; bar3=high>=tp3; inv=low<=invalid
            bar_at=pd.Timestamp(bar.close_time).isoformat()
            if bar1 and not first_tp1_at:
                first_tp1_at=bar_at
            if inv and not first_invalidation_at:
                first_invalidation_at=bar_at
            # Ambiguous only when the ordering is genuinely unknowable.
            # If TP1 was already reached on an earlier bar, a later bar spanning
            # TP1+stop is NOT ambiguous. TP3+stop in the same new bar remains
            # ambiguous because the terminal ordering is unknown.
            if inv and (bar3 or (bar1 and not hit1)):
                terminal="AMBIGUOUS_INVALIDATION_TP"
                terminal_at=bar_at
                break
            hit1=hit1 or bar1; hit2=hit2 or bar2; hit3=hit3 or bar3
            if inv:
                terminal="INVALIDATED"
                terminal_at=bar_at
                break
            if bar3:
                terminal="TP3"
                terminal_at=pd.Timestamp(bar.close_time).isoformat()
                break

        metrics.update(
            max_favorable_pct=max(float(row.get("max_favorable_pct") or 0),_ret(max_high,entry)),
            max_adverse_pct=min(float(row.get("max_adverse_pct") or 0),_ret(min_low,entry)),
            tp1_hit=int(hit1),tp2_hit=int(hit2),tp3_hit=int(hit3),
            invalidated=int(bool(row.get("invalidated"))),
            first_tp1_at=first_tp1_at,
            first_invalidation_at=first_invalidation_at,
        )
        if terminal=="INVALIDATED":
            metrics.update(
                invalidated=1,state="CLOSED",closed_at=terminal_at,
                result="INVALIDATED"
            )
        elif terminal=="TP3":
            metrics.update(state="CLOSED",closed_at=terminal_at,result="TP3")
        elif terminal=="AMBIGUOUS_INVALIDATION_TP":
            metrics.update(
                invalidated=0,state="CLOSED",closed_at=terminal_at,
                result="AMBIGUOUS_INVALIDATION_TP"
            )

    # Fixed-horizon forward returns remain useful even after a trading terminal
    # event, so closed signals continue to receive 3D/5D/7D/10D observations.
    for days,col in ((3,"return_3d"),(5,"return_5d"),(7,"return_7d"),(10,"return_10d")):
        if row.get(col) is not None:
            continue
        target=anchor+pd.Timedelta(days=days)
        after=data[data["close_time"]>=target]
        if not after.empty:
            metrics[col]=_ret(float(after.iloc[0].close),entry)

    age=(now-anchor).total_seconds()/86400
    current_state=str(metrics.get("state",row.get("state") or "OPEN"))
    if current_state!="CLOSED" and age>=10 and (
        metrics.get("return_10d") is not None or row.get("return_10d") is not None
    ):
        ret10=metrics.get("return_10d",row.get("return_10d"))
        metrics.update(
            state="CLOSED",closed_at=datetime.now(timezone.utc).isoformat(),
            result="POSITIVE_10D" if float(ret10)>0 else "NEGATIVE_10D"
        )
    update_metrics(row["id"],**metrics)
    return True


async def update_all(limit=30):
    rows=open_rows(limit); updated=0; errors=[]
    for row in rows:
        try:
            updated+=1 if await update_one(row) else 0
        except Exception as exc:
            errors.append(f"{row.get('symbol','?')}: {type(exc).__name__}: {exc}")
            log.warning("Spot tracker failed %s: %s",row.get("symbol"),exc)
    if rows and len(errors)>=max(2,(len(rows)+1)//2):
        raise RuntimeError(
            f"Spot tracker data degraded for {len(errors)}/{len(rows)} signals: "
            +"; ".join(errors[:3])
        )
    return updated
