import httpx
import pandas as pd
from .config import BINANCE_BASE_URL

async def get_klines(symbol, interval, limit=500):
    rurl=f"{BINANCE_BASE_URL}/fapi/v1/klines"
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.get(rurl, params={"symbol":symbol.upper(),"interval":interval,"limit":min(limit,1500)})
        r.raise_for_status()
        data=r.json()
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df=pd.DataFrame(data,columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    return df[["open_time","open","high","low","close","volume"]]

async def get_symbols():
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.get(f"{BINANCE_BASE_URL}/fapi/v1/exchangeInfo")
        r.raise_for_status()
        data=r.json()
    return [s["symbol"] for s in data["symbols"]
            if s.get("quoteAsset")=="USDT" and s.get("status")=="TRADING"]
