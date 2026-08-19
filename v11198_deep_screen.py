"""V11.19.8 · low-cost derivatives pre-screen.

All 36 wide shortlist names are screened. The screen deliberately uses only:
- one bulk premiumIndex request for funding/mark data across the market;
- one openInterest request per shortlisted symbol;
- technical/taker information already computed from closed candles.

No signal may be delivered from this layer. It only ranks which names deserve
the expensive full derivatives snapshot. The full production snapshot and all
existing final gates remain authoritative.
"""
from __future__ import annotations

import asyncio
import math
import os
import time

import app.market as market

SCREEN_TIMEOUT_SEC=max(12,min(60,int(os.getenv("V11198_SCREEN_TIMEOUT_SEC","38"))))
SCREEN_CONCURRENCY=max(3,min(8,int(os.getenv("V11198_SCREEN_CONCURRENCY","6"))))
FULL_DEEP_TARGET=max(10,min(18,int(os.getenv("V11198_FULL_DEEP_TARGET","14"))))
MIN_SCREEN_COVERAGE=max(.75,min(1.0,float(os.getenv("V11198_MIN_SCREEN_COVERAGE",".85"))))


def _f(value,default=0.0):
    try:
        v=float(value)
        return v if math.isfinite(v) else float(default)
    except Exception:
        return float(default)


async def _premium_bulk():
    payload=await market._get("/fapi/v1/premiumIndex")
    rows=payload if isinstance(payload,list) else []
    return {
        str(row.get("symbol")):row
        for row in rows if isinstance(row,dict) and row.get("symbol")
    }


async def _oi(symbol,sem):
    try:
        async with sem:
            payload=await asyncio.wait_for(
                market._get("/fapi/v1/openInterest",{"symbol":symbol}),
                timeout=10,
            )
        if not isinstance(payload,dict):
            raise RuntimeError("invalid openInterest payload")
        return symbol,payload,None
    except Exception as exc:
        return symbol,None,exc


def _screen_score(row,premium,oi_payload,ticker):
    """Rank only. Never acts as a production eligibility gate."""
    symbol,lower,base,higher,soft_l,soft_s=row
    long_side=float(soft_l)>=float(soft_s)
    soft=max(float(soft_l),float(soft_s))
    mark=_f((premium or {}).get("markPrice"),_f((ticker or {}).get("price")))
    funding=_f((premium or {}).get("lastFundingRate"))*100.0
    oi=_f((oi_payload or {}).get("openInterest"))
    oi_notional=oi*mark

    # Liquidity of positioning is useful for prioritization, not eligibility.
    oi_bonus=min(8.0,max(0.0,math.log10(max(1.0,oi_notional))-5.5)*2.0)

    # Funding against the desired side is modestly penalized; favorable/neutral
    # funding gets a small bonus. Extreme funding is not hard-blocked here because
    # the full snapshot owns the final crowding decision.
    if long_side:
        funding_adj=2.0 if funding<=0.01 else (-min(8.0,max(0.0,funding-0.02)*180.0))
    else:
        funding_adj=2.0 if funding>=-0.01 else (-min(8.0,max(0.0,-funding-0.02)*180.0))

    score=max(0.0,min(120.0,soft+oi_bonus+funding_adj))
    return {
        "symbol":symbol,
        "side":"LONG" if long_side else "SHORT",
        "score":score,
        "soft":soft,
        "funding_pct":funding,
        "open_interest":oi,
        "oi_notional":oi_notional,
    }


async def screen(rows,tickers):
    rows=list(rows or [])
    started=time.monotonic()
    if not rows:
        return [],{"status":"EMPTY","requested":0,"complete":0,"coverage":0.0}

    try:
        premium=await asyncio.wait_for(_premium_bulk(),timeout=12)
    except Exception:
        # Ranking can continue without funding if OI and technical ranking exist.
        premium={}

    sem=asyncio.Semaphore(SCREEN_CONCURRENCY)
    tasks=[asyncio.create_task(_oi(row[0],sem)) for row in rows]
    done,pending=await asyncio.wait(tasks,timeout=SCREEN_TIMEOUT_SEC)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending,return_exceptions=True)

    oi_map={}
    errors={}
    for task in done:
        try:
            symbol,payload,error=task.result()
            if error is None and payload is not None:
                oi_map[symbol]=payload
            else:
                errors[symbol]=f"{type(error).__name__}: {error}" if error else "unknown"
        except Exception as exc:
            errors["?"]=f"{type(exc).__name__}: {exc}"

    ranked=[]
    for row in rows:
        symbol=row[0]
        if symbol not in oi_map:
            continue
        ranked.append((
            _screen_score(
                row,premium.get(symbol),oi_map.get(symbol),tickers.get(symbol,{})
            ),
            row,
        ))
    ranked.sort(key=lambda x:float(x[0]["score"]),reverse=True)

    complete=len(ranked)
    coverage=complete/max(1,len(rows))
    diag={
        "status":"OK" if coverage>=MIN_SCREEN_COVERAGE else "INCOMPLETE",
        "requested":len(rows),
        "complete":complete,
        "coverage":round(coverage,4),
        "cancelled":len(pending),
        "errors":len(errors),
        "elapsed_sec":round(time.monotonic()-started,2),
        "premium_bulk_available":bool(premium),
        "full_deep_target":FULL_DEEP_TARGET,
        "top":[dict(meta) for meta,_ in ranked[:8]],
    }
    return ranked,diag


def select_full_deep(screened,target=FULL_DEEP_TARGET,min_opposite=3):
    """Adaptive strongest-first selection with minimum opposite-side coverage."""
    screened=list(screened or [])
    if not screened:
        return []
    target=max(1,min(int(target),len(screened)))
    preferred=[x for x in screened if x[0]["side"]=="LONG"]
    opposite=[x for x in screened if x[0]["side"]=="SHORT"]
    # Determine dominant direction by aggregate top strength, not arbitrary 50/50.
    long_strength=sum(float(x[0]["score"]) for x in preferred[:6])
    short_strength=sum(float(x[0]["score"]) for x in opposite[:6])
    dominant="LONG" if long_strength>=short_strength else "SHORT"
    dom=preferred if dominant=="LONG" else opposite
    opp=opposite if dominant=="LONG" else preferred

    picked=[]
    for x in dom:
        if len(picked)>=target-max(0,min_opposite):
            break
        picked.append(x)
    for x in opp[:min(min_opposite,target-len(picked))]:
        if x not in picked:
            picked.append(x)
    for x in screened:
        if len(picked)>=target:
            break
        if x not in picked:
            picked.append(x)

    picked.sort(key=lambda x:float(x[0]["score"]),reverse=True)
    return picked[:target]
