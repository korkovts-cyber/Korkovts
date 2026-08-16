import asyncio
from .config import (MAX_SYMBOLS_TO_SCAN,MIN_SIGNAL_SCORE,MIN_24H_QUOTE_VOLUME,
    SCAN_CONCURRENCY,DEEP_ANALYSIS_LIMIT)
from .market import get_symbols,get_klines,get_tickers,get_derivatives_snapshot
from .strategy import analyze
from .news import get_news_sentiment,for_symbol
from .db import calibration_penalty

async def technical_candidate(symbol,market_bias=None,semaphore=None,news=None,min_score=MIN_SIGNAL_SCORE):
    try:
        async with semaphore:
            lower,a,b=await asyncio.gather(get_klines(symbol,"15m",260),get_klines(symbol,"1h",350),
                get_klines(symbol,"4h",350))
        preliminary=analyze(symbol,"1H",a,b,max(60,min_score-15),lower,market_bias,None,
                            for_symbol(news or {},symbol))
        return (symbol,lower,a,b,preliminary) if preliminary else None
    except Exception:
        return None

async def deep_candidate(candidate,market_bias,semaphore,news,min_score=MIN_SIGNAL_SCORE):
    symbol,lower,a,b,preliminary=candidate
    try:
        async with semaphore:
            d=await asyncio.wait_for(get_derivatives_snapshot(symbol),timeout=18)
        learned_penalty=calibration_penalty(symbol,preliminary.side)
        return analyze(symbol,"1H",a,b,min(95,min_score+learned_penalty),lower,market_bias,d,for_symbol(news,symbol))
    except Exception:
        return None

async def market_state():
    a,b=await asyncio.gather(get_klines("BTCUSDT","1h",260),get_klines("BTCUSDT","4h",260))
    from .indicators import enrich
    x=enrich(a).iloc[-1]; h=enrich(b).iloc[-1]
    bias="NEUTRAL"
    if x.close>x.ema200 and x.ema20>x.ema50 and h.close>h.ema200: bias="LONG"
    if x.close<x.ema200 and x.ema20<x.ema50 and h.close<h.ema200: bias="SHORT"
    atr_pct=float(x.atr_pct); adjustment=0; label="нормальный"
    if atr_pct>=1.5: adjustment=6; label="повышенная волатильность"
    elif atr_pct<=0.30 or (float(x.adx)<16 and bias=="NEUTRAL"): adjustment=4; label="флэт/низкий импульс"
    elif bias=="NEUTRAL": adjustment=2; label="неопределённый тренд"
    return {"bias":bias,"score_adjustment":adjustment,"label":label,"btc_atr_pct":atr_pct}

async def market_regime():
    return (await market_state())["bias"]

async def scan():
    symbols,tickers,state,news=await asyncio.gather(get_symbols(),get_tickers(),market_state(),get_news_sentiment())
    bias=state["bias"]; min_score=min(92,MIN_SIGNAL_SCORE+state["score_adjustment"])
    symbols=[s for s in symbols if tickers.get(s,{}).get("quote_volume",0)>=MIN_24H_QUOTE_VOLUME]
    symbols.sort(key=lambda s:tickers[s]["quote_volume"],reverse=True)
    if MAX_SYMBOLS_TO_SCAN>0: symbols=symbols[:MAX_SYMBOLS_TO_SCAN]
    semaphore=asyncio.Semaphore(SCAN_CONCURRENCY)
    candidates=await asyncio.gather(*(technical_candidate(s,bias,semaphore,news,min_score) for s in symbols))
    candidates=[x for x in candidates if x]
    candidates.sort(key=lambda x:x[4].score,reverse=True)
    candidates=candidates[:DEEP_ANALYSIS_LIMIT]
    results=await asyncio.gather(*(deep_candidate(x,bias,semaphore,news,min_score) for x in candidates))
    return sorted([x for x in results if x],key=lambda x:x.score,reverse=True)
