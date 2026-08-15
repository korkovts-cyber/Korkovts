import asyncio
from .config import MAX_SYMBOLS_TO_SCAN,MIN_SIGNAL_SCORE
from .market import get_symbols,get_klines
from .strategy import analyze

async def one(symbol):
    try:
        a,b=await asyncio.gather(get_klines(symbol,"1h",350),get_klines(symbol,"4h",350))
        return analyze(symbol,"1H",a,b,MIN_SIGNAL_SCORE)
    except Exception:
        return None

async def scan():
    symbols=(await get_symbols())[:MAX_SYMBOLS_TO_SCAN]
    results=await asyncio.gather(*(one(s) for s in symbols))
    return sorted([x for x in results if x],key=lambda x:x.score,reverse=True)
