import asyncio
import random
import httpx
import pandas as pd
from .config import BINANCE_BASE_URL

async def _get(path,params=None):
    last=None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r=await c.get(f"{BINANCE_BASE_URL}{path}",params=params)
            if r.status_code==429:
                await asyncio.sleep(min(8,float(r.headers.get("Retry-After",1))*(attempt+1)))
                last=httpx.HTTPStatusError("Binance rate limit",request=r.request,response=r); continue
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException,httpx.NetworkError,httpx.HTTPStatusError) as e:
            last=e
            if isinstance(e,httpx.HTTPStatusError) and e.response.status_code<500: raise
            if attempt<2: await asyncio.sleep((2**attempt)+random.random())
    raise last

async def get_klines(symbol, interval, limit=500):
    data=await _get("/fapi/v1/klines",{"symbol":symbol.upper(),"interval":interval,"limit":min(limit,1500)})
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df=pd.DataFrame(data,columns=cols)
    for c in ["open","high","low","close","volume","taker_buy_base"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["close_time"]=pd.to_datetime(df["close_time"],unit="ms",utc=True)
    df=df[df.close_time<pd.Timestamp.now(tz="UTC")]
    return df[["open_time","open","high","low","close","volume","taker_buy_base"]]

async def get_klines_since(symbol,interval,start_ms,limit=1500):
    data=await _get("/fapi/v1/klines",{"symbol":symbol.upper(),"interval":interval,
        "startTime":int(start_ms),"limit":min(limit,1500)})
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df=pd.DataFrame(data,columns=cols)
    if df.empty: return df
    for c in ["open","high","low","close","volume","taker_buy_base"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["close_time"]=pd.to_datetime(df["close_time"],unit="ms",utc=True)
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

async def get_derivatives_snapshot(symbol):
    premium,oi,oi_hist,taker,global_ls,top_pos,depth=await __import__('asyncio').gather(
        _get("/fapi/v1/premiumIndex",{"symbol":symbol}),
        _get("/fapi/v1/openInterest",{"symbol":symbol}),
        _get("/futures/data/openInterestHist",{"symbol":symbol,"period":"15m","limit":12}),
        _get("/futures/data/takerlongshortRatio",{"symbol":symbol,"period":"15m","limit":6}),
        _get("/futures/data/globalLongShortAccountRatio",{"symbol":symbol,"period":"15m","limit":3}),
        _get("/futures/data/topLongShortPositionRatio",{"symbol":symbol,"period":"15m","limit":3}),
        _get("/fapi/v1/depth",{"symbol":symbol,"limit":20}))
    oi_values=[float(x.get("sumOpenInterestValue",0)) for x in oi_hist]
    oi_change=((oi_values[-1]/oi_values[0]-1)*100) if len(oi_values)>1 and oi_values[0] else 0
    taker_ratios=[float(x.get("buySellRatio",1)) for x in taker]
    bids=sum(float(p)*float(q) for p,q in depth.get("bids",[]))
    asks=sum(float(p)*float(q) for p,q in depth.get("asks",[]))
    imbalance=(bids-asks)/(bids+asks) if bids+asks else 0
    best_bid=float(depth.get("bids",[[0]])[0][0]); best_ask=float(depth.get("asks",[[0]])[0][0])
    mid=(best_bid+best_ask)/2
    index_price=float(premium.get("indexPrice",0)); mark=float(premium.get("markPrice",0))
    return {"funding":float(premium.get("lastFundingRate",0)),"mark_price":mark,
            "open_interest":float(oi.get("openInterest",0)),"oi_change_pct":oi_change,
            "taker_ratio":sum(taker_ratios[-3:])/min(3,len(taker_ratios)) if taker_ratios else 1,
            "global_ls":float(global_ls[-1].get("longShortRatio",1)) if global_ls else 1,
            "top_position_ls":float(top_pos[-1].get("longShortRatio",1)) if top_pos else 1,
            "book_imbalance":imbalance,"spread_bps":((best_ask-best_bid)/mid*10000) if mid else 999,
            "basis_bps":((mark-index_price)/index_price*10000) if index_price else 0,"deep_data":True}

async def get_prices(symbols):
    tickers=await get_tickers()
    return {s:tickers.get(s) for s in symbols if tickers.get(s)}
