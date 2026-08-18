"""Public Binance Spot market-data layer for V11.8.1.

This module is deliberately separate from app.market (USD-M Futures). Spot
signals use Spot candles, Spot volume, Spot trades and Spot order books as the
source of truth. Only public endpoints are used; no account/trading API key is
required.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

import httpx
import pandas as pd

log=logging.getLogger(__name__)

BASE_URL="https://data-api.binance.vision"
USER_AGENT="Korkovts-Spot-Universe/11.8.1"
_client: httpx.AsyncClient|None=None
_sem=asyncio.Semaphore(8)
_cooldown_until=0.0
_cache={}

_INTERVAL_SECONDS={
    "1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,
    "1h":3600,"2h":7200,"4h":14400,"6h":21600,"8h":28800,
    "12h":43200,"1d":86400,"3d":259200,"1w":604800,
}

STABLE_BASES={
    "USDC","FDUSD","TUSD","USDP","DAI","USDE","USDS","PYUSD","USD1",
    "USDJ","GUSD","FRAX","LUSD","AEUR","EUR","TRY","BRL","GBP",
    "BIDR","IDRT","UAH","PLN","RON","ARS","MXN","ZAR","JPY","RUB",
}
_LEVERAGED_SUFFIXES=("UP","DOWN","BULL","BEAR")


def _is_leveraged_base(base):
    base=str(base or "").upper()
    # Avoid false positives such as JUP. Binance leveraged-token tickers append
    # the suffix to a non-trivial underlying (e.g. BTCUP / ETHDOWN); require at least 3 chars to avoid names such as JUP/SOUP.
    for suffix in _LEVERAGED_SUFFIXES:
        if base.endswith(suffix) and len(base)-len(suffix)>=3:
            return True
    return False

@dataclass(frozen=True)
class SpotMeta:
    symbol:str
    base_asset:str
    quote_asset:str
    status:str
    tick_size:float
    min_price:float
    max_price:float
    step_size:float
    min_qty:float
    min_notional:float


def _http_client():
    global _client
    if _client is None or _client.is_closed:
        _client=httpx.AsyncClient(
            timeout=httpx.Timeout(15,connect=8),
            limits=httpx.Limits(max_connections=20,max_keepalive_connections=12),
            headers={"User-Agent":USER_AGENT,"Accept":"application/json"},
            follow_redirects=True,
        )
    return _client


async def close():
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client=None




def _retry_after_seconds(headers,status):
    default=120.0 if int(status or 0)==418 else 2.0
    try:
        value=float((headers or {}).get("Retry-After",default) or default)
    except Exception:
        value=default
    if not (value>0):
        value=default
    # Binance documents escalating IP bans; keep a sanity ceiling but do not
    # truncate a legitimate multi-minute/hour Retry-After to 60 seconds.
    return min(value,3*86400.0)


async def _wait_cooldown():
    while True:
        delay=float(_cooldown_until)-time.time()
        if delay<=0:
            return
        await asyncio.sleep(min(delay,60.0))


async def _get(path,params=None):
    global _cooldown_until
    last=None
    for attempt in range(4):
        await _wait_cooldown()
        try:
            async with _sem:
                # Recheck after queueing: a sibling request may have received
                # 429/418 while this coroutine was waiting for the semaphore.
                await _wait_cooldown()
                r=await _http_client().get(BASE_URL+path,params=params)
            if r.status_code in (418,429):
                retry=_retry_after_seconds(r.headers,r.status_code)
                _cooldown_until=max(_cooldown_until,time.time()+retry)
                last=httpx.HTTPStatusError("Binance Spot rate limit",request=r.request,response=r)
                continue
            r.raise_for_status()
            # Spot limits are endpoint/rate-limit dependent. We only use the
            # header as telemetry and never assume a hard global numeric limit.
            used=r.headers.get("X-MBX-USED-WEIGHT-1M") or r.headers.get("x-mbx-used-weight-1m")
            if used:
                try:
                    if int(used)>1000:
                        log.info("Binance Spot request weight telemetry: %s",used)
                except ValueError:
                    pass
            return r.json()
        except (httpx.TimeoutException,httpx.NetworkError,httpx.HTTPStatusError) as exc:
            last=exc
            if isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code<500 and exc.response.status_code not in (418,429):
                raise
            if attempt<3:
                await asyncio.sleep(min(8,(2**attempt)+random.random()))
    raise last or RuntimeError("Binance Spot request failed")


def _ttl_cache_get(key):
    row=_cache.get(key)
    if row and row[0]>time.time():
        return row[1]
    return None


def _ttl_cache_put(key,value,ttl):
    _cache[key]=(time.time()+float(ttl),value)
    if len(_cache)>3500:
        for k,_ in sorted(_cache.items(),key=lambda item:item[1][0])[:500]:
            _cache.pop(k,None)
    return value


async def exchange_info(force=False):
    key=("exchange_info",)
    cached=None if force else _ttl_cache_get(key)
    if cached is not None:
        return cached
    payload=await _get("/api/v3/exchangeInfo")
    return _ttl_cache_put(key,payload,3600)


def _filters(row):
    fmap={f.get("filterType"):f for f in row.get("filters",[]) if isinstance(f,dict)}
    price=fmap.get("PRICE_FILTER",{})
    lot=fmap.get("LOT_SIZE",{})
    notional=fmap.get("NOTIONAL",{}) or fmap.get("MIN_NOTIONAL",{})
    return SpotMeta(
        symbol=str(row.get("symbol","")),
        base_asset=str(row.get("baseAsset","")),
        quote_asset=str(row.get("quoteAsset","")),
        status=str(row.get("status","")),
        tick_size=float(price.get("tickSize",0) or 0),
        min_price=float(price.get("minPrice",0) or 0),
        max_price=float(price.get("maxPrice",0) or 0),
        step_size=float(lot.get("stepSize",0) or 0),
        min_qty=float(lot.get("minQty",0) or 0),
        min_notional=float(notional.get("minNotional",0) or 0),
    )


async def universe():
    payload=await exchange_info()
    rows=[]
    for row in payload.get("symbols",[]):
        symbol=str(row.get("symbol","")).upper()
        base=str(row.get("baseAsset","")).upper()
        quote=str(row.get("quoteAsset","")).upper()
        if quote!="USDT" or row.get("status")!="TRADING":
            continue
        if row.get("isSpotTradingAllowed") is False:
            continue
        perms={str(x).upper() for x in row.get("permissions",[]) or []}
        if perms and "SPOT" not in perms:
            continue
        if base in STABLE_BASES or _is_leveraged_base(base):
            continue
        if not symbol or not base:
            continue
        rows.append(_filters(row))
    return rows


async def tickers_24h(force=False):
    key=("tickers24",)
    cached=None if force else _ttl_cache_get(key)
    if cached is not None:
        return cached
    payload=await _get("/api/v3/ticker/24hr")
    result={}
    for r in payload if isinstance(payload,list) else []:
        symbol=str(r.get("symbol","")).upper()
        if not symbol:
            continue
        try:
            result[symbol]={
                "last":float(r.get("lastPrice",0) or 0),
                "change_pct":float(r.get("priceChangePercent",0) or 0),
                "quote_volume":float(r.get("quoteVolume",0) or 0),
                "volume":float(r.get("volume",0) or 0),
                "trades":int(r.get("count",0) or 0),
                "high":float(r.get("highPrice",0) or 0),
                "low":float(r.get("lowPrice",0) or 0),
            }
        except (TypeError,ValueError):
            continue
    return _ttl_cache_put(key,result,30)


async def klines(symbol,interval,limit=300):
    symbol=str(symbol).upper(); interval=str(interval)
    limit=max(30,min(int(limit),1000))
    seconds=_INTERVAL_SECONDS.get(interval,3600)
    key=("klines",symbol,interval,limit)
    cached=_ttl_cache_get(key)
    if cached is not None:
        return cached.copy(deep=False)
    payload=await _get("/api/v3/klines",{"symbol":symbol,"interval":interval,"limit":limit})
    cols=[
        "open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore",
    ]
    df=pd.DataFrame(payload,columns=cols)
    if df.empty:
        return df
    for c in ("open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote"):
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["trades"]=pd.to_numeric(df["trades"],errors="coerce").fillna(0)
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["close_time"]=pd.to_datetime(df["close_time"],unit="ms",utc=True)
    df=df[df["close_time"]<pd.Timestamp.now(tz="UTC")].dropna(subset=["close"])
    # Reuse until just after the next UTC candle close. Daily/4H data therefore
    # stays cheap during repeated scans without hiding a newly closed bar.
    now=time.time()
    next_close=(int(now)//seconds+1)*seconds+3
    ttl=max(5,min(seconds/3,next_close-now))
    return _ttl_cache_put(key,df,ttl).copy(deep=False)


async def klines_range(symbol,interval,start_ms,end_ms=None,limit=1000):
    """Closed Spot candles in a bounded historical range (used by forward audit)."""
    symbol=str(symbol).upper(); interval=str(interval)
    params={
        "symbol":symbol,"interval":interval,
        "startTime":int(start_ms),"limit":max(1,min(int(limit),1000)),
    }
    if end_ms is not None:
        params["endTime"]=int(end_ms)
    payload=await _get("/api/v3/klines",params)
    cols=[
        "open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore",
    ]
    df=pd.DataFrame(payload,columns=cols)
    if df.empty:
        return df
    for c in ("open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote"):
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["trades"]=pd.to_numeric(df["trades"],errors="coerce").fillna(0)
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["close_time"]=pd.to_datetime(df["close_time"],unit="ms",utc=True)
    return df[df["close_time"]<pd.Timestamp.now(tz="UTC")].dropna(subset=["close"])


async def book_ticker(symbol):
    r=await _get("/api/v3/ticker/bookTicker",{"symbol":str(symbol).upper()})
    bid=float(r.get("bidPrice",0) or 0); ask=float(r.get("askPrice",0) or 0)
    return {
        "bid":bid,"ask":ask,
        "bid_qty":float(r.get("bidQty",0) or 0),
        "ask_qty":float(r.get("askQty",0) or 0),
        "fetched_at":time.time(),
    }


async def depth(symbol,limit=100):
    valid=(5,10,20,50,100,500,1000,5000)
    limit=min(valid,key=lambda x:abs(x-int(limit)))
    r=await _get("/api/v3/depth",{"symbol":str(symbol).upper(),"limit":limit})
    return {
        "lastUpdateId":int(r.get("lastUpdateId",0) or 0),
        "bids":[(float(p),float(q)) for p,q in (r.get("bids") or [])],
        "asks":[(float(p),float(q)) for p,q in (r.get("asks") or [])],
        "fetched_at":time.time(),
    }


async def agg_trades(symbol,limit=1000):
    rows=await _get("/api/v3/aggTrades",{"symbol":str(symbol).upper(),"limit":min(1000,max(50,int(limit)))})
    result=[]
    for r in rows if isinstance(rows,list) else []:
        try:
            price=float(r.get("p",0)); qty=float(r.get("q",0))
            result.append({
                "price":price,"qty":qty,"notional":price*qty,
                "time_ms":int(r.get("T",0) or 0),
                # m=True: buyer is maker -> seller was aggressive taker.
                "buyer_taker":not bool(r.get("m")),
            })
        except (TypeError,ValueError):
            continue
    return result


def request_status():
    return {
        "cache_entries":len(_cache),
        "cooldown_seconds":max(0.0,_cooldown_until-time.time()),
        "base_url":BASE_URL,
    }
