"""V11.10 market-data coherence contract.

Inspired by production trading frameworks that centralize market data before a
strategy consumes it.  Korkovts still keeps the existing exchange adapters, but
this module adds a causal/freshness contract at the decision boundary:

- only already-closed candles may be used;
- the newest closed candle may not be materially stale;
- recent candle history may not contain large holes;
- future timestamps are rejected;
- the check is deterministic and stores diagnostics instead of inventing data.

Missing timestamp metadata is reported as UNOBSERVABLE instead of being guessed.
The live Binance adapters always provide ``open_time``; this fail-open branch is
kept only for legacy/unit-test compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
import time
from typing import Any


INTERVAL_SECONDS={
    "1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,
    "1h":3600,"2h":7200,"4h":14400,"6h":21600,"8h":28800,
    "12h":43200,"1d":86400,
}

# analysis timeframe -> (lower, base, higher)
PROFILE={
    "15M":("5m","15m","1h"),
    "1H":("15m","1h","4h"),
}


@dataclass(frozen=True)
class FrameStatus:
    role:str
    interval:str
    observable:bool
    rows:int
    latest_close_epoch:float|None
    age_sec:float|None
    max_age_sec:float
    future:bool
    gap:bool
    max_gap_sec:float|None
    ok:bool
    reason:str


@dataclass(frozen=True)
class SnapshotStatus:
    eligible:bool
    observable:bool
    status:str
    reason:str
    frames:tuple[FrameStatus,...]

    def as_dict(self):
        return {
            "eligible":self.eligible,
            "observable":self.observable,
            "status":self.status,
            "reason":self.reason,
            "frames":[asdict(x) for x in self.frames],
        }


def _epoch(value:Any)->float|None:
    if value is None:
        return None
    try:
        # pandas.Timestamp and datetime both expose timestamp().
        ts=value.timestamp()
        if math.isfinite(float(ts)):
            return float(ts)
    except Exception:
        pass
    try:
        text=str(value).replace("Z","+00:00")
        dt=datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return float(dt.timestamp())
    except Exception:
        return None


def _open_times(frame)->list[float]:
    if frame is None or not hasattr(frame,"columns") or "open_time" not in frame.columns:
        return []
    values=[]
    try:
        tail=frame["open_time"].tail(16).tolist()
    except Exception:
        return []
    for value in tail:
        ts=_epoch(value)
        if ts is not None:
            values.append(ts)
    return values


def validate_frame(frame,interval:str,role:str="frame",now:float|None=None)->FrameStatus:
    interval=str(interval)
    seconds=float(INTERVAL_SECONDS.get(interval,0) or 0)
    now=float(time.time() if now is None else now)
    rows=int(len(frame)) if frame is not None and hasattr(frame,"__len__") else 0
    # Latest closed candle can naturally be almost one full interval old.  A
    # 35% grace plus two minutes tolerates scheduling/network jitter without
    # silently accepting a missing full candle.
    max_age=seconds*1.35+120.0 if seconds>0 else 0.0
    opens=_open_times(frame)
    if seconds<=0:
        return FrameStatus(role,interval,False,rows,None,None,max_age,False,False,None,
                           True,"unknown interval; not enforced")
    if not opens:
        return FrameStatus(role,interval,False,rows,None,None,max_age,False,False,None,
                           True,"open_time unavailable; legacy compatibility")

    latest_close=opens[-1]+seconds
    age=now-latest_close
    future=age < -90.0
    non_monotonic=any(b<=a for a,b in zip(opens,opens[1:]))
    gaps=[]
    for a,b in zip(opens,opens[1:]):
        delta=b-a
        if math.isfinite(delta):
            gaps.append(delta)
    max_gap=max(gaps) if gaps else None
    # Binance intervals are exact.  Allow 50% extra to avoid false alarms from
    # small timestamp conversions while still catching one missing candle.
    gap=bool(max_gap is not None and max_gap>seconds*1.50+2.0)
    stale=age>max_age
    ok=not future and not gap and not stale and not non_monotonic
    reasons=[]
    if future:
        reasons.append(f"{role} candle closes in the future")
    if stale:
        reasons.append(f"{role} {interval} stale {age/60:.1f}m")
    if gap:
        reasons.append(f"{role} {interval} history gap {max_gap/seconds:.1f}x")
    if non_monotonic:
        reasons.append(f"{role} {interval} timestamps duplicate/non-monotonic")
    return FrameStatus(
        role,interval,True,rows,latest_close,age,max_age,future,gap,max_gap,ok,
        "; ".join(reasons) if reasons else "fresh contiguous closed candles",
    )


def validate_snapshot(timeframe:str,lower,base,higher,now:float|None=None)->SnapshotStatus:
    tf=str(timeframe or "").upper()
    intervals=PROFILE.get(tf)
    if intervals is None:
        return SnapshotStatus(True,False,"UNOBSERVABLE",f"unsupported timeframe {tf}",tuple())
    frames=(
        validate_frame(lower,intervals[0],"lower",now),
        validate_frame(base,intervals[1],"base",now),
        validate_frame(higher,intervals[2],"higher",now),
    )
    observable=all(x.observable for x in frames)
    bad=[x.reason for x in frames if x.observable and not x.ok]
    missing=[x.role for x in frames if not x.observable]
    if bad:
        return SnapshotStatus(False,observable,"STALE_OR_GAPPED","; ".join(bad),frames)
    if not observable:
        return SnapshotStatus(True,False,"UNOBSERVABLE","timestamp metadata unavailable for: "+", ".join(missing),frames)
    return SnapshotStatus(True,True,"GOOD","causal candle snapshot coherent",frames)


def validate_spot_frame(frame,interval:str,role:str="spot",now:float|None=None)->FrameStatus:
    """Spot helper; Spot frames retain close_time but open_time is enough."""
    return validate_frame(frame,interval,role,now)
