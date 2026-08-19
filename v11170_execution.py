"""V11.17 execution reality + trigger half-life, hardened in V11.17.1."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math, time
from typing import Any

SCHEMA="11.17-execution-reality-v2"
DEFAULT_NOTIONALS=(500.0,1000.0,5000.0)
HARD_TTL_SEC=60.0
MAX_FUTURE_TRIGGER_SKEW_SEC=2.0


def _f(v,d=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else float(d)
    except Exception: return float(d)


def trigger_freshness(age_sec:float):
    age=max(0.0,_f(age_sec))
    if age<=15: return 1.0
    if age<=30: return 1.0-(age-15)*(0.15/15)
    if age<=45: return .85-(age-30)*(0.20/15)
    if age<=60: return .65-(age-45)*(0.25/15)
    return 0.0


def _normalized_rows(side,book):
    rows=list((book or {}).get("asks" if str(side).upper()=="LONG" else "bids") or [])
    out=[]
    for row in rows:
        if not isinstance(row,(list,tuple)) or len(row)<2: continue
        p=_f(row[0]); q=_f(row[1])
        if p>0 and q>0: out.append((p,q))
    if str(side).upper()=="LONG":
        if any(out[i][0]>out[i+1][0] for i in range(len(out)-1)): return []
    else:
        if any(out[i][0]<out[i+1][0] for i in range(len(out)-1)): return []
    return out


def _impact(side,book,notional):
    rows=_normalized_rows(side,book)
    if not rows: return None
    target=max(1.0,_f(notional)); remain=target; cost=0.0; qty=0.0
    best=_f(rows[0][0])
    for p,q in rows:
        level_notional=p*q; take=min(remain,level_notional)
        if take<=0: continue
        cost+=take; qty+=take/p; remain-=take
        if remain<=1e-9: break
    if remain>max(0.01,target*.005) or qty<=0 or best<=0: return None
    avg=cost/qty
    adverse=(avg-best)/best*10000 if str(side).upper()=="LONG" else (best-avg)/best*10000
    return max(0.0,adverse)

@dataclass(frozen=True)
class ExecutionDecision:
    eligible: bool
    score: float
    trigger_age_sec: float
    freshness: float
    spread_bps: float
    impact_500_bps: float|None
    impact_1000_bps: float|None
    impact_5000_bps: float|None
    stability_score: float
    reasons: tuple[str,...]
    blockers: tuple[str,...]
    def as_dict(self): return asdict(self)


def assess(signal:Any,book:dict|None,trigger_checked_at:float|None=None,now:float|None=None):
    now=float(now or time.time())
    try: checked=float(trigger_checked_at) if trigger_checked_at is not None else now
    except Exception: checked=now
    raw_age=now-checked
    age=max(0.0,raw_age); fresh=trigger_freshness(age)
    reasons=[]; blockers=[]; score=100.0
    side=str(getattr(signal,"side","") or "").upper()
    if side not in {"LONG","SHORT"}:
        blockers.append("invalid signal side for execution simulation"); score-=100
    if raw_age < -MAX_FUTURE_TRIGGER_SKEW_SEC:
        blockers.append(f"ENTRY trigger timestamp is {abs(raw_age):.1f}s in the future"); score-=100
    if age>HARD_TTL_SEC or fresh<=0:
        blockers.append(f"ENTRY trigger expired after {age:.1f}s"); score-=100
    elif fresh<.65:
        blockers.append(f"ENTRY trigger freshness too low {fresh:.0%}"); score-=45
    else: reasons.append(f"trigger freshness {fresh:.0%}")
    if not book:
        blockers.append("execution book unavailable"); score-=60
        spread=999.0; stability=0.0; impacts=[None,None,None]
    else:
        spread=_f(book.get("spread_bps"),999); stability=_f(book.get("stability_score"))
        rows=_normalized_rows(getattr(signal,"side",""),book)
        if not rows:
            blockers.append("execution depth ladder invalid/unsorted"); score-=50
        impacts=[_impact(getattr(signal,"side",""),book,n) for n in DEFAULT_NOTIONALS]
        if spread>4.0:
            blockers.append(f"spread too wide {spread:.1f}bps"); score-=35
        elif spread>2.5:
            score-=12; reasons.append(f"spread elevated {spread:.1f}bps")
        else: reasons.append(f"spread {spread:.1f}bps")
        if stability<65:
            blockers.append(f"L2 stability too low {stability:.0f}/100"); score-=35
        else: reasons.append(f"L2 stability {stability:.0f}/100")
        i1=impacts[1]
        if i1 is None:
            blockers.append("$1k executable depth unavailable"); score-=35
        elif i1>8.0:
            blockers.append(f"$1k impact too high {i1:.1f}bps"); score-=35
        elif i1>4.0:
            score-=15; reasons.append(f"$1k impact elevated {i1:.1f}bps")
        else: reasons.append(f"$1k impact {i1:.1f}bps")
        i5=impacts[2]
        if i5 is None:
            blockers.append("$5k executable depth unavailable"); score-=25
        elif i5>15.0:
            blockers.append(f"$5k impact too high {i5:.1f}bps"); score-=25
        elif i5>8.0:
            score-=10; reasons.append(f"$5k impact elevated {i5:.1f}bps")
        else: reasons.append(f"$5k impact {i5:.1f}bps")
        side=str(getattr(signal,"side","")).upper()
        adverse_change=_f(book.get("bid_depth_change_2s")) if side=="LONG" else _f(book.get("ask_depth_change_2s"))
        adverse_share=_f(book.get("adverse_long_share_5s")) if side=="LONG" else _f(book.get("adverse_short_share_5s"))
        if adverse_change<=-.45 or adverse_share>=.70:
            blockers.append("liquidity withdrawal/adverse imbalance before entry"); score-=45
    score=max(0.0,min(100.0,score*max(.35,fresh)))
    d=ExecutionDecision(not blockers and score>=75,round(score,2),round(age,3),round(fresh,4),round(spread,4),
                        None if impacts[0] is None else round(impacts[0],4),
                        None if impacts[1] is None else round(impacts[1],4),
                        None if impacts[2] is None else round(impacts[2],4),
                        round(stability,2),tuple(reasons),tuple(dict.fromkeys(blockers)))
    if isinstance(getattr(signal,"feature_snapshot",None),dict):
        signal.feature_snapshot.setdefault("execution_reality_v11170",{}).update(d.as_dict()|{"schema":SCHEMA,"negative_only":True})
    return d
