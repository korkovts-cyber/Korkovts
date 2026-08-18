"""Empirical confidence calibration for V11.7.1.

This module deliberately refuses to invent a win probability from a strategy
score. It reports a percentage only when a sufficiently large set of delivered,
resolved forward trades exists for a comparable cohort.
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app.config import DATABASE_PATH

FUTURES_RELEASE_KEY="11.7.1-futures-evidence"
SPOT_RELEASE_KEYS=("11.8.1-market-intelligence",)
MIN_PROVISIONAL=30
MIN_CALIBRATED=50
MAX_CI_WIDTH=0.32


@contextmanager
def _db():
    c=sqlite3.connect(DATABASE_PATH,timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    c.row_factory=sqlite3.Row
    try:
        yield c
    finally:
        c.close()


@dataclass(frozen=True)
class Estimate:
    available:bool
    probability:float|None
    lower:float|None
    upper:float|None
    n:int
    wins:int
    label:str
    cohort:str
    expectancy:float|None=None
    profit_factor:float|None=None

    @property
    def interval_width(self):
        if self.lower is None or self.upper is None:
            return None
        return float(self.upper-self.lower)


def wilson_interval(wins:int,n:int,z:float=1.96):
    n=int(n); wins=int(wins)
    if n<=0:
        return None,None
    p=wins/n
    denom=1+z*z/n
    centre=(p+z*z/(2*n))/denom
    margin=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/denom
    return max(0.0,centre-margin),min(1.0,centre+margin)


def _profit_factor(values):
    gains=sum(v for v in values if v>0)
    losses=-sum(v for v in values if v<0)
    if losses>0:
        return gains/losses
    return 999.0 if gains>0 else 0.0


def _estimate_from_values(values,cohort):
    vals=[float(v) for v in values if v is not None and math.isfinite(float(v))]
    n=len(vals); wins=sum(1 for v in vals if v>0)
    lo,hi=wilson_interval(wins,n)
    if n<MIN_PROVISIONAL:
        return Estimate(False,None,None,None,n,wins,"INSUFFICIENT",cohort,
                        (sum(vals)/n if n else None),(_profit_factor(vals) if n else None))
    label="CALIBRATED" if n>=MIN_CALIBRATED and lo is not None and hi is not None and hi-lo<=MAX_CI_WIDTH else "PROVISIONAL"
    # Use the Wilson interval centre as a shrinkage point estimate instead of
    # raw wins/n. Even 30/30 must never render as "100% probability".
    point=(lo+hi)/2 if lo is not None and hi is not None else wins/n
    return Estimate(True,point,lo,hi,n,wins,label,cohort,
                    sum(vals)/n,_profit_factor(vals))


def _futures_rows(signal:Any):
    setup=str(getattr(signal,"setup_type","") or "")
    timeframe=str(getattr(signal,"timeframe","") or "").upper()
    side=str(getattr(signal,"side","") or "").upper()
    market=str((getattr(signal,"market_context",{}) or {}).get("bias") or getattr(signal,"production_regime","") or "").upper()
    base="""
        SELECT pnl_r,setup_type,timeframe,side,market_regime
        FROM signals
        WHERE status='CLOSED'
          AND activated_at IS NOT NULL
          AND COALESCE(is_shadow,0)=0
          AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
          AND COALESCE(release_version,'')=?
          AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
          AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
          AND pnl_r IS NOT NULL
    """
    with _db() as c:
        rows=[dict(r) for r in c.execute(base,(FUTURES_RELEASE_KEY,)).fetchall()]
    return rows,setup,timeframe,side,market


def futures(signal:Any):
    try:
        rows,setup,timeframe,side,market=_futures_rows(signal)
    except Exception:
        return Estimate(False,None,None,None,0,0,"UNAVAILABLE","Futures DB unavailable")

    cohorts=[
        (f"{setup} · {timeframe} · {side} · {market}",
         [r["pnl_r"] for r in rows if str(r.get("setup_type") or "")==setup
          and str(r.get("timeframe") or "").upper()==timeframe
          and str(r.get("side") or "").upper()==side
          and (not market or str(r.get("market_regime") or "").upper()==market)]),
        (f"{setup} · {timeframe} · {side}",
         [r["pnl_r"] for r in rows if str(r.get("setup_type") or "")==setup
          and str(r.get("timeframe") or "").upper()==timeframe
          and str(r.get("side") or "").upper()==side]),
        (f"{timeframe} · {side}",
         [r["pnl_r"] for r in rows if str(r.get("timeframe") or "").upper()==timeframe
          and str(r.get("side") or "").upper()==side]),
        ("all delivered V11.7.1 Futures",
         [r["pnl_r"] for r in rows]),
    ]
    best=None
    for cohort,vals in cohorts:
        est=_estimate_from_values(vals,cohort)
        if est.available:
            return est
        if best is None or est.n>best.n:
            best=est
    return best or Estimate(False,None,None,None,0,0,"INSUFFICIENT","no resolved Futures cohort")


def _spot_success_value(row):
    """Comparable Spot success = first TP1 before invalidation and within 7 days."""
    if row.get("return_7d") is None:
        return None

    def _ts(value):
        if not value:
            return None
        try:
            from datetime import datetime, timezone
            dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    delivered=_ts(row.get("delivered_at"))
    tp1_at=_ts(row.get("first_tp1_at"))
    invalid_at=_ts(row.get("first_invalidation_at"))
    if delivered is None:
        return None

    horizon=delivered.timestamp()+7*86400
    # Same observed bar = chronology unknowable; exclude rather than guess.
    if tp1_at is not None and invalid_at is not None and tp1_at==invalid_at:
        return None
    if tp1_at is None or tp1_at.timestamp()>horizon:
        return -1.0
    if invalid_at is not None and invalid_at<=tp1_at:
        return -1.0
    return 1.0


def spot(signal:Any):
    setup=str(getattr(signal,"setup_type","") or "")
    regime=str(getattr(signal,"market_regime","") or "").upper()
    try:
        marks=','.join('?' for _ in SPOT_RELEASE_KEYS)
        query=f"""
            SELECT delivered_at,return_7d,tp1_hit,invalidated,result,
                   first_tp1_at,first_invalidation_at,setup_type,market_regime
            FROM spot_signals
            WHERE delivered_at IS NOT NULL
              AND signal_status='BUY'
              AND return_7d IS NOT NULL
              AND COALESCE(delivery_uncertain,0)=0
              AND release_version IN ({marks})
        """
        with _db() as c:
            rows=[dict(r) for r in c.execute(query,SPOT_RELEASE_KEYS).fetchall()]
    except Exception:
        return Estimate(False,None,None,None,0,0,"UNAVAILABLE","Spot DB unavailable")

    cohorts=[
        (f"{setup} · {regime}",[
            _spot_success_value(r) for r in rows
            if str(r.get("setup_type") or "")==setup and str(r.get("market_regime") or "").upper()==regime
        ]),
        (f"{setup}",[_spot_success_value(r) for r in rows if str(r.get("setup_type") or "")==setup]),
        ("all delivered V11.7.1 Spot · TP1-before-stop",[_spot_success_value(r) for r in rows]),
    ]
    best=None
    for cohort,vals in cohorts:
        est=_estimate_from_values([v for v in vals if v is not None],cohort)
        if est.available:
            return est
        if best is None or est.n>best.n:
            best=est
    return best or Estimate(False,None,None,None,0,0,"INSUFFICIENT","no resolved Spot cohort")


def short_text(est:Estimate):
    if not est.available or est.probability is None:
        return f"н/д · forward выборка {est.n}/{MIN_PROVISIONAL}"
    label="калибр." if est.label=="CALIBRATED" else "предв."
    scope=str(est.cohort or "общая база")
    if len(scope)>34:
        scope=scope[:31]+"…"
    return (
        f"{est.probability*100:.0f}% {label} · {est.wins}/{est.n} успехов · "
        f"95% CI {est.lower*100:.0f}–{est.upper*100:.0f}% · база: {scope}"
    )
