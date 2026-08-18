"""V11.10 transparent #1 selection-stability diagnostics.

A ranked list always has a first item, but that does not mean #1 is materially
better than #2.  This module labels the final choice without changing trading
eligibility or inventing confidence.  It is intentionally diagnostic-only.
"""
from __future__ import annotations

from typing import Any


def _pro_key(s:Any):
    return (
        float(getattr(s,"professional_rank",0) or 0),
        float(getattr(s,"score",0) or 0),
        -float(getattr(s,"estimated_cost_r",0) or 0),
    )


def annotate(chosen,pool=None):
    selected=list(chosen or [])
    eligible=list(pool or selected)
    if not selected:
        return selected
    top=selected[0]
    p1=float(getattr(top,"decision_priority",getattr(top,"professional_rank",0)) or 0)
    p2=(float(getattr(selected[1],"decision_priority",getattr(selected[1],"professional_rank",0)) or 0)
        if len(selected)>1 else None)
    gap=(p1-p2) if p2 is not None else None
    base_top=max(eligible,key=_pro_key) if eligible else top
    consensus=(str(getattr(base_top,"symbol",""))==str(getattr(top,"symbol",""))
               and str(getattr(base_top,"side",""))==str(getattr(top,"side",""))
               and str(getattr(base_top,"timeframe",""))==str(getattr(top,"timeframe","")))
    if p2 is None:
        label="SOLO"
    elif gap>=2.0 and consensus:
        label="STABLE"
    elif gap>=1.0:
        label="GOOD"
    elif gap>=0.35:
        label="CLOSE"
    else:
        label="FRAGILE"
    payload={
        "label":label,
        "decision_gap":gap,
        "base_pro_consensus":consensus,
        "base_pro_top":str(getattr(base_top,"symbol","") or ""),
        "selected_top":str(getattr(top,"symbol","") or ""),
        "selected_count":len(selected),
        "eligible_count":len(eligible),
        "diagnostic_only":True,
    }
    try:
        top.selection_stability_label=label
        top.selection_priority_gap=gap
        top.selection_base_consensus=consensus
        top.feature_snapshot.setdefault("selection_stability_v11100",{}).update(payload)
    except Exception:
        pass
    return selected
