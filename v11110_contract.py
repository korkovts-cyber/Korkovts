"""Fail-closed ENTRY NOW market-data contract for V11.11."""
from __future__ import annotations
from dataclasses import dataclass
import time

@dataclass(frozen=True)
class ContractResult:
    ok:bool; reason:str; score:float

def _crossfeed_check(l2,quote,now):
    if quote is None:
        return None
    try:
        qbid=float(quote.get("bid") or 0); qask=float(quote.get("ask") or 0)
        lbid=float(l2.get("best_bid") or 0); lask=float(l2.get("best_ask") or 0)
        qts=float(quote.get("ts") or 0)
    except Exception:
        return "Futures bookTicker coherence fields invalid"
    if qbid<=0 or qask<=0 or qask<qbid or lbid<=0 or lask<=0 or lask<lbid:
        return "Futures L2/bookTicker top-of-book unavailable"
    age=max(0.0,float(now)-qts) if qts>0 else 999999.0
    if age>3.0:
        return f"Futures bookTicker stale ({age:.1f}s)"
    qmid=(qbid+qask)/2; lmid=(lbid+lask)/2; ref=max(1e-12,(qmid+lmid)/2)
    qspread=(qask-qbid)/qmid*10000 if qmid>0 else 999999.0
    lspread=(lask-lbid)/lmid*10000 if lmid>0 else 999999.0
    divergence=max(abs(qbid-lbid),abs(qask-lask))/ref*10000
    tolerance=max(6.0,2.0*max(qspread,lspread))
    if divergence>tolerance:
        return f"Futures L2/bookTicker diverged ({divergence:.1f}bps > {tolerance:.1f})"
    return None

def evaluate_entry_contract(side,l2,stability,quote=None,now=None):
    side=str(side or "").upper(); l2=l2 or {}; st=stability or {}
    if not l2 or not l2.get("sequence_synced") or not l2.get("healthy"):
        return ContractResult(False,"Futures L2 sequence/freshness unavailable",0.0)
    coherence=_crossfeed_check(l2,quote,float(now or time.time()))
    if coherence:
        return ContractResult(False,coherence,0.0)
    samples=int(st.get("samples",0) or 0); coverage=float(st.get("coverage_sec",0) or 0)
    score=float(st.get("stability_score",0) or 0)
    if samples<8 or coverage<5.0 or score<65:
        return ContractResult(False,f"Futures L2 warming/unstable ({samples} samples, {coverage:.1f}s, score {score:.0f})",score)
    gap_age=st.get("last_gap_age_sec")
    if gap_age is not None and float(gap_age)<30:
        return ContractResult(False,"recent Futures L2 sequence gap",score)
    imb=float(st.get("median_imbalance_20bps",0) or 0)
    bid_rep=float(st.get("bid_replenishment_ratio",0) or 0); ask_rep=float(st.get("ask_replenishment_ratio",0) or 0)
    signed=imb if side=="LONG" else -imb
    support=bid_rep if side=="LONG" else ask_rep
    if signed<-.35:
        return ContractResult(False,f"local depth strongly opposes {side} ({signed:+.2f})",score)
    if support<.35:
        return ContractResult(False,f"resting liquidity support weak ({support:.2f})",score)
    return ContractResult(True,"Futures L2 sequence/stability/coherence confirmed",score)
