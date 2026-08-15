import asyncio
from .config import MAX_SYMBOLS_TO_SCAN,MIN_SIGNAL_SCORE,MIN_24H_QUOTE_VOLUME,SCAN_CONCURRENCY
from .market import get_symbols,get_klines,get_tickers,get_derivatives_snapshot
from .strategy import analyze

async def one(symbol,market_bias=None,semaphore=None):
    try:
        async with semaphore:
            lower,a,b,d=await asyncio.gather(get_klines(symbol,"15m",260),get_klines(symbol,"1h",350),
                get_klines(symbol,"4h",350),get_derivatives_snapshot(symbol))
        return analyze(symbol,"1H",a,b,MIN_SIGNAL_SCORE,lower,market_bias,d)
    except Exception:
        return None

async def market_regime():
    a,b=await asyncio.gather(get_klines("BTCUSDT","1h",260),get_klines("BTCUSDT","4h",260))
    from .indicators import enrich
    x=enrich(a).iloc[-1]; h=enrich(b).iloc[-1]
    if x.close>x.ema200 and x.ema20>x.ema50 and h.close>h.ema200: return "LONG"
    if x.close<x.ema200 and x.ema20<x.ema50 and h.close<h.ema200: return "SHORT"
    return "NEUTRAL"

async def scan():
    symbols,tickers,bias=await asyncio.gather(get_symbols(),get_tickers(),market_regime())
    symbols=[s for s in symbols if tickers.get(s,{}).get("quote_volume",0)>=MIN_24H_QUOTE_VOLUME]
    symbols.sort(key=lambda s:tickers[s]["quote_volume"],reverse=True)
    if MAX_SYMBOLS_TO_SCAN>0: symbols=symbols[:MAX_SYMBOLS_TO_SCAN]
    semaphore=asyncio.Semaphore(SCAN_CONCURRENCY)
    results=await asyncio.gather(*(one(s,bias,semaphore) for s in symbols))
    return sorted([x for x in results if x],key=lambda x:x.score,reverse=True)
