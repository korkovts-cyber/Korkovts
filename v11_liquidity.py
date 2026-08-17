"""Execution/liquidity checks for V11.2.1 final candidates.

One Binance depth snapshot is reused for all reference notionals for a symbol.
This avoids doubling REST weight and scan latency.
"""

from __future__ import annotations

import asyncio
import time

from app.market import _get

_cache = {}
_semaphore = asyncio.Semaphore(4)


def _simulate(levels, notional_usd, side):
    remaining = float(notional_usd)
    spent = 0.0
    qty_total = 0.0
    first_price = None
    for price_s, qty_s in levels:
        price = float(price_s); qty = float(qty_s)
        if price <= 0 or qty <= 0:
            continue
        if first_price is None:
            first_price = price
        capacity = price * qty
        take_usd = min(remaining, capacity)
        take_qty = take_usd / price
        spent += take_usd
        qty_total += take_qty
        remaining -= take_usd
        if remaining <= 1e-9:
            break
    if first_price is None or qty_total <= 0 or remaining > notional_usd * .05:
        return 999.0
    avg = spent / qty_total
    if side == "BUY":
        return max(0.0, (avg/first_price - 1) * 10000)
    return max(0.0, (1 - avg/first_price) * 10000)


async def depth_metrics(symbol):
    symbol=str(symbol).upper()
    now=time.time()
    cached=_cache.get(symbol)
    if cached and now-cached[0] < 45:
        return dict(cached[1])
    try:
        async with _semaphore:
            data=await asyncio.wait_for(
                _get("/fapi/v1/depth",{"symbol":symbol,"limit":100}),
                timeout=8,
            )
        bids=(data or {}).get("bids") or []
        asks=(data or {}).get("asks") or []
        result={
            "buy_1k_bps":_simulate(asks,1000,"BUY"),
            "sell_1k_bps":_simulate(bids,1000,"SELL"),
            "buy_5k_bps":_simulate(asks,5000,"BUY"),
            "sell_5k_bps":_simulate(bids,5000,"SELL"),
            "unavailable":False,
        }
    except Exception:
        # Never pretend missing liquidity data means zero slippage.
        result={
            "buy_1k_bps":5.0,"sell_1k_bps":5.0,
            "buy_5k_bps":10.0,"sell_5k_bps":10.0,
            "unavailable":True,
        }
    _cache[symbol]=(now,result)
    return dict(result)


async def impact(symbol, notional_usd=1000):
    """Compatibility helper used by older tests/code."""
    m=await depth_metrics(symbol)
    if int(notional_usd)<=1000:
        return {
            "buy_bps":m["buy_1k_bps"],"sell_bps":m["sell_1k_bps"],
            "unavailable":m["unavailable"],
        }
    return {
        "buy_bps":m["buy_5k_bps"],"sell_bps":m["sell_5k_bps"],
        "unavailable":m["unavailable"],
    }


async def annotate(signals):
    async def one(s):
        m=await depth_metrics(s.symbol)
        if s.side=="LONG":
            s.impact_1k_bps=float(m["buy_1k_bps"])
            s.impact_5k_bps=float(m["buy_5k_bps"])
        else:
            s.impact_1k_bps=float(m["sell_1k_bps"])
            s.impact_5k_bps=float(m["sell_5k_bps"])
        s.liquidity_check_unavailable=bool(m["unavailable"])
        s.feature_snapshot.setdefault("execution_v1121",{}).update({
            "impact_1k_bps":s.impact_1k_bps,
            "impact_5k_bps":s.impact_5k_bps,
            "liquidity_check_unavailable":s.liquidity_check_unavailable,
        })
        return s
    if not signals:
        return []
    return await asyncio.gather(*(one(s) for s in signals))
