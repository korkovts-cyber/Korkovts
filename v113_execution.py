"""Final execution + state-first microstructure revalidation for V11.7.1."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.config import ROUND_TRIP_COST_PCT
from v11_liquidity import depth_metrics, _attach_l2
from v113_micro import attach as attach_micro


@dataclass(frozen=True)
class Revalidation:
    eligible: bool
    status: str
    reason: str
    mid: float
    bid: float
    ask: float
    spread_bps: float
    distance_r: float
    micro_adjustment: float
    l2_state: str
    total_cost_r: float
    checked_at: float


def evaluate_quote(signal,bid,ask,max_spread_bps=5.0,
                   max_favorable_r=.35,max_adverse_r=.70):
    now=time.time()
    bid=float(bid or 0); ask=float(ask or 0)
    if bid<=0 or ask<=0 or ask<bid:
        return Revalidation(False,"NO_QUOTE","fresh execution quote unavailable",
                            0,bid,ask,999,0,0,"UNAVAILABLE",0.0,now)

    mid=(bid+ask)/2
    spread=(ask-bid)/mid*10000 if mid else 999
    entry=float(signal.entry_high if signal.side=="LONG" else signal.entry_low)
    stop=float(signal.stop); tp1=float(signal.tp1)
    risk=abs(entry-stop)
    if risk<=0:
        return Revalidation(False,"BAD_GEOMETRY","invalid entry/stop geometry",
                            mid,bid,ask,spread,0,0,"UNKNOWN",0.0,now)

    if spread>max_spread_bps:
        return Revalidation(False,"SPREAD_WIDE",f"live spread {spread:.1f}bps",
                            mid,bid,ask,spread,0,0,"UNKNOWN",0.0,now)

    if signal.side=="LONG":
        distance_r=(mid-entry)/risk
        if bid<=stop:
            return Revalidation(False,"STOP_INVALID","price already invalidated LONG",
                                mid,bid,ask,spread,distance_r,0,"UNKNOWN",0.0,now)
        if ask>=tp1:
            return Revalidation(False,"MOVE_DONE","TP1 already reached before delivery",
                                mid,bid,ask,spread,distance_r,0,"UNKNOWN",0.0,now)
    else:
        distance_r=(entry-mid)/risk
        if ask>=stop:
            return Revalidation(False,"STOP_INVALID","price already invalidated SHORT",
                                mid,bid,ask,spread,distance_r,0,"UNKNOWN",0.0,now)
        if bid<=tp1:
            return Revalidation(False,"MOVE_DONE","TP1 already reached before delivery",
                                mid,bid,ask,spread,distance_r,0,"UNKNOWN",0.0,now)

    if distance_r>max_favorable_r:
        return Revalidation(False,"LATE_ENTRY",f"price moved {distance_r:+.2f}R beyond entry",
                            mid,bid,ask,spread,distance_r,0,"UNKNOWN",0.0,now)
    if distance_r< -max_adverse_r:
        return Revalidation(False,"ENTRY_DEGRADED",f"price moved {distance_r:+.2f}R against setup",
                            mid,bid,ask,spread,distance_r,0,"UNKNOWN",0.0,now)

    return Revalidation(True,"OK","fresh execution window",
                        mid,bid,ask,spread,distance_r,0,"UNKNOWN",0.0,now)


def _cost_components(signal):
    entry=float(signal.entry_high if signal.side=="LONG" else signal.entry_low)
    stop=float(signal.stop)
    risk=abs(entry-stop)
    if risk<=0:
        return {"static_r":999.0,"entry_impact_r":0.0,"exit_impact_r":0.0,
                "funding_allowance_r":0.0,"total_r":999.0}

    # Recompute configured round-trip fee/cost on the FINAL tick-rounded
    # geometry. The strategy's earlier estimated_cost_r was calculated before
    # exchange-filter normalization and can therefore be slightly stale.
    strategy_static=float(getattr(signal,"estimated_cost_r",0) or 0)
    static=entry*(float(ROUND_TRIP_COST_PCT)/100.0)/risk
    # LONG enters through asks and exits through bids. SHORT does the inverse.
    if signal.side=="LONG":
        entry_bps=float(getattr(signal,"buy_1k_bps",0) or 0)
        exit_bps=float(getattr(signal,"sell_1k_bps",0) or 0)
        funding_rate=max(0.0,float(getattr(signal,"funding",0) or 0))
    else:
        entry_bps=float(getattr(signal,"sell_1k_bps",0) or 0)
        exit_bps=float(getattr(signal,"buy_1k_bps",0) or 0)
        funding_rate=max(0.0,-float(getattr(signal,"funding",0) or 0))

    entry_impact_r=entry*(entry_bps/10000.0)/risk
    exit_impact_r=entry*(exit_bps/10000.0)/risk

    # Short-term 15M signals normally expire inside four hours. For 1H signals
    # reserve one current funding payment as a conservative allowance.
    funding_allowance_r=(
        entry*funding_rate/risk
        if str(getattr(signal,"timeframe","")).upper()=="1H"
        else 0.0
    )
    total=static+entry_impact_r+exit_impact_r+funding_allowance_r
    return {
        "static_r":static,
        "strategy_static_r_before_rounding":strategy_static,
        "entry_impact_r":entry_impact_r,
        "exit_impact_r":exit_impact_r,
        "funding_allowance_r":funding_allowance_r,
        "total_r":total,
    }


def _total_cost_r(signal):
    return _cost_components(signal)["total_r"]


async def revalidate(signal):
    # Force a new top-100 snapshot. The earlier scan snapshot remains in
    # v11_liquidity history and is used to measure L2 transition/deterioration.
    m=await depth_metrics(signal.symbol,force=True)
    _attach_l2(signal,m)

    quote=evaluate_quote(
        signal,
        float(m.get("best_bid",0) or 0),
        float(m.get("best_ask",0) or 0),
    )
    if not quote.eligible:
        result=quote
    else:
        signal.buy_1k_bps=float(m.get("buy_1k_bps",999))
        signal.sell_1k_bps=float(m.get("sell_1k_bps",999))
        signal.buy_5k_bps=float(m.get("buy_5k_bps",999))
        signal.sell_5k_bps=float(m.get("sell_5k_bps",999))
        signal=attach_micro(signal)
        cost_parts=_cost_components(signal)
        total_cost=float(cost_parts["total_r"])
        signal.feature_snapshot.setdefault("execution_revalidation",{}).update({
            "cost_components":cost_parts,
        })
        if total_cost>.30:
            result=Revalidation(
                False,"COST_EDGE",
                f"estimated fee+impact cost {total_cost:.2f}R exceeds 0.30R",
                quote.mid,quote.bid,quote.ask,quote.spread_bps,quote.distance_r,
                float(getattr(signal,"micro_adjustment",0) or 0),
                str(getattr(signal,"l2_state","UNKNOWN")),
                total_cost,time.time(),
            )
        elif not bool(getattr(signal,"micro_eligible",True)):
            result=Revalidation(
                False,"MICRO_BLOCK",
                "; ".join(getattr(signal,"micro_reasons",[]) or ["adverse L2 state"]),
                quote.mid,quote.bid,quote.ask,quote.spread_bps,quote.distance_r,
                float(getattr(signal,"micro_adjustment",0) or 0),
                str(getattr(signal,"l2_state","UNKNOWN")),
                total_cost,time.time(),
            )
        else:
            result=Revalidation(
                True,"OK","fresh execution + microstructure + cost window",
                quote.mid,quote.bid,quote.ask,quote.spread_bps,quote.distance_r,
                float(getattr(signal,"micro_adjustment",0) or 0),
                str(getattr(signal,"l2_state","UNKNOWN")),
                total_cost,time.time(),
            )

    signal.execution_revalidation=result
    signal.feature_snapshot.setdefault("execution_revalidation",{}).update({
        "eligible":result.eligible,
        "status":result.status,
        "reason":result.reason,
        "mid":result.mid,"bid":result.bid,"ask":result.ask,
        "spread_bps":result.spread_bps,
        "distance_r":result.distance_r,
        "micro_adjustment":result.micro_adjustment,
        "l2_state":result.l2_state,
        "total_cost_r":result.total_cost_r,
        "checked_at_epoch":result.checked_at,
        "quote_source":"fresh-depth-100",
    })
    return signal,result


async def revalidate_many(signals):
    if not signals:
        return []
    import asyncio
    return await asyncio.gather(*(revalidate(s) for s in signals))
