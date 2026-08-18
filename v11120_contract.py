"""V11.12 fail-closed live ENTRY contract.

Adds only vetoes on top of the audited V11.11 L2 gate:
- bookTicker is mandatory (no fail-open coherence path),
- aggressive flow must persist across multiple seconds,
- single-bucket flow concentration is rejected,
- sudden support-side liquidity withdrawal / spread shock is rejected.
"""
from __future__ import annotations
import math
from v11110_contract import ContractResult, evaluate_entry_contract


def _finite_number(value):
    try:
        x=float(value)
    except (TypeError,ValueError,OverflowError):
        return None
    return x if math.isfinite(x) else None


def evaluate_live_contract(side,l2,stability,quote,flow,now=None):
    side=str(side or "").upper(); st=stability or {}; l2=l2 or {}
    if side not in {"LONG","SHORT"}:
        return ContractResult(False,"invalid side for live entry contract",0.0)
    if not quote:
        return ContractResult(False,"Futures bookTicker unavailable",0.0)

    # NaN/Inf can make ordinary comparisons silently evaluate False. Validate
    # every top-of-book input before delegating to the inherited V11.11 gate.
    for label,value in (("book bid",quote.get("bid")),("book ask",quote.get("ask")),
                        ("book ts",quote.get("ts")),("L2 bid",l2.get("best_bid")),
                        ("L2 ask",l2.get("best_ask"))):
        x=_finite_number(value)
        if x is None:
            return ContractResult(False,f"non-finite live market data: {label}",0.0)
        if label!="book ts" and x<=0:
            return ContractResult(False,f"invalid live market data: {label}",0.0)

    # V11.11 computes these fields internally, but explicit validation here
    # prevents IEEE NaN/Inf from bypassing its ordinary threshold comparisons.
    for label,value in (("L2 stability score",st.get("stability_score",0)),
                        ("L2 samples",st.get("samples",0)),
                        ("L2 coverage",st.get("coverage_sec",0)),
                        ("L2 imbalance",st.get("median_imbalance_20bps",0)),
                        ("L2 bid replenishment",st.get("bid_replenishment_ratio",0)),
                        ("L2 ask replenishment",st.get("ask_replenishment_ratio",0))):
        if _finite_number(value) is None:
            return ContractResult(False,f"non-finite live market data: {label}",0.0)
    gap_age=st.get("last_gap_age_sec")
    if gap_age is not None and _finite_number(gap_age) is None:
        return ContractResult(False,"non-finite live market data: L2 gap age",0.0)

    base=evaluate_entry_contract(side,l2,st,quote=quote,now=now)
    if not base.ok:
        return base

    flow=flow or {}
    parsed={}
    flow_fields={
        "age":flow.get("age_sec",999999),
        "total":flow.get("total_notional",0),
        "trades":flow.get("trades",0),
        "active":flow.get("active_seconds_10s",flow.get("active_seconds",0)),
        "coverage":flow.get("coverage_10s",flow.get("coverage_sec",0)),
        "concentration":flow.get("max_bucket_share_10s",flow.get("max_bucket_share",1.0)),
        "recent_total":flow.get("recent10_total_notional",flow.get("total_notional",0)),
        "recent_trades":flow.get("recent10_trades",flow.get("trades",0)),
        "recent_buy_share":flow.get("buy_share_10s",flow.get("buy_share",.5)),
    }
    for name,value in flow_fields.items():
        x=_finite_number(value)
        if x is None:
            return ContractResult(False,f"non-finite live market data: flow {name}",base.score)
        parsed[name]=x
    age=parsed["age"]; total=parsed["total"]; trades=int(parsed["trades"]); active=int(parsed["active"])
    coverage=parsed["coverage"]; concentration=parsed["concentration"]
    recent_total=parsed["recent_total"]; recent_trades=int(parsed["recent_trades"]); recent_buy_share=parsed["recent_buy_share"]
    if not (0.0<=concentration<=1.000001 and 0.0<=recent_buy_share<=1.000001):
        return ContractResult(False,"invalid live flow ratio",base.score)
    if age<0 or min(total,recent_total,trades,recent_trades,active,coverage)<0:
        return ContractResult(False,"invalid negative live flow metric",base.score)
    if age>8.0:
        return ContractResult(False,f"live taker flow stale ({age:.1f}s)",base.score)
    if total<5000 or trades<5:
        return ContractResult(False,"live taker flow sample too small",base.score)
    if recent_total<1500 or recent_trades<3:
        return ContractResult(False,"last-10s taker flow sample too small",base.score)
    if active<3 or coverage<2.0:
        return ContractResult(False,f"live taker flow not persistent in last 10s ({active}s active, {coverage:.1f}s coverage)",base.score)
    if concentration>.85:
        return ContractResult(False,f"live taker flow concentrated in one recent burst ({concentration:.0%})",base.score)
    if side=="LONG" and recent_buy_share<.45:
        return ContractResult(False,f"last-10s taker flow reversed against LONG ({recent_buy_share:.0%} buys)",base.score)
    if side=="SHORT" and recent_buy_share>.55:
        return ContractResult(False,f"last-10s taker flow reversed against SHORT ({recent_buy_share:.0%} buys)",base.score)

    metric_values={
        "support_change":(st.get("bid_depth_change_2s",0) if side=="LONG" else st.get("ask_depth_change_2s",0)),
        "support_ratio":(st.get("bid_replenishment_ratio",0) if side=="LONG" else st.get("ask_replenishment_ratio",0)),
        "spread_ratio":st.get("spread_ratio_2s",1),
        "current_spread":st.get("current_spread_bps",0),
        "adverse":(st.get("adverse_long_share_5s",0) if side=="LONG" else st.get("adverse_short_share_5s",0)),
        "recent":st.get("recent5_samples",0),
    }
    clean={}
    for name,value in metric_values.items():
        # Missing optional shock metrics are neutral for backward-compatible
        # warm-up, but explicit NaN/Inf is data corruption and must fail closed.
        if value is None:
            clean[name]=0.0 if name not in {"spread_ratio"} else 1.0
            continue
        x=_finite_number(value)
        if x is None:
            return ContractResult(False,f"non-finite live market data: L2 {name}",base.score)
        clean[name]=x
    support_change=clean["support_change"]; support_ratio=clean["support_ratio"]
    spread_ratio=clean["spread_ratio"]; current_spread=clean["current_spread"]
    adverse=clean["adverse"]; recent=int(clean["recent"])
    if support_ratio<0 or spread_ratio<0 or current_spread<0 or not (0<=adverse<=1.000001) or recent<0:
        return ContractResult(False,"invalid live L2 shock metric",base.score)

    if support_change<-.45 and support_ratio<.65:
        return ContractResult(False,f"support-side liquidity pulled ({support_change:+.0%})",base.score)
    if spread_ratio>2.5 and current_spread>2.0:
        return ContractResult(False,f"micro spread shock ({spread_ratio:.1f}x)",base.score)
    if recent>=6 and adverse>.70:
        return ContractResult(False,f"persistent adverse L2 imbalance ({adverse:.0%})",base.score)
    return ContractResult(True,"V11.12 L2 + cross-feed + persistent-flow contract confirmed",base.score)
