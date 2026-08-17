import asyncio
import logging
import random
import time

import httpx
import pandas as pd

from .config import ADL_MAX_AGE_MINUTES, BINANCE_BASE_URL

log=logging.getLogger(__name__)
_client=None
_kline_cache={}
_INTERVAL_SECONDS={"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,
                   "1h":3600,"2h":7200,"4h":14400,"6h":21600,"8h":28800,
                   "12h":43200,"1d":86400}

def _normalize_adl_risk(value):
    """Translate Binance's public ADL labels to the strategy vocabulary."""
    risk=str(value or "unknown").strip().lower()
    return "medium" if risk in ("middle","moderate") else risk

def _normalize_adl_row(value):
    """Keep an unexpected scalar ADL response from breaking a whole scan."""
    row=dict(value) if isinstance(value,dict) else {"risk":value}
    row["risk"]=_normalize_adl_risk(row.get("risk","unknown"))
    row.setdefault("fresh",False)
    row.setdefault("age_minutes",9999)
    return row

def _http_client():
    global _client
    if _client is None or _client.is_closed:
        _client=httpx.AsyncClient(
            timeout=httpx.Timeout(25,connect=10),
            limits=httpx.Limits(max_connections=32,max_keepalive_connections=16),
            headers={"User-Agent":"Korkovts-Signal-AI/10R"})
    return _client

async def close_http_client():
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client=None

async def _get(path,params=None):
    last=None
    for attempt in range(3):
        try:
            r=await _http_client().get(f"{BINANCE_BASE_URL}{path}",params=params)
            if r.status_code in (418,429):
                await asyncio.sleep(min(15,float(r.headers.get("Retry-After",1))*(attempt+1)))
                last=httpx.HTTPStatusError("Binance rate limit",request=r.request,response=r); continue
            r.raise_for_status()
            used=int(r.headers.get("x-mbx-used-weight-1m",0) or 0)
            if used>=2100:
                log.warning("Binance request weight is high: %s/2400",used)
                await asyncio.sleep(2)
            return r.json()
        except (httpx.TimeoutException,httpx.NetworkError,httpx.HTTPStatusError) as e:
            last=e
            if isinstance(e,httpx.HTTPStatusError) and e.response.status_code<500: raise
            if attempt<2: await asyncio.sleep((2**attempt)+random.random())
    raise last or RuntimeError("Binance request failed")

async def get_klines(symbol, interval, limit=500):
    symbol=symbol.upper(); limit=min(limit,1500); now=time.time()
    key=(symbol,interval,limit)
    cached=_kline_cache.get(key)
    if cached and cached[0]>now:
        return cached[1].copy(deep=False)
    data=await _get("/fapi/v1/klines",{"symbol":symbol,"interval":interval,"limit":limit})
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df=pd.DataFrame(data,columns=cols)
    for c in ["open","high","low","close","volume","taker_buy_base"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["close_time"]=pd.to_datetime(df["close_time"],unit="ms",utc=True)
    df=df[df.close_time<pd.Timestamp.now(tz="UTC")]
    result=df[["open_time","open","high","low","close","volume","taker_buy_base"]]
    seconds=_INTERVAL_SECONDS.get(interval,60)
    # Reuse only until the next UTC candle can have closed. The small grace
    # period avoids a burst at the exact boundary while never skipping a full
    # newly closed bar.
    expires=(int(now)//seconds+1)*seconds+3
    _kline_cache[key]=(expires,result)
    if len(_kline_cache)>1500:
        for old_key,_ in sorted(_kline_cache.items(),key=lambda item:item[1][0])[:250]:
            _kline_cache.pop(old_key,None)
    return result.copy(deep=False)

def kline_cache_status():
    now=time.time()
    return {"entries":len(_kline_cache),
            "fresh":sum(1 for expires,_ in _kline_cache.values() if expires>now)}

async def get_klines_since(symbol,interval,start_ms,limit=1500):
    data=[]; cursor=int(start_ms); page_limit=min(limit,1500)
    # A 48-hour signal contains up to 2880 one-minute bars. Paginate only when
    # the service was offline long enough for one Binance page to be insufficient.
    for _ in range(4):
        page=await _get("/fapi/v1/klines",{"symbol":symbol.upper(),"interval":interval,
            "startTime":cursor,"limit":page_limit})
        if not page: break
        data.extend(page)
        if len(page)<page_limit: break
        next_cursor=int(page[-1][6])+1
        if next_cursor<=cursor: break
        cursor=next_cursor
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df=pd.DataFrame(data,columns=cols)
    if df.empty: return df
    for c in ["open","high","low","close","volume","taker_buy_base"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["close_time"]=pd.to_datetime(df["close_time"],unit="ms",utc=True)
    df=df.drop_duplicates("open_time").sort_values("open_time")
    return df[df.close_time<pd.Timestamp.now(tz="UTC")][["open_time","close_time","open","high","low","close","volume","taker_buy_base"]]

async def get_symbols():
    data=await _get("/fapi/v1/exchangeInfo")
    return [s["symbol"] for s in data["symbols"]
            if s.get("quoteAsset")=="USDT" and s.get("status")=="TRADING"
            and s.get("contractType")=="PERPETUAL"]

async def get_tickers():
    rows=await _get("/fapi/v1/ticker/24hr")
    return {r["symbol"]:{"price":float(r["lastPrice"]),"change":float(r["priceChangePercent"]),
                         "quote_volume":float(r["quoteVolume"])} for r in rows}

async def get_adl_risks(symbol=None):
    """Return Binance's symbol-level ADL risk map.

    The endpoint is public and accepts an optional symbol.  Fetching the whole
    map once per scan avoids adding one request for every deep candidate.
    """
    params={"symbol":symbol} if symbol else None
    payload=await _get("/fapi/v1/symbolAdlRisk",params)
    rows=payload if isinstance(payload,list) else [payload]
    result={}
    for row in rows:
        if not isinstance(row,dict) or not row.get("symbol"):
            continue
        update_ms=int(row.get("updateTime",0) or 0)
        age_min=max(0.0,(time.time()*1000-update_ms)/60000) if update_ms else 9999.0
        result[str(row["symbol"])]= {
            "risk":_normalize_adl_risk(row.get("adlRisk","unknown")),
            "update_time":update_ms,
            "age_minutes":age_min,
            "fresh":age_min<=ADL_MAX_AGE_MINUTES,
        }
    return result

async def get_derivatives_snapshot(symbol,adl=None):
    names=("premium","oi","oi_hist","taker","global_ls","top_pos","depth","basis_hist","adl")
    adl_request=(get_adl_risks(symbol) if adl is None
                 else asyncio.sleep(0,result={symbol:adl}))
    values=await asyncio.gather(
        _get("/fapi/v1/premiumIndex",{"symbol":symbol}),
        _get("/fapi/v1/openInterest",{"symbol":symbol}),
        _get("/futures/data/openInterestHist",{"symbol":symbol,"period":"15m","limit":13}),
        _get("/futures/data/takerlongshortRatio",{"symbol":symbol,"period":"15m","limit":6}),
        _get("/futures/data/globalLongShortAccountRatio",{"symbol":symbol,"period":"15m","limit":3}),
        _get("/futures/data/topLongShortPositionRatio",{"symbol":symbol,"period":"15m","limit":6}),
        _get("/fapi/v1/depth",{"symbol":symbol,"limit":20}),
        _get("/futures/data/basis",{"pair":symbol,"contractType":"PERPETUAL","period":"1h","limit":25}),
        adl_request,return_exceptions=True)
    data={name:(None if isinstance(value,Exception) else value) for name,value in zip(names,values)}
    # Binance occasionally answers a data endpoint with a JSON scalar/object
    # while still returning HTTP 200.  Treat that one component as unavailable
    # instead of iterating it as a list and aborting every deep candidate.
    premium=data["premium"] if isinstance(data["premium"],dict) else {}
    oi=data["oi"] if isinstance(data["oi"],dict) else {}
    oi_hist=data["oi_hist"] if isinstance(data["oi_hist"],list) else []
    taker=data["taker"] if isinstance(data["taker"],list) else []
    global_ls=data["global_ls"] if isinstance(data["global_ls"],list) else []
    top_pos=data["top_pos"] if isinstance(data["top_pos"],list) else []
    depth=data["depth"] if isinstance(data["depth"],dict) else {}
    basis_hist=data["basis_hist"] if isinstance(data["basis_hist"],list) else []
    adl_map=data["adl"] or {}
    adl_value=adl_map.get(symbol,{}) if isinstance(adl_map,dict) else {}
    adl_row=_normalize_adl_row(adl_value)
    available={
        "premium":bool(premium.get("markPrice")) and bool(premium.get("indexPrice")),
        "oi":bool(oi.get("openInterest")),
        "oi_hist":len(oi_hist)>=2,
        "taker":len(taker)>=3,
        "global_ls":len(global_ls)>=1,
        "top_pos":len(top_pos)>=1,
        "depth":bool(depth.get("bids")) and bool(depth.get("asks")),
        "basis_hist":len(basis_hist)>=2,
        "adl":isinstance(adl_row,dict) and str(adl_row.get("risk","unknown")).lower()!="unknown",
    }
    missing=[name for name,is_available in available.items() if not is_available]
    oi_values=[float(x.get("sumOpenInterestValue",0)) for x in oi_hist]
    oi_change=((oi_values[-1]/oi_values[0]-1)*100) if len(oi_values)>1 and oi_values[0] else 0
    taker_ratios=[float(x.get("buySellRatio",1)) for x in taker]
    top_position_ratios=[float(x.get("longShortRatio",1)) for x in top_pos]
    top_position_change_pct=(
        (top_position_ratios[-1]/top_position_ratios[0]-1)*100
        if len(top_position_ratios)>1 and top_position_ratios[0] else 0
    )
    bid_rows=depth.get("bids") or []; ask_rows=depth.get("asks") or []
    bids=sum(float(p)*float(q) for p,q in bid_rows)
    asks=sum(float(p)*float(q) for p,q in ask_rows)
    imbalance=(bids-asks)/(bids+asks) if bids+asks else 0
    best_bid=float(bid_rows[0][0]) if bid_rows else 0
    best_ask=float(ask_rows[0][0]) if ask_rows else 0
    mid=(best_bid+best_ask)/2
    index_price=float(premium.get("indexPrice",0)); mark=float(premium.get("markPrice",0))
    basis_rates=[float(row.get("basisRate",0) or 0) for row in basis_hist]
    basis_rate=basis_rates[-1] if basis_rates else ((mark-index_price)/index_price if index_price else 0)
    basis_change_24h_bps=(basis_rates[-1]-basis_rates[0])*10000 if len(basis_rates)>1 else 0
    quality=sum(available.values())
    # Taker flow, crowding and top-position ratios are mandatory because the
    # final strategy actively gates on them instead of treating them as telemetry.
    core_ok=all(available[name] for name in
                ("premium","oi","oi_hist","taker","global_ls","top_pos","depth","adl"))
    adl_risk=_normalize_adl_risk(adl_row.get("risk","unknown"))
    adl_fresh=bool(adl_row.get("fresh",False))
    return {"funding":float(premium.get("lastFundingRate",0)),"mark_price":mark,
            "open_interest":float(oi.get("openInterest",0)),"oi_change_pct":oi_change,
            "taker_ratio":sum(taker_ratios[-3:])/min(3,len(taker_ratios)) if taker_ratios else 1,
            "global_ls":float(global_ls[-1].get("longShortRatio",1)) if global_ls else 1,
            "top_position_ls":float(top_pos[-1].get("longShortRatio",1)) if top_pos else 1,
            "top_position_change_pct":top_position_change_pct,
            "book_imbalance":imbalance,"spread_bps":((best_ask-best_bid)/mid*10000) if mid else 999,
            "basis_bps":basis_rate*10000,"basis_change_24h_bps":basis_change_24h_bps,
            "adl_risk":adl_risk,"adl_age_minutes":float(adl_row.get("age_minutes",9999)),
            "adl_fresh":adl_fresh,
            "deep_data":core_ok and quality>=8,"data_quality":quality,"data_quality_total":len(names),
            "missing":missing}

async def get_prices(symbols):
    tickers=await get_tickers()
    return {s:tickers.get(s) for s in symbols if tickers.get(s)}
