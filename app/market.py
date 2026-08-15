import httpx
import pandas as pd
from .config import BINANCE_BASE_URL

async def _get(path,params=None):
    async with httpx.AsyncClient(timeout=25) as c:
        r=await c.get(f"{BINANCE_BASE_URL}{path}",params=params)
        r.raise_for_status()
        return r.json()

async def get_klines(symbol, interval, limit=500):
    data=await _get("/fapi/v1/klines",{"symbol":symbol.upper(),"interval":interval,"limit":min(limit,1500)})
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df=pd.DataFrame(data,columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["close_time"]=pd.to_datetime(df["close_time"],unit="ms",utc=True)
    df=df[df.close_time<pd.Timestamp.now(tz="UTC")]
    return df[["open_time","open","high","low","close","volume"]]

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
    premium,oi=await __import__('asyncio').gather(
        _get("/fapi/v1/premiumIndex",{"symbol":symbol}),
        _get("/fapi/v1/openInterest",{"symbol":symbol}))
    return {"funding":float(premium.get("lastFundingRate",0)),
            "mark_price":float(premium.get("markPrice",0)),
            "open_interest":float(oi.get("openInterest",0))}

async def get_prices(symbols):
    tickers=await get_tickers()
    return {s:tickers.get(s) for s in symbols if tickers.get(s)}
