"""Execution and L2-liquidity features for V11.4.1.

A single Binance depth snapshot is reused for market-impact and order-book
state metrics. The strategy never gets a signal from this module; it can only
be ranked lower or rejected after the core strategy has already confirmed it.
"""

from __future__ import annotations

import asyncio
import time

from app.market import _get

_cache={}
_history={}
_semaphore=asyncio.Semaphore(4)


def _simulate(levels,notional_usd,side):
    remaining=float(notional_usd)
    spent=0.0
    qty_total=0.0
    first_price=None
    for price_s,qty_s in levels:
        price=float(price_s); qty=float(qty_s)
        if price<=0 or qty<=0:
            continue
        if first_price is None:
            first_price=price
        capacity=price*qty
        take_usd=min(remaining,capacity)
        take_qty=take_usd/price
        spent+=take_usd
        qty_total+=take_qty
        remaining-=take_usd
        if remaining<=1e-9:
            break
    if first_price is None or qty_total<=0 or remaining>float(notional_usd)*.05:
        return 999.0
    avg=spent/qty_total
    if side=="BUY":
        return max(0.0,(avg/first_price-1)*10000)
    return max(0.0,(1-avg/first_price)*10000)


def _notional(levels):
    return sum(float(p)*float(q) for p,q in levels if float(p)>0 and float(q)>0)


def _within_bps(levels,mid,bps,is_bid):
    if mid<=0:
        return 0.0
    band=float(bps)/10000.0
    total=0.0
    for p,q in levels:
        price=float(p); qty=float(q)
        if price<=0 or qty<=0:
            continue
        eligible=(price>=mid*(1-band)) if is_bid else (price<=mid*(1+band))
        if eligible:
            total+=price*qty
    return total


def _imbalance(bid,ask):
    total=float(bid)+float(ask)
    return (float(bid)-float(ask))/total if total>0 else 0.0


def _book_state(impact_5k,imb10,total10,spread_bps,probe_notional=5000):
    coverage=float(total10)/max(1.0,float(probe_notional))
    impact=float(impact_5k)
    spread=float(spread_bps)
    # Relative to the execution probe: a market that cannot show several
    # multiples of the intended notional near mid is thin regardless of coin price.
    if impact>8 or spread>5 or coverage<4:
        return "THIN"
    if float(imb10)>=.60:
        return "BID_HEAVY"
    if float(imb10)<=-.60:
        return "ASK_HEAVY"
    if impact<=2 and spread<=1.5 and coverage>=20 and abs(float(imb10))<=.30:
        return "DEEP_BALANCED"
    return "BALANCED"


def _shape(bids,asks):
    if not bids or not asks:
        return {
            "best_bid":0.0,"best_ask":0.0,"mid":0.0,"spread_bps":999.0,
            "top_imbalance":0.0,"imbalance_5bps":0.0,"imbalance_10bps":0.0,
            "imbalance_20bps":0.0,"bid_depth_10bps":0.0,"ask_depth_10bps":0.0,
            "total_depth_10bps":0.0,"microprice":0.0,"microprice_bias_bps":0.0,
        }
    best_bid=float(bids[0][0]); best_ask=float(asks[0][0])
    bid_q=float(bids[0][1]); ask_q=float(asks[0][1])
    mid=(best_bid+best_ask)/2
    spread=(best_ask-best_bid)/mid*10000 if mid else 999.0
    # Microprice moves toward the side with less resting quantity.
    micro=((best_ask*bid_q)+(best_bid*ask_q))/(bid_q+ask_q) if bid_q+ask_q else mid
    micro_bias=(micro-mid)/mid*10000 if mid else 0.0

    top_bid=_notional(bids[:5]); top_ask=_notional(asks[:5])
    bid5=_within_bps(bids,mid,5,True); ask5=_within_bps(asks,mid,5,False)
    bid10=_within_bps(bids,mid,10,True); ask10=_within_bps(asks,mid,10,False)
    bid20=_within_bps(bids,mid,20,True); ask20=_within_bps(asks,mid,20,False)
    return {
        "best_bid":best_bid,"best_ask":best_ask,"mid":mid,"spread_bps":spread,
        "top_imbalance":_imbalance(top_bid,top_ask),
        "imbalance_5bps":_imbalance(bid5,ask5),
        "imbalance_10bps":_imbalance(bid10,ask10),
        "imbalance_20bps":_imbalance(bid20,ask20),
        "bid_depth_10bps":bid10,"ask_depth_10bps":ask10,
        "total_depth_10bps":bid10+ask10,
        "microprice":micro,"microprice_bias_bps":micro_bias,
    }


async def depth_metrics(symbol,max_age=45,force=False):
    symbol=str(symbol).upper()
    now=time.time()
    cached=_cache.get(symbol)
    if not force and cached and now-cached[0]<float(max_age):
        return dict(cached[1])

    try:
        async with _semaphore:
            data=await asyncio.wait_for(
                _get("/fapi/v1/depth",{"symbol":symbol,"limit":100}),
                timeout=8,
            )
        bids=(data or {}).get("bids") or []
        asks=(data or {}).get("asks") or []
        shape=_shape(bids,asks)
        buy_1k=_simulate(asks,1000,"BUY")
        sell_1k=_simulate(bids,1000,"SELL")
        buy_5k=_simulate(asks,5000,"BUY")
        sell_5k=_simulate(bids,5000,"SELL")

        prior=_history.get(symbol)
        prior_age=(now-prior[0]) if prior else None
        valid_prior=bool(prior and prior_age is not None and prior_age<=120)
        prev_imb=(
            float(prior[1].get("imbalance_10bps",shape["imbalance_10bps"]))
            if valid_prior else shape["imbalance_10bps"]
        )
        prev_total=(
            float(prior[1].get("total_depth_10bps",shape["total_depth_10bps"]))
            if valid_prior else shape["total_depth_10bps"]
        )
        depth_ratio=(shape["total_depth_10bps"]/prev_total) if prev_total>0 else 1.0

        result={
            **shape,
            "buy_1k_bps":buy_1k,"sell_1k_bps":sell_1k,
            "buy_5k_bps":buy_5k,"sell_5k_bps":sell_5k,
            "imbalance_delta_10bps":shape["imbalance_10bps"]-prev_imb,
            "depth_ratio_vs_previous":depth_ratio,
            "previous_age_sec":prior_age if valid_prior else None,
            "unavailable":False,
        }
        # State is directional-agnostic. Direction is applied later.
        result["depth_coverage_5k"]=shape["total_depth_10bps"]/5000.0
        result["state"]=_book_state(
            max(buy_5k,sell_5k),shape["imbalance_10bps"],
            shape["total_depth_10bps"],shape["spread_bps"],5000
        )
        _history[symbol]=(now,dict(result))
    except Exception:
        result={
            "best_bid":0.0,"best_ask":0.0,"mid":0.0,"spread_bps":999.0,
            "buy_1k_bps":5.0,"sell_1k_bps":5.0,
            "buy_5k_bps":10.0,"sell_5k_bps":10.0,
            "top_imbalance":0.0,"imbalance_5bps":0.0,"imbalance_10bps":0.0,
            "imbalance_20bps":0.0,"bid_depth_10bps":0.0,"ask_depth_10bps":0.0,
            "total_depth_10bps":0.0,"microprice":0.0,"microprice_bias_bps":0.0,
            "imbalance_delta_10bps":0.0,"depth_ratio_vs_previous":1.0,
            "previous_age_sec":None,"state":"UNAVAILABLE","unavailable":True,
        }

    _cache[symbol]=(now,dict(result))
    return dict(result)


async def impact(symbol,notional_usd=1000):
    m=await depth_metrics(symbol)
    if int(notional_usd)<=1000:
        return {"buy_bps":m["buy_1k_bps"],"sell_bps":m["sell_1k_bps"],"unavailable":m["unavailable"]}
    return {"buy_bps":m["buy_5k_bps"],"sell_bps":m["sell_5k_bps"],"unavailable":m["unavailable"]}


def _attach_l2(signal,m):
    side_sign=1 if signal.side=="LONG" else -1
    signal.l2_state=str(m.get("state","UNAVAILABLE"))
    signal.l2_imbalance_10=float(m.get("imbalance_10bps",0) or 0)
    signal.l2_signed_imbalance_10=side_sign*signal.l2_imbalance_10
    signal.l2_microprice_bias_bps=float(m.get("microprice_bias_bps",0) or 0)
    signal.l2_signed_microprice_bps=side_sign*signal.l2_microprice_bias_bps
    signal.l2_depth_10bps=float(m.get("total_depth_10bps",0) or 0)
    signal.l2_depth_ratio=float(m.get("depth_ratio_vs_previous",1) or 1)
    signal.l2_imbalance_delta=side_sign*float(m.get("imbalance_delta_10bps",0) or 0)

    if signal.side=="LONG":
        signal.impact_1k_bps=float(m.get("buy_1k_bps",999))
        signal.impact_5k_bps=float(m.get("buy_5k_bps",999))
    else:
        signal.impact_1k_bps=float(m.get("sell_1k_bps",999))
        signal.impact_5k_bps=float(m.get("sell_5k_bps",999))
    signal.liquidity_check_unavailable=bool(m.get("unavailable"))

    signal.feature_snapshot.setdefault("execution_v113",{}).update({
        "impact_1k_bps":signal.impact_1k_bps,
        "impact_5k_bps":signal.impact_5k_bps,
        "liquidity_check_unavailable":signal.liquidity_check_unavailable,
        "l2_state":signal.l2_state,
        "imbalance_10bps":signal.l2_imbalance_10,
        "signed_imbalance_10bps":signal.l2_signed_imbalance_10,
        "microprice_bias_bps":signal.l2_microprice_bias_bps,
        "signed_microprice_bias_bps":signal.l2_signed_microprice_bps,
        "depth_10bps_usd":signal.l2_depth_10bps,
        "depth_ratio_vs_previous":signal.l2_depth_ratio,
        "depth_coverage_5k":float(m.get("depth_coverage_5k",0) or 0),
        "signed_imbalance_delta":signal.l2_imbalance_delta,
        "spread_bps":float(m.get("spread_bps",999)),
    })
    # Backward-compatible field used by V11 UI/engine.
    signal.feature_snapshot.setdefault("execution_v1121",{}).update({
        "impact_1k_bps":signal.impact_1k_bps,
        "impact_5k_bps":signal.impact_5k_bps,
        "liquidity_check_unavailable":signal.liquidity_check_unavailable,
    })
    return signal


async def annotate(signals):
    async def one(s):
        return _attach_l2(s,await depth_metrics(s.symbol))
    if not signals:
        return []
    return await asyncio.gather(*(one(s) for s in signals))
