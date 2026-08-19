"""V11.15.1 Indicator Edge Pack.

Quality-first, negative-only confirmation layer for already-qualified Futures
signals.  It does NOT add to professional_rank and deliberately groups
correlated measurements so they cannot manufacture fake independent evidence.

Families:
- location: anchored VWAP + volume-profile POC
- flow: closed-candle CVD + live aggTrade aggressor pulse
- participation: RVOL
- positioning: price/OI matrix (corroboration only; overlaps derivatives family)
- sweep safety: adverse liquidity sweep veto / clean structure confirmation
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

import numpy as np
import pandas as pd

SCHEMA="11.15.1-indicator-edge-v1"

@dataclass(frozen=True)
class IndicatorEdge:
    available: bool
    score: float
    support: int
    conflicts: int
    auto_eligible: bool
    prime_eligible: bool
    avwap: float | None
    poc: float | None
    rvol: float | None
    cvd10: float | None
    agg_imbalance: float | None
    oi_matrix: str
    sweep: str
    reasons: tuple[str,...]
    blockers: tuple[str,...]


def _f(v, default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _frame(df):
    if df is None or len(df)<40:
        return None
    x=df.copy()
    for c in ("open","high","low","close","volume"):
        if c not in x:
            return None
        x[c]=pd.to_numeric(x[c],errors="coerce")
    if "taker_buy_base" in x:
        x["taker_buy_base"]=pd.to_numeric(x["taker_buy_base"],errors="coerce")
    x=x.replace([np.inf,-np.inf],np.nan).dropna(subset=["open","high","low","close","volume"])
    return x if len(x)>=40 else None


def _atr(x, n=14):
    prev=x.close.shift(1)
    tr=pd.concat([(x.high-x.low).abs(),(x.high-prev).abs(),(x.low-prev).abs()],axis=1).max(axis=1)
    return _f(tr.tail(n).mean(),0.0)


def _anchored_vwap(x, side):
    tail=x.tail(80).copy()
    if len(tail)<30:
        return None
    anchor=int(np.nanargmin(tail.low.to_numpy())) if side=="LONG" else int(np.nanargmax(tail.high.to_numpy()))
    y=tail.iloc[anchor:]
    vol=y.volume.clip(lower=0)
    total=_f(vol.sum(),0)
    if total<=0:
        return None
    typical=(y.high+y.low+y.close)/3.0
    return _f((typical*vol).sum()/total, float(y.close.iloc[-1]))


def _volume_profile_poc(x, bins=24):
    y=x.tail(120)
    prices=((y.high+y.low+y.close)/3.0).to_numpy(dtype=float)
    vols=y.volume.clip(lower=0).to_numpy(dtype=float)
    lo=float(np.nanmin(y.low)); hi=float(np.nanmax(y.high))
    if not math.isfinite(lo) or not math.isfinite(hi) or hi<=lo or vols.sum()<=0:
        return None
    edges=np.linspace(lo,hi,int(bins)+1)
    hist,_=np.histogram(prices,bins=edges,weights=vols)
    idx=int(np.argmax(hist))
    return float((edges[idx]+edges[idx+1])/2.0)


def _rvol(x):
    if len(x)<22:
        return None
    base=float(x.volume.iloc[-21:-1].median())
    return None if base<=0 else _f(float(x.volume.iloc[-1])/base)


def _cvd10(x):
    if "taker_buy_base" not in x or len(x)<10:
        return None
    y=x.tail(10)
    total=_f(y.volume.sum(),0)
    if total<=0:
        return None
    delta=(2*y.taker_buy_base.fillna(y.volume/2)-y.volume).sum()
    return _f(delta/total)


def _sweep(x, side):
    if len(x)<22:
        return "UNKNOWN"
    last=x.iloc[-1]; prev=x.iloc[-21:-1]
    prior_hi=float(prev.high.max()); prior_lo=float(prev.low.min())
    atr=max(_atr(x),abs(float(last.close))*1e-6)
    # Require a meaningful wick excursion and a close back through the prior
    # level. Ordinary trend progression must not be mislabeled as a sweep.
    bull_sweep=(float(last.low)<prior_lo-.50*atr and float(last.close)>prior_lo+.15*atr)
    bear_sweep=(float(last.high)>prior_hi+.50*atr and float(last.close)<prior_hi-.15*atr)
    if side=="LONG":
        if bear_sweep: return "ADVERSE_HIGH_SWEEP"
        if bull_sweep: return "FAVORABLE_LOW_SWEEP"
    else:
        if bull_sweep: return "ADVERSE_LOW_SWEEP"
        if bear_sweep: return "FAVORABLE_HIGH_SWEEP"
    return "CLEAN"


def _oi_matrix(side, snap):
    d=dict((snap or {}).get("derivatives") or {})
    oi=_f(d.get("oi_change_pct")); price=_f(d.get("price_change_pct"))
    if oi<.2:
        return "LOW_OI_CHANGE"
    aligned=(price>=.15) if side=="LONG" else (price<=-.15)
    opposite=(price<=-.40) if side=="LONG" else (price>=.40)
    if aligned: return "NEW_POSITION_CONFIRMATION"
    if oi>=.8 and opposite: return "ADVERSE_POSITION_BUILD"
    return "MIXED"


def evaluate(frame, side, feature_snapshot=None, agg_imbalance=None):
    side=str(side or "").upper()
    x=_frame(frame)
    if x is None or side not in {"LONG","SHORT"}:
        return IndicatorEdge(False,0,0,0,False,False,None,None,None,None,None,
                             "UNAVAILABLE","UNKNOWN",(),("indicator data unavailable",))
    price=float(x.close.iloc[-1]); atr=max(_atr(x),abs(price)*1e-6)
    avwap=_anchored_vwap(x,side); poc=_volume_profile_poc(x); rvol=_rvol(x); cvd=_cvd10(x)
    agg=None if agg_imbalance is None else _f(agg_imbalance)
    matrix=_oi_matrix(side,feature_snapshot or {})
    sweep=_sweep(x,side)
    long=side=="LONG"
    reasons=[]; blockers=[]; support=0; conflicts=0; score=50.0

    # LOCATION FAMILY: AVWAP + profile are one family, never two votes.
    av_ok=(price>=avwap) if (avwap is not None and long) else ((price<=avwap) if avwap is not None else False)
    poc_ok=(price>=poc) if (poc is not None and long) else ((price<=poc) if poc is not None else False)
    if av_ok and poc_ok:
        support+=1; score+=12; reasons.append("location: AVWAP + POC aligned")
    elif avwap is not None and ((long and price<avwap-.35*atr) or ((not long) and price>avwap+.35*atr)):
        conflicts+=1; score-=18; blockers.append("price materially wrong side of anchored VWAP")
    else:
        reasons.append("location: mixed/near value")

    # FLOW FAMILY: closed CVD + live aggressor pulse are one vote.
    cvd_ok=(cvd is not None and (cvd>=.03 if long else cvd<=-.03))
    agg_ok=(agg is not None and (agg>=.03 if long else agg<=-.03))
    agg_bad=(agg is not None and (agg<=-.15 if long else agg>=.15))
    cvd_bad=(cvd is not None and (cvd<=-.10 if long else cvd>=.10))
    if cvd_ok and agg_ok:
        support+=1; score+=14; reasons.append("flow: CVD + live aggressors aligned")
    elif agg_bad or cvd_bad:
        conflicts+=1; score-=22; blockers.append("CVD/aggressor flow opposes entry")
    else:
        reasons.append("flow: not independently confirmed")

    # PARTICIPATION FAMILY.
    if rvol is not None and rvol>=1.15:
        support+=1; score+=10; reasons.append(f"participation: RVOL {rvol:.2f}x")
    elif rvol is not None and rvol<.65:
        conflicts+=1; score-=12; blockers.append(f"weak participation RVOL {rvol:.2f}x")
    else:
        reasons.append(f"participation: RVOL {rvol:.2f}x" if rvol is not None else "participation unavailable")

    # POSITIONING is corroboration and cannot count as a new independent family
    # in the global evidence audit; here it only tightens this pack.
    if matrix=="NEW_POSITION_CONFIRMATION":
        support+=1; score+=8; reasons.append("positioning: price/OI confirms new positions")
    elif matrix=="ADVERSE_POSITION_BUILD":
        conflicts+=1; score-=20; blockers.append("price/OI shows adverse position build")
    else:
        reasons.append(f"positioning: {matrix.lower()}")

    # SWEEP SAFETY FAMILY. Clean structure counts as safety confirmation; an
    # adverse sweep is terminal for quality-first AUTO.
    if sweep.startswith("ADVERSE"):
        conflicts+=1; score-=24; blockers.append(f"adverse liquidity sweep: {sweep}")
    else:
        support+=1; score+=8
        reasons.append("liquidity sweep favorable" if sweep.startswith("FAVORABLE") else "liquidity structure clean")

    score=max(0.0,min(100.0,score))
    # Quality-first policy: no terminal blocker, >=3/5 pack confirmations for
    # AUTO; PRIME requires 4/5 and stronger total quality.
    auto=(not blockers and support>=3 and conflicts==0 and score>=68)
    prime=(auto and support>=4 and score>=80 and rvol is not None and rvol>=.90)
    return IndicatorEdge(True,round(score,2),support,conflicts,auto,prime,
                         None if avwap is None else round(avwap,10),
                         None if poc is None else round(poc,10),
                         None if rvol is None else round(rvol,4),
                         None if cvd is None else round(cvd,4),
                         None if agg is None else round(agg,4),
                         matrix,sweep,tuple(reasons),tuple(blockers))


def annotate(signal:Any, frame, agg_imbalance=None):
    result=evaluate(frame,getattr(signal,"side",None),getattr(signal,"feature_snapshot",{}) or {},agg_imbalance)
    signal.indicator_edge_score=result.score
    signal.indicator_edge_support=result.support
    signal.indicator_edge_auto=result.auto_eligible
    signal.indicator_edge_prime=result.prime_eligible
    signal.feature_snapshot.setdefault("indicator_edge_v11151",{}).update({
        "schema":SCHEMA,**asdict(result),
        "negative_only":True,"professional_rank_changed":False,
        "anti_duplication":"AVWAP+POC=location; CVD+agg=flow; OI corroborates positioning",
    })
    return signal
