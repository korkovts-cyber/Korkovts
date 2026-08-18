"""V11.11 fail-closed wrapper around the audited V11.7.1 ENTRY NOW machine.

The underlying strategy/state persistence is untouched. This layer can only
turn READY into WAIT when sequence-synchronised Futures L2 is not trustworthy.
"""
from __future__ import annotations
from dataclasses import replace
import asyncio
import logging
import v1142_entry_now as _base
from v11110_futures_orderbook import snapshot as futures_l2_snapshot, stability as futures_l2_stability
from v11110_contract import evaluate_entry_contract
from v11110_tape import capture_decision

log=logging.getLogger(__name__)
ENTRY_GATE_VERSION="11.11.0-futures-l2"
_last_gate={}

# Stable re-exports used by bot_v11110. Keeping these delegated avoids changing
# the V11.10/11.7.1 state-machine and its historical regression contracts.
init=_base.init
arm=_base.arm
active_rows=_base.active_rows
active_symbols=_base.active_symbols
get_row=_base.get_row
record_check=_base.record_check
mark_pending_delivery=_base.mark_pending_delivery
mark_delivery_uncertain=_base.mark_delivery_uncertain
mark_triggered=_base.mark_triggered
mark_shadowed=_base.mark_shadowed
cancel=_base.cancel
def status_text():
    return _base.status_text()+"\n\n🧱 <b>ENTRY GATE 11.11</b> · Futures sequence-synced L2 · fail-closed"
row_from_signal=_base.row_from_signal
TriggerAssessment=_base.TriggerAssessment

async def assess_row(row):
    symbol=str(row["symbol"]).upper()
    # Reuse the exact audited V11.7.1 pure evaluator, while retaining the exact
    # 1m/3m inputs for forensic replay. This is logically equivalent to
    # _base.assess_row() before the new L2 veto is applied.
    f1,f3=await asyncio.gather(
        _base.get_klines(symbol,"1m",80),
        _base.get_klines(symbol,"3m",80),
    )
    px=_base.live_price(symbol,20)
    quote=_base.live_book(symbol,20)
    flow=_base.live_flow(symbol,60,20)
    assessment=_base.evaluate(row,f1,f3,px=px,bk=quote,flow_row=flow)
    l2=futures_l2_snapshot(symbol,3.0,100)
    st=futures_l2_stability(symbol,3.0)
    contract=evaluate_entry_contract(str(row.get("side") or ""),l2,st,quote=quote)
    _last_gate[symbol]={
        "version":ENTRY_GATE_VERSION,"ok":bool(contract.ok),"reason":str(contract.reason),
        "score":float(contract.score),"sequence_synced":bool((l2 or {}).get("sequence_synced")),
        "event_age_sec":(l2 or {}).get("event_age_sec"),
        "stability_score":float((st or {}).get("stability_score",0) or 0),
        "samples":int((st or {}).get("samples",0) or 0),
        "coverage_sec":float((st or {}).get("coverage_sec",0) or 0),
    }
    pre_state=str(assessment.state)
    if pre_state=="READY" and not contract.ok:
        assessment=replace(
            assessment,state="WAIT",score=min(79.0,float(assessment.score)),
            reason=(contract.reason+"; "+assessment.reason)[:500],
        )
    elif pre_state=="READY" and contract.ok:
        assessment=replace(assessment,reason=("Futures L2 confirmed; "+assessment.reason)[:500])

    # Persist high-value transitions/vetoes only; do not write every 30s WAIT.
    previous=str(row.get("last_state") or "")
    last_reason=str(row.get("last_reason") or "")
    important_veto=(pre_state=="READY" and not contract.ok and str(contract.reason) not in last_reason)
    should_capture=(str(assessment.state)=="READY" or str(assessment.state)!=previous or important_veto)
    if should_capture:
        try:
            tape_quote=dict(quote or {})
            tape_quote["price"]=float(px or 0)
            capture_decision(
                row,assessment,frame1=f1,frame3=f3,flow=flow,quote=tape_quote,
                l2=l2,l2_stability=st,force=str(assessment.state)=="READY",
            )
        except Exception as exc:
            log.debug("V11.11 tape capture failed %s: %s",symbol,exc)
    return assessment

async def assess_signal(signal):
    assessment=await assess_row(_base.row_from_signal(signal))
    try:
        signal.feature_snapshot.setdefault("entry_gate_v11110",{}).update(
            dict(_last_gate.get(str(signal.symbol).upper(),{}))
        )
    except Exception:
        pass
    return assessment

def gate_status(symbol):
    return dict(_last_gate.get(str(symbol or "").upper(),{}))
