"""V11.15 Strong Consensus Gate.

Negative-only quality overlay for already-qualified Futures candidates.
It never increases professional_rank and never rescues a rejected setup.
Its purpose is to distinguish a merely-qualified candidate from a setup that
has broad independent confirmation strong enough for AUTO/ENTRY NOW.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

try:
    from v11151_indicator_edge import assessment as indicator_edge_assessment
except Exception:
    indicator_edge_assessment=None

SCHEMA="11.15.0-strong-consensus-v1"

@dataclass(frozen=True)
class StrongAssessment:
    label:str
    score:float
    auto_eligible:bool
    prime_eligible:bool
    reasons:tuple[str,...]
    blockers:tuple[str,...]


def _f(v,default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def assess(signal:Any)->StrongAssessment:
    snap=dict(getattr(signal,"feature_snapshot",{}) or {})
    evidence=dict(snap.get("evidence_v117") or {})
    meta=dict(snap.get("meta_v113") or {})
    edge=dict(snap.get("decision_edge_v11100") or {})
    rv=dict(snap.get("execution_revalidation") or {})
    margin=dict(snap.get("decision_margin_v11140") or {})
    stability=dict(snap.get("selection_stability_v11100") or {})

    rank=_f(getattr(signal,"professional_rank",0))
    support=int(evidence.get("support",getattr(signal,"evidence_support",0)) or 0)
    market=dict(snap.get("market") or getattr(signal,"market_context",{}) or {})
    independent_relief=bool(market.get("breadth_blocked") and market.get("independent_mode"))
    # V11.15 baseline contract remains support>=5 in normal regimes.
    # V11.18 may relax only the already-earned breadth-divergence independent mode
    # to 4 independent families; this never applies to normal market regimes.
    required_support=4 if independent_relief else 5
    conflicts=int(evidence.get("conflict",getattr(signal,"evidence_conflicts",0)) or 0)
    hard=list(evidence.get("hard_conflicts") or [])
    edge_label=str(edge.get("label") or "INSUFFICIENT").upper()
    edge_lcb=edge.get("lcb90_r",getattr(signal,"expected_net_r_lcb",None))
    edge_n=int(edge.get("n",getattr(signal,"edge_sample_n",0)) or 0)
    edge_days=int(edge.get("block_days",getattr(signal,"edge_block_days",0)) or 0)
    meta_ready=bool(meta.get("ready"))
    meta_eligible=bool(meta.get("eligible",True))
    meta_score=_f(meta.get("score"),.5)
    meta_threshold=_f(meta.get("threshold"),.6)
    fresh_pen=_f(rv.get("freshness_penalty",getattr(signal,"execution_freshness_penalty",0)))
    cost_pen=_f(getattr(signal,"live_cost_penalty",0))
    latency_pen=_f(rv.get("pipeline_latency_penalty",getattr(signal,"pipeline_latency_penalty",0)))
    selection=str(stability.get("label") or getattr(signal,"selection_stability_label","") or "").upper()
    margin_label=str(margin.get("label") or getattr(signal,"decision_margin_label","") or "").upper()
    margin_value=margin.get("margin_to_best_competitor",getattr(signal,"decision_margin",None))
    margin_value=None if margin_value is None else _f(margin_value)

    reasons=[]; blockers=[]
    score=0.0
    edge_pack={"available":False,"unique_support":0,"unique_conflicts":0,"refinement_conflicts":0}
    try:
        if indicator_edge_assessment is not None: edge_pack=indicator_edge_assessment(signal)
    except Exception:
        pass

    # Rank contributes strongly but cannot dominate independent evidence.
    score += max(0.0,min(35.0,(rank-75.0)*2.5))
    reasons.append(f"PRO {rank:.1f}")

    # Independent families are the core of the consensus gate.
    score += max(0.0,min(35.0,support*5.0))
    score -= min(18.0,conflicts*6.0)
    reasons.append(f"evidence {support} support/{conflicts} conflict")
    if hard:
        blockers.append(f"hard evidence conflict: {hard[0]}")

    # Fresh execution penalties are negative-only.
    execution_pen=max(0.0,fresh_pen)+max(0.0,cost_pen)+max(0.0,latency_pen)
    score -= min(14.0,execution_pen*4.0)
    if execution_pen<=.75:
        reasons.append("fresh execution clean")
    elif execution_pen>1.75:
        blockers.append(f"execution penalty {execution_pen:.2f}")

    # Mature negative historical edge is a blocker. Positive history can only
    # increase confidence in the label, never professional_rank.
    if edge_label in {"WEAK_EDGE","EDGE_WARNING"}:
        blockers.append(edge_label)
        score-=12.0
    elif edge_label=="UNCERTAIN_EDGE":
        score-=5.0
    elif edge_label.startswith("ROBUST_") and edge_lcb is not None:
        score+=min(8.0,max(0.0,_f(edge_lcb))*12.0)
        reasons.append(f"robust edge n={edge_n}/days={edge_days}")

    # Meta only participates when it has independently earned READY status.
    if meta_ready:
        if not meta_eligible or meta_score<meta_threshold:
            blockers.append("Meta OOS rejects")
            score-=15.0
        else:
            score+=6.0
            reasons.append(f"Meta OOS {meta_score:.2f}")

    # A fragile/tied top candidate should not receive PRIME branding unless the
    # rest of the evidence is exceptional.
    if selection=="FRAGILE":
        score-=7.0
    elif selection in {"STABLE","SOLO"}:
        score+=5.0
    elif selection=="GOOD":
        score+=3.0

    if margin_label=="CLEAR_PRIME":
        score+=4.0
    elif margin_label=="NEAR_TIE":
        score-=3.0

    pack_available=bool(edge_pack.get("available"))
    pack_support=int(edge_pack.get("unique_support",0) or 0)
    pack_conflict=int(edge_pack.get("unique_conflicts",0) or 0)
    refine_conflict=int(edge_pack.get("refinement_conflicts",0) or 0)
    if pack_available:
        score+=min(12.0,pack_support*4.0)
        score-=min(14.0,pack_conflict*7.0)
        score-=min(8.0,refine_conflict*4.0)
        reasons.append(f"edge pack {pack_support} unique support/{pack_conflict} conflict")
        if pack_conflict>=2: blockers.append("Indicator Edge has 2+ independent context conflicts")
        if refine_conflict>=2: blockers.append("CVD and OI both oppose the trade")

    score=max(0.0,min(100.0,score))

    auto_eligible=(
        not blockers
        and rank>=84.0
        and support>=required_support
        and conflicts<=1
        and execution_pen<=1.75
        and score>=50.0
        and (not pack_available or (pack_support>=1 and pack_conflict<=1 and refine_conflict<=1))
    )
    prime_eligible=(
        auto_eligible
        and rank>=88.0
        and support>=6
        and conflicts==0
        and score>=68.0
        and selection!="FRAGILE"
        and margin_label!="NEAR_TIE"
        and (not pack_available or (pack_support>=2 and pack_conflict==0 and refine_conflict==0))
    )

    label="PRIME_STRONG" if prime_eligible else ("STRONG" if auto_eligible else "QUALIFIED_ONLY")
    return StrongAssessment(label,round(score,2),auto_eligible,prime_eligible,tuple(reasons),tuple(blockers))


def annotate(signal:Any)->Any:
    a=assess(signal)
    signal.strong_signal_label=a.label
    signal.strong_signal_score=a.score
    signal.strong_auto_eligible=a.auto_eligible
    signal.strong_prime_eligible=a.prime_eligible
    signal.feature_snapshot.setdefault("strong_consensus_v11150",{}).update({
        "schema":SCHEMA,**asdict(a),
        "negative_only":True,
        "professional_rank_changed":False,
    })
    return signal


def annotate_many(rows):
    return [annotate(s) for s in (rows or [])]
