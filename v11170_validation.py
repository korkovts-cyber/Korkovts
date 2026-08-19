"""V11.17 research integrity guards: lookahead and recursive stability."""
from __future__ import annotations
from dataclasses import dataclass,asdict
import math
import pandas as pd

SCHEMA="11.17-validation-guards-v2"
@dataclass(frozen=True)
class ValidationDecision:
    eligible:bool; label:str; reason:str; delta:float|None=None
    def as_dict(self): return asdict(self)


def no_future_rows(frame,decision_ts=None):
    if frame is None or not hasattr(frame,"columns") or not len(frame):
        return ValidationDecision(False,"BLOCK","frame unavailable")
    col="close_time" if "close_time" in frame.columns else ("open_time" if "open_time" in frame.columns else None)
    if col is None:
        return ValidationDecision(False,"BLOCK","timestamp column unavailable")
    try:
        ts=pd.to_datetime(frame[col],utc=True,errors="coerce")
        if ts.isna().any(): return ValidationDecision(False,"BLOCK","invalid timestamps")
        if not ts.is_monotonic_increasing: return ValidationDecision(False,"BLOCK","timestamps not monotonic")
        if ts.duplicated().any(): return ValidationDecision(False,"BLOCK","duplicate timestamps")
        if decision_ts is not None:
            d=pd.Timestamp(decision_ts,unit="s",tz="UTC") if isinstance(decision_ts,(int,float)) else pd.Timestamp(decision_ts)
            if d.tzinfo is None: d=d.tz_localize("UTC")
            else: d=d.tz_convert("UTC")
            if (ts>d).any(): return ValidationDecision(False,"BLOCK","future row detected")
        return ValidationDecision(True,"PASS","no future rows")
    except Exception as exc:
        return ValidationDecision(False,"BLOCK",f"timestamp validation failed: {type(exc).__name__}")


def recursive_stability(indicator_fn,frame,windows=(80,120,180),tolerance=1e-6):
    vals=[]
    try:
        for n in windows:
            if len(frame)<n: continue
            v=float(indicator_fn(frame.tail(n)))
            if not math.isfinite(v): return ValidationDecision(False,"BLOCK","non-finite recursive indicator")
            vals.append(v)
        if len(vals)<2: return ValidationDecision(True,"LEARNING","insufficient windows")
        scale=max(1.0,max(abs(v) for v in vals)); delta=(max(vals)-min(vals))/scale
        if delta>float(tolerance): return ValidationDecision(False,"BLOCK","indicator changes with startup window",delta)
        return ValidationDecision(True,"PASS","recursive indicator stable",delta)
    except Exception as exc:
        return ValidationDecision(False,"BLOCK",f"recursive validation failed: {type(exc).__name__}")
