"""V11.12 deterministic replay checks for the live entry contract."""
from __future__ import annotations
from v11120_contract import evaluate_live_contract


def replay_gate(bundle,now=None):
    arm=bundle.get("arm") or {}
    side=str(arm.get("side") or "")
    l2=bundle.get("l2") or {}
    stability=bundle.get("l2_stability") or {}
    quote=bundle.get("quote") or {}
    flow=bundle.get("flow") or {}
    ts=float(now if now is not None else bundle.get("captured_at") or 0)
    result=evaluate_live_contract(side,l2,stability,quote,flow,now=ts or None)
    return {"ok":bool(result.ok),"reason":str(result.reason),"score":float(result.score),"gate_version":"11.12.0-live-contract"}


def compare_gate(bundle,recorded_gate=None):
    replayed=replay_gate(bundle)
    recorded=recorded_gate or bundle.get("gate") or {}
    if not recorded:
        return {**replayed,"recorded":None,"same":None}
    expected=bool(recorded.get("ok"))
    return {**replayed,"recorded":expected,"same":expected==bool(replayed["ok"])}
