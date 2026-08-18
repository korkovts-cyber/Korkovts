"""V11.10 correlation-aware forward-edge selector.

V11.9 used a conservative normal-error lower bound. V11.10 keeps the same
negative-first policy but also resamples whole UTC-day blocks so many signals
from one market move cannot masquerade as many independent observations.

This layer never creates or rescues a trade. It only orders candidates that
already passed Production, execution, evidence, Meta and V11.10 protections.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass, asdict
from typing import Any, Iterable

import numpy as np

from app.config import DATABASE_PATH
from v1171_sqlite import db_session

BASE_FUTURES_RELEASE="11.7.1-futures-evidence"
MIN_HISTORY=20
MIN_POSITIVE_HISTORY=50
MIN_NEGATIVE_DAYS=8
MIN_POSITIVE_DAYS=12
PRIOR_STRENGTH=40.0
NONSTATIONARITY_FLOOR_R=0.08
ONE_SIDED_Z_90=1.645
BOOTSTRAP_SIMS=1400


@dataclass(frozen=True)
class EdgeEstimate:
    n:int
    block_days:int
    mean_r:float|None
    shrunk_mean_r:float|None
    normal_lcb90_r:float|None
    block_p05_r:float|None
    block_p95_r:float|None
    lcb90_r:float|None
    positive_probability:float|None
    profit_factor:float|None
    adjustment:float
    label:str
    cohort:str


def _pf(values:Iterable[float])->float:
    vals=list(values)
    gains=sum(v for v in vals if v>0)
    losses=-sum(v for v in vals if v<0)
    if losses>0:
        return gains/losses
    return 999.0 if gains>0 else 0.0


def _day(value,index:int)->str:
    text=str(value or "")
    if len(text)>=10 and text[4:5]=="-" and text[7:8]=="-":
        return text[:10]
    # Missing event time cannot establish temporal independence. Treat all
    # such rows as one unknown block so they can never unlock positive
    # promotion through fake block diversity.
    return "unknown"


def _normal_stats(vals):
    n=len(vals)
    mean=sum(vals)/n if n else 0.0
    if n>1:
        var=sum((x-mean)**2 for x in vals)/(n-1)
        sd=math.sqrt(max(0.0,var))
    else:
        sd=0.0
    shrink=n/(n+PRIOR_STRENGTH) if n else 0.0
    shrunk=mean*shrink
    se=sd/math.sqrt(n) if n>1 else max(abs(mean),1.0)
    uncertainty=math.sqrt(se*se+NONSTATIONARITY_FLOOR_R**2)
    lcb=shrunk-ONE_SIDED_Z_90*uncertainty
    return mean,shrunk,lcb


def _bootstrap_blocks(rows,cohort:str):
    grouped={}
    vals=[]
    for i,row in enumerate(rows):
        try:
            pnl=float(row.get("pnl_r") if isinstance(row,dict) else row)
        except Exception:
            continue
        if not math.isfinite(pnl):
            continue
        vals.append(pnl)
        stamp=(row.get("event_time") if isinstance(row,dict) else None)
        grouped.setdefault(_day(stamp,i),[]).append(pnl)
    blocks=[np.asarray(v,dtype=float) for _,v in sorted(grouped.items()) if v]
    days=len(blocks)
    if not vals:
        return vals,days,None,None,None
    if days<2:
        return vals,days,None,None,None
    digest=hashlib.sha256(str(cohort).encode("utf-8","replace")).digest()
    seed=int.from_bytes(digest[:8],"big") ^ 11100
    rng=np.random.default_rng(seed)
    means=np.empty(BOOTSTRAP_SIMS,dtype=float)
    for j in range(BOOTSTRAP_SIMS):
        picked=rng.integers(0,days,size=days)
        total=0.0; count=0
        for idx in picked:
            block=blocks[int(idx)]
            total+=float(block.sum()); count+=int(block.size)
        means[j]=total/count if count else 0.0
    return vals,days,float(np.quantile(means,.05)),float(np.quantile(means,.95)),float((means>0).mean())


def _stats(rows,cohort:str)->EdgeEstimate:
    # Backwards-compatible convenience: a list of floats is accepted.
    normalized=[]
    for i,row in enumerate(rows or []):
        if isinstance(row,dict):
            normalized.append(row)
        else:
            normalized.append({"pnl_r":row,"event_time":None})
    vals,days,p05,p95,p_pos=_bootstrap_blocks(normalized,cohort)
    n=len(vals)
    if not vals:
        return EdgeEstimate(0,0,None,None,None,None,None,None,None,None,0.0,"INSUFFICIENT",cohort)
    mean,shrunk,normal_lcb=_normal_stats(vals)
    # Use the more conservative lower bound whenever block resampling exists.
    lcb=min(normal_lcb,p05) if p05 is not None else normal_lcb

    adj=0.0; label="OBSERVE"
    if n>=MIN_HISTORY:
        if lcb<=-.30:
            adj=-3.0; label="WEAK_EDGE"
        elif lcb<=-.15:
            adj=-2.0; label="EDGE_WARNING"
        elif lcb<0:
            adj=-1.0; label="UNCERTAIN_EDGE"
        elif n>=MIN_POSITIVE_HISTORY and days>=MIN_POSITIVE_DAYS and p05 is not None:
            # Positive promotion requires correlation-aware evidence. A lucky
            # cluster of signals from a few market days is not enough.
            if p05>=.30 and (p_pos or 0)>=.95:
                adj=2.0; label="ROBUST_STRONG_EDGE"
            elif p05>=.15 and (p_pos or 0)>=.93:
                adj=1.25; label="ROBUST_CONFIRMED_EDGE"
            elif p05>=.05 and (p_pos or 0)>=.90:
                adj=.50; label="ROBUST_POSITIVE_EDGE"
            else:
                label="POSITIVE_NOT_ROBUST"
        elif n>=MIN_POSITIVE_HISTORY:
            label="POSITIVE_NOT_DIVERSE"
        else:
            label="POSITIVE_NOT_MATURE"
    return EdgeEstimate(
        n,days,mean,shrunk,normal_lcb,p05,p95,lcb,p_pos,_pf(vals),adj,label,cohort
    )


def _all_history(limit:int=3000):
    query="""
        SELECT pnl_r,setup_type,timeframe,side,market_regime,
               COALESCE(closed_at,created_at) event_time
        FROM signals
        WHERE status='CLOSED'
          AND activated_at IS NOT NULL
          AND COALESCE(is_shadow,0)=0
          AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
          AND COALESCE(release_version,'')=?
          AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
          AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
          AND pnl_r IS NOT NULL
        ORDER BY COALESCE(closed_at,created_at) DESC
        LIMIT ?
    """
    try:
        with db_session(DATABASE_PATH,row_factory=sqlite3.Row) as c:
            return [dict(r) for r in c.execute(query,(BASE_FUTURES_RELEASE,int(limit))).fetchall()]
    except Exception:
        return []


def _estimate_from_rows(signal:Any,rows)->EdgeEstimate:
    setup=str(getattr(signal,"setup_type","") or "")
    timeframe=str(getattr(signal,"timeframe","") or "").upper()
    side=str(getattr(signal,"side","") or "").upper()
    market=str(
        (getattr(signal,"market_context",{}) or {}).get("bias")
        or getattr(signal,"production_regime","") or ""
    ).upper()

    def match(**wanted):
        out=[]
        for r in rows:
            good=True
            for key,value in wanted.items():
                actual=str(r.get(key) or "")
                if key in ("timeframe","side","market_regime"):
                    actual=actual.upper(); value=str(value).upper()
                else:
                    value=str(value)
                if actual!=value:
                    good=False; break
            if good: out.append(r)
        return out

    cohorts=[]
    if market:
        cohorts.append((
            f"{setup} · {timeframe} · {side} · {market}",
            match(setup_type=setup,timeframe=timeframe,side=side,market_regime=market),
        ))
    cohorts.extend([
        (f"{setup} · {timeframe} · {side}",match(setup_type=setup,timeframe=timeframe,side=side)),
        (f"{timeframe} · {side}",match(timeframe=timeframe,side=side)),
        ("all delivered comparable Futures",list(rows)),
    ])
    best=None
    for name,cohort_rows in cohorts:
        e=_stats(cohort_rows,name)
        if e.n>=MIN_HISTORY and e.block_days>=MIN_NEGATIVE_DAYS:
            return e
        if best is None or (e.n,e.block_days)>(best.n,best.block_days):
            best=e
    return best or EdgeEstimate(0,0,None,None,None,None,None,None,None,None,0.0,"INSUFFICIENT","no comparable history")


def estimate(signal:Any)->EdgeEstimate:
    return _estimate_from_rows(signal,_all_history())


def _attach(signal,e:EdgeEstimate):
    base=float(getattr(signal,"professional_rank",0) or 0)
    priority=max(0.0,min(99.0,base+float(e.adjustment)))
    signal.decision_priority=priority
    signal.expected_net_r=(None if e.shrunk_mean_r is None else float(e.shrunk_mean_r))
    signal.expected_net_r_lcb=(None if e.lcb90_r is None else float(e.lcb90_r))
    signal.edge_sample_n=int(e.n)
    signal.edge_block_days=int(e.block_days)
    signal.edge_adjustment=float(e.adjustment)
    signal.edge_positive_probability=(None if e.positive_probability is None else float(e.positive_probability))
    signal.feature_snapshot.setdefault("decision_edge_v11100",{}).update({
        **asdict(e),
        "base_professional_rank":base,
        "decision_priority":priority,
        "source_release":BASE_FUTURES_RELEASE,
        "policy":"negative-first; UTC-day block bootstrap; positive>=50 trades & >=12 days",
    })
    return signal


def annotate(signal:Any)->Any:
    return _attach(signal,estimate(signal))


def annotate_many(rows):
    signals=list(rows or [])
    history=_all_history()
    cache={}
    for signal in signals:
        # Cache by the cohort-defining attributes; batch scans often contain
        # multiple signals that share timeframe/side/setup.
        key=(
            str(getattr(signal,"setup_type","") or ""),
            str(getattr(signal,"timeframe","") or "").upper(),
            str(getattr(signal,"side","") or "").upper(),
            str((getattr(signal,"market_context",{}) or {}).get("bias") or getattr(signal,"production_regime","") or "").upper(),
        )
        e=cache.get(key)
        if e is None:
            e=_estimate_from_rows(signal,history); cache[key]=e
        _attach(signal,e)
    return signals


def selection_key(signal:Any):
    priority=float(getattr(signal,"decision_priority",getattr(signal,"professional_rank",0)) or 0)
    lcb=getattr(signal,"expected_net_r_lcb",None)
    lcb=float(lcb) if lcb is not None and math.isfinite(float(lcb)) else -999.0
    block_days=int(getattr(signal,"edge_block_days",0) or 0)
    return (
        priority,
        lcb,
        min(block_days,60),
        float(getattr(signal,"professional_rank",0) or 0),
        float(getattr(signal,"score",0) or 0),
        -float(getattr(signal,"estimated_cost_r",0) or 0),
    )
