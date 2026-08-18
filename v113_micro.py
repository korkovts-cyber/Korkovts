"""State-first L2 microstructure decision layer for V11.4.1.

Research principle:
1) identify the liquidity state first;
2) interpret order flow only inside that state;
3) punish contradictions more than confirmations are rewarded.

This module cannot rescue a signal rejected by the base Production engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MicroDecision:
    eligible: bool
    adjustment: float
    state: str
    label: str
    reasons: tuple[str,...]


def evaluate(signal):
    state=str(getattr(signal,"l2_state","UNAVAILABLE"))
    signed_imb=float(getattr(signal,"l2_signed_imbalance_10",0) or 0)
    signed_micro=float(getattr(signal,"l2_signed_microprice_bps",0) or 0)
    depth_ratio=float(getattr(signal,"l2_depth_ratio",1) or 1)
    imbalance_delta=float(getattr(signal,"l2_imbalance_delta",0) or 0)
    ofi=float(getattr(signal,"alpha_ofi_5m",0) or 0)
    direction=1 if signal.side=="LONG" else -1
    signed_ofi=direction*ofi

    reasons=[]
    adjustment=0.0
    eligible=True

    if state=="UNAVAILABLE":
        # Execution quality already applies a liquidity penalty. Do not invent
        # supportive L2 information when the snapshot is missing.
        return MicroDecision(True,-1.5,state,"L2 unavailable",("no fresh L2 state",))

    if state=="THIN":
        adjustment-=2.0
        reasons.append("thin L2 liquidity")

    # State-first contradiction: severe opposite book + opposite closed flow.
    severe_book=signed_imb<=-.65 and signed_micro<=-.25
    severe_flow=signed_ofi<=-.08
    deteriorating=depth_ratio<.65 or imbalance_delta<=-.30
    if severe_book and severe_flow and deteriorating:
        eligible=False
        adjustment-=6.0
        reasons.append("L2+flow adverse-selection cluster")
    else:
        if severe_book and severe_flow:
            adjustment-=3.0
            reasons.append("L2 and order flow contradict")
        elif signed_imb<=-.50 and signed_ofi<=-.05:
            adjustment-=1.8
            reasons.append("book/flow pressure against trade")

    # Confirmations are intentionally smaller than contradiction penalties.
    deep=state=="DEEP_BALANCED"
    if deep and signed_imb>=.35 and signed_micro>=.10 and signed_ofi>=.05:
        adjustment+=.8
        reasons.append("deep book supports flow")
    elif signed_imb>=.50 and signed_ofi>=.08:
        adjustment+=.4
        reasons.append("book and flow aligned")

    # Liquidity deterioration near delivery matters even when direction looks OK.
    if depth_ratio<.55:
        adjustment-=1.0
        reasons.append("near-book depth contracted")

    adjustment=max(-6.0,min(1.0,adjustment))
    label=(
        "BLOCK" if not eligible else
        "ADVERSE" if adjustment<=-2 else
        "CAUTION" if adjustment<0 else
        "SUPPORTIVE" if adjustment>0 else
        "NEUTRAL"
    )
    return MicroDecision(eligible,adjustment,state,label,tuple(reasons))


def attach(signal):
    d=evaluate(signal)
    signal.micro_eligible=d.eligible
    signal.micro_adjustment=d.adjustment
    signal.micro_label=d.label
    signal.micro_reasons=list(d.reasons)
    signal.feature_snapshot.setdefault("micro_v113",{}).update({
        "eligible":d.eligible,
        "adjustment":d.adjustment,
        "state":d.state,
        "label":d.label,
        "reasons":list(d.reasons),
    })
    return signal
