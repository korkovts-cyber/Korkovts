"""V11.19.7 · resilient mandatory Futures source stage.

The full-universe scan needs two mandatory discovery sources:
- exchangeInfo -> tradable USDT perpetual universe
- ticker/24hr -> liquidity / 24h cross-sectional context

They are fetched independently with explicit diagnostics. exchangeInfo may use a
long-lived recently verified cache because the contract universe changes slowly.
ticker/24hr may use only a short recently verified cache because it affects
liquidity/ranking, while fresh candles/L2 still remain authoritative for entries.
"""
from __future__ import annotations

import asyncio
import copy
import os
import time

import app.market as market
import v1141_governor as governor

EXCHANGEINFO_TIMEOUT_SEC=max(8,min(25,int(os.getenv("V11197_EXCHANGEINFO_TIMEOUT_SEC","14"))))
TICKER_TIMEOUT_SEC=max(8,min(25,int(os.getenv("V11197_TICKER_TIMEOUT_SEC","14"))))
EXCHANGEINFO_CACHE_MAX_SEC=max(600,min(86400,int(os.getenv("V11197_EXCHANGEINFO_CACHE_MAX_SEC","21600"))))
TICKER_CACHE_MAX_SEC=max(15,min(120,int(os.getenv("V11197_TICKER_CACHE_MAX_SEC","75"))))

_cache={
    "symbols":{"at":0.0,"value":None},
    "tickers":{"at":0.0,"value":None},
}
_stats={
    "exchangeinfo_live":0,"exchangeinfo_cache":0,
    "ticker_live":0,"ticker_cache":0,
    "last_exchangeinfo_error":"","last_ticker_error":"",
}


class MandatorySourceError(RuntimeError):
    pass


def _age(key):
    row=_cache[key]
    if row["value"] is None:
        return 1e18
    return max(0.0,time.time()-float(row["at"] or 0.0))


def _store(key,value):
    _cache[key]={"at":time.time(),"value":copy.deepcopy(value)}
    return value


def _cached(key,max_age):
    if _cache[key]["value"] is None or _age(key)>float(max_age):
        return None
    return copy.deepcopy(_cache[key]["value"])


def _governor_cooldown():
    try:
        return max(0.0,float(governor.status().get("cooldown_seconds",0) or 0))
    except Exception:
        return 0.0


async def _live_symbols():
    cooldown=_governor_cooldown()
    if cooldown>0:
        raise MandatorySourceError(f"exchangeInfo blocked by Binance rate-limit cooldown {cooldown:.0f}s")
    try:
        value=await asyncio.wait_for(market.get_symbols(),timeout=EXCHANGEINFO_TIMEOUT_SEC)
    except asyncio.TimeoutError as exc:
        raise MandatorySourceError(
            f"exchangeInfo timeout after {EXCHANGEINFO_TIMEOUT_SEC}s"
        ) from exc
    except Exception as exc:
        raise MandatorySourceError(
            f"exchangeInfo failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(value,list) or not value:
        raise MandatorySourceError("exchangeInfo returned empty perpetual universe")
    _stats["exchangeinfo_live"]+=1
    _stats["last_exchangeinfo_error"]=""
    return _store("symbols",value)


async def _live_tickers():
    cooldown=_governor_cooldown()
    if cooldown>0:
        raise MandatorySourceError(f"ticker/24hr blocked by Binance rate-limit cooldown {cooldown:.0f}s")
    try:
        value=await asyncio.wait_for(market.get_tickers(),timeout=TICKER_TIMEOUT_SEC)
    except asyncio.TimeoutError as exc:
        raise MandatorySourceError(
            f"ticker/24hr timeout after {TICKER_TIMEOUT_SEC}s"
        ) from exc
    except Exception as exc:
        raise MandatorySourceError(
            f"ticker/24hr failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(value,dict) or not value:
        raise MandatorySourceError("ticker/24hr returned empty market snapshot")
    _stats["ticker_live"]+=1
    _stats["last_ticker_error"]=""
    return _store("tickers",value)


async def symbols():
    try:
        value=await _live_symbols()
        return value,{"source":"LIVE","age_sec":0.0,"reason":""}
    except Exception as exc:
        _stats["last_exchangeinfo_error"]=str(exc)
        cached=_cached("symbols",EXCHANGEINFO_CACHE_MAX_SEC)
        if cached is None:
            raise
        _stats["exchangeinfo_cache"]+=1
        return cached,{
            "source":"CACHE",
            "age_sec":round(_age("symbols"),1),
            "reason":str(exc),
        }


async def tickers():
    try:
        value=await _live_tickers()
        return value,{"source":"LIVE","age_sec":0.0,"reason":""}
    except Exception as exc:
        _stats["last_ticker_error"]=str(exc)
        cached=_cached("tickers",TICKER_CACHE_MAX_SEC)
        if cached is None:
            raise
        _stats["ticker_cache"]+=1
        return cached,{
            "source":"CACHE",
            "age_sec":round(_age("tickers"),1),
            "reason":str(exc),
        }


async def mandatory_sources():
    # Independent tasks preserve the exact failing source instead of collapsing
    # both into a generic asyncio.TimeoutError.
    a=asyncio.create_task(symbols(),name="v11197-exchangeinfo")
    b=asyncio.create_task(tickers(),name="v11197-ticker24h")
    results=await asyncio.gather(a,b,return_exceptions=True)

    errors=[]
    if isinstance(results[0],Exception):
        errors.append(str(results[0]))
    if isinstance(results[1],Exception):
        errors.append(str(results[1]))
    if errors:
        raise MandatorySourceError("; ".join(errors))

    symbol_value,symbol_meta=results[0]
    ticker_value,ticker_meta=results[1]
    return symbol_value,ticker_value,{
        "exchangeInfo":symbol_meta,
        "ticker24h":ticker_meta,
        "governor_cooldown_sec":round(_governor_cooldown(),1),
    }


def status():
    return {
        **dict(_stats),
        "symbols_cache_age_sec":None if _cache["symbols"]["value"] is None else round(_age("symbols"),1),
        "tickers_cache_age_sec":None if _cache["tickers"]["value"] is None else round(_age("tickers"),1),
        "exchangeinfo_timeout_sec":EXCHANGEINFO_TIMEOUT_SEC,
        "ticker_timeout_sec":TICKER_TIMEOUT_SEC,
        "exchangeinfo_cache_max_sec":EXCHANGEINFO_CACHE_MAX_SEC,
        "ticker_cache_max_sec":TICKER_CACHE_MAX_SEC,
        "governor_cooldown_sec":round(_governor_cooldown(),1),
    }
