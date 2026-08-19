"""V11.18 negative-only regime, contradiction, stability and health defense.

This layer never creates alpha and never increases professional_rank.  It only
blocks or downgrades a fully-qualified ENTRY candidate when market context is
hostile, evidence contradicts itself, the trigger is unstable, or runtime health
is unsafe.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math, time
from typing import Any

SCHEMA="11.18-regime-portfolio-defense-v2"
PERSISTENCE_CONFIRMATIONS={
    "EXTREME_VOL":3,"HIGH_DISPERSION":3,"DIVERGENCE":3,
    "RANGE":3,"RANGE_LOW_VOL":3,
}

def required_confirmations(regime:str)->int:
    return int(PERSISTENCE_CONFIRMATIONS.get(str(regime or "").upper(),2))

@dataclass(frozen=True)
class DefenseDecision:
    eligible: bool
    score: float
    regime: str
    reasons: tuple[str,...]
    blockers: tuple[str,...]
    def as_dict(self): return asdict(self)

def _f(v,d=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else float(d)
    except Exception: return float(d)

def assess(signal:Any, book:dict|None=None, confirm_streak:int=0, health:Any=None, now:float|None=None):
    now=float(now or time.time()); blockers=[]; reasons=[]; score=100.0
    side=str(getattr(signal,"side","") or "").upper()
    regime=str(getattr(signal,"production_regime","") or "UNKNOWN").upper()
    fs=dict(getattr(signal,"feature_snapshot",{}) or {})
    market=dict(getattr(signal,"market_context",{}) or fs.get("market") or {})
    edge=dict(fs.get("indicator_edge_v11151") or {})

    if side not in {"LONG","SHORT"}:
        blockers.append("invalid side in defense layer"); score-=100

    # Regime-specific production defense. SHOCK is fail-closed; transitional or
    # range regimes demand stronger persistence rather than adding score.
    if regime=="SHOCK":
        blockers.append("market regime SHOCK"); score-=70
    elif regime in PERSISTENCE_CONFIRMATIONS:
        needed=required_confirmations(regime)
        if int(confirm_streak)<needed:
            blockers.append(f"{regime} requires {needed} confirmation checks")
            score-=35 if regime in {"EXTREME_VOL","HIGH_DISPERSION","DIVERGENCE"} else 25
        else:
            reasons.append(f"{regime} persistence confirmed")
    else:
        reasons.append(f"regime {regime or 'UNKNOWN'}")

    # Cross-market guard: an explicit BTC bias opposite to the trade requires
    # the scanner to have marked the candidate as independently strong.
    bias=str(market.get("bias") or market.get("btc_bias_raw") or "NEUTRAL").upper()
    independent=bool(market.get("independent_mode"))
    opposite=(side=="LONG" and bias=="SHORT") or (side=="SHORT" and bias=="LONG")
    if opposite and not independent:
        blockers.append(f"BTC bias {bias} opposes {side}"); score-=35
    elif opposite:
        score-=10; reasons.append("opposite BTC bias allowed only by independent-mode")

    # Contradiction matrix uses families, not raw indicator count, to avoid
    # double-counting correlated CVD/taker/OI evidence.
    location=[str((edge.get(k) or {}).get("state","NEUTRAL")).upper() for k in ("avwap","volume_profile")]
    participation=str((edge.get("rvol") or {}).get("state","NEUTRAL")).upper()
    structure=str((edge.get("liquidity_sweep") or {}).get("state","NEUTRAL")).upper()
    flow=str((edge.get("cvd_live") or edge.get("cvd") or {}).get("state","NEUTRAL")).upper()
    positioning=str((edge.get("oi_matrix") or {}).get("state","NEUTRAL")).upper()
    families={
        "location":"CONFLICT" if "CONFLICT" in location else ("SUPPORT" if "SUPPORT" in location else "NEUTRAL"),
        "participation":participation,"structure":structure,"flow":flow,"positioning":positioning,
    }
    conflicts=[k for k,v in families.items() if v=="CONFLICT"]
    if len(conflicts)>=2:
        blockers.append("multi-family contradiction: "+", ".join(conflicts)); score-=45
    elif len(conflicts)==1:
        score-=12; reasons.append("single-family conflict: "+conflicts[0])
    else: reasons.append("no multi-family contradiction")

    # Adverse-selection guard over short-lived microstructure.  Book freshness
    # itself remains authoritative in V11.17 snapshot/execution layers.
    b=dict(book or {})
    if b:
        micro=_f(b.get("microprice_drift_bps_2s"),0)
        adverse_share=_f(b.get("adverse_long_share_5s"),0) if side=="LONG" else _f(b.get("adverse_short_share_5s"),0)
        signed_micro=micro if side=="LONG" else -micro
        if signed_micro<=-2.5 or adverse_share>=.78:
            blockers.append("pre-entry adverse selection detected"); score-=45
        elif signed_micro<=-1.2 or adverse_share>=.62:
            score-=15; reasons.append("microstructure turning adverse")

    # Circuit breaker: runtime hard pause is authoritative.  We intentionally
    # inspect only stable attributes so this module remains testable without app/.
    if health is not None:
        if bool(getattr(health,"hard_pause",False)):
            blockers.append("runtime health circuit breaker active"); score-=100
        status=str(getattr(health,"status","") or "").upper()
        if status in {"DEGRADED","RED","ERROR"} and not bool(getattr(health,"hard_pause",False)):
            score-=15; reasons.append(f"runtime health {status}")

    score=max(0.0,min(100.0,score))
    d=DefenseDecision(not blockers and score>=75.0,round(score,2),regime,tuple(dict.fromkeys(reasons)),tuple(dict.fromkeys(blockers)))
    if isinstance(getattr(signal,"feature_snapshot",None),dict):
        signal.feature_snapshot.setdefault("defense_v11180",{}).update(d.as_dict()|{"schema":SCHEMA,"negative_only":True,"professional_rank_changed":False})
    return d
