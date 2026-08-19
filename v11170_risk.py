"""V11.17 single non-bypassable final risk gateway.

V11.17.1 hardening makes the displayed score reflect the weakest verified
component instead of reporting a misleading 100/100 whenever every boolean gate
barely passed.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
from typing import Any
SCHEMA="11.17-final-risk-gateway-v2"

@dataclass(frozen=True)
class FinalRiskDecision:
    eligible: bool
    label: str
    score: float
    reasons: tuple[str,...]
    blockers: tuple[str,...]
    def as_dict(self): return asdict(self)


def _finite_score(v, default=None):
    try:
        x=float(v)
        if not math.isfinite(x): return default
        return max(0.0,min(100.0,x))
    except Exception:
        return default


def assess(signal:Any,strong,indicator_ok:bool,adaptive_ok:bool,snapshot,execution,safety_allow_live:bool=True,defense=None):
    blockers=[]; reasons=[]; penalties=0.0; components=[]

    strong_score=_finite_score(getattr(strong,"score",None))
    if not bool(getattr(strong,"auto_eligible",False)):
        blockers.append("Strong Consensus below AUTO floor"); penalties+=35
    else:
        reasons.append("Strong Consensus PASS")
        if strong_score is not None: components.append(strong_score)

    fs=dict(getattr(signal,"feature_snapshot",{}) or {})
    indicator=dict(fs.get("indicator_edge_v11151") or {})
    indicator_score=_finite_score(indicator.get("score"))
    if not indicator_ok:
        blockers.append("Indicator Edge below quality-first floor"); penalties+=30
    else:
        reasons.append("Indicator Edge PASS")
        if indicator_score is not None: components.append(indicator_score)

    adaptive=dict(fs.get("adaptive_edge_v11160") or {})
    adaptive_score=_finite_score(adaptive.get("score"))
    if not adaptive_ok:
        blockers.append("Adaptive Edge degraded/quarantined"); penalties+=45
    else:
        reasons.append("Adaptive Edge PASS")
        if adaptive_score is not None: components.append(adaptive_score)

    if not bool(getattr(snapshot,"eligible",False)):
        blockers.extend(list(getattr(snapshot,"blockers",()) or ("market snapshot invalid",))); penalties+=45
    else:
        reasons.append("Market Snapshot PASS")
        ss=_finite_score(getattr(snapshot,"score",None))
        if ss is not None: components.append(ss)

    if not bool(getattr(execution,"eligible",False)):
        blockers.extend(list(getattr(execution,"blockers",()) or ("execution reality failed",))); penalties+=45
    else:
        reasons.append("Execution Reality PASS")
        es=_finite_score(getattr(execution,"score",None))
        if es is not None: components.append(es)

    if defense is not None:
        if not bool(getattr(defense,"eligible",False)):
            blockers.extend(list(getattr(defense,"blockers",()) or ("V11.18 defense failed",))); penalties+=45
        else:
            reasons.append("Regime/Defense PASS")
            ds=_finite_score(getattr(defense,"score",None))
            if ds is not None: components.append(ds)

    # Safety remains authoritative immediately after this quality gateway.  It
    # is deliberately reported, not duplicated, so SHADOW/POSITION_BUSY logic
    # keeps its existing lifecycle semantics.
    if not safety_allow_live: reasons.append("live safety currently non-production")

    blockers=tuple(dict.fromkeys(str(x) for x in blockers if x))
    reasons=tuple(dict.fromkeys(str(x) for x in reasons if x))
    weakest=min(components) if components else 100.0
    score=max(0.0,min(100.0,weakest-penalties))
    eligible=not blockers and score>=75.0
    label="PASS" if eligible else "BLOCK"
    d=FinalRiskDecision(eligible,label,round(score,2),reasons,blockers)
    if isinstance(getattr(signal,"feature_snapshot",None),dict):
        signal.feature_snapshot.setdefault("final_risk_gateway_v11170",{}).update(d.as_dict()|{
            "schema":SCHEMA,"negative_only":True,"weakest_component":round(weakest,2)
        })
    return d
