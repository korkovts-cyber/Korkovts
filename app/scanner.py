import asyncio
import logging
from .config import (MAX_SYMBOLS_TO_SCAN,MIN_SIGNAL_SCORE,MIN_24H_QUOTE_VOLUME,
    SCAN_CONCURRENCY,DEEP_ANALYSIS_LIMIT)
from .market import get_symbols,get_klines,get_tickers,get_derivatives_snapshot,get_adl_risks
from .strategy import analyze
from .news import get_news_sentiment,for_symbol
from .db import calibration_penalty,save_shadow,was_shadowed_recently
from .liquidations import snapshot as liquidation_snapshot
from .research import (annotate_correlation_clusters,breadth_is_extreme_against,
                       market_breadth)

log=logging.getLogger(__name__)

def _adl_shadow_reason(derivatives):
    risk=str(derivatives.get("adl_risk","unknown")).upper()
    return f"ADL_{risk}" if derivatives.get("adl_fresh") else "ADL_STALE"

def _store_shadows(rows):
    """Persist silent counterfactuals without touching Telegram deduplication."""
    stored=0
    for signal,reason in rows:
        if signal and not was_shadowed_recently(signal.symbol,signal.side,signal.timeframe,reason,24):
            save_shadow(signal,reason); stored+=1
    if stored:
        log.info("stored %s silent shadow candidates",stored)

async def technical_candidate(symbol,market_context=None,semaphore=None,news=None,min_score=MIN_SIGNAL_SCORE):
    try:
        async with semaphore:
            lower,a,b=await asyncio.gather(get_klines(symbol,"15m",260),get_klines(symbol,"1h",350),
                get_klines(symbol,"4h",350))
        market_bias=(market_context or {}).get("bias")
        preliminary=analyze(symbol,"1H",a,b,max(60,min_score-15),lower,market_bias,None,
                            for_symbol(news or {},symbol),market_context)
        return (symbol,lower,a,b,preliminary) if preliminary else None
    except Exception as exc:
        log.debug("technical prefilter failed for %s: %s",symbol,exc)
        return None

async def deep_candidate(candidate,market_context,semaphore,news,adl_risks,min_score=MIN_SIGNAL_SCORE):
    symbol,lower,a,b,preliminary=candidate
    try:
        async with semaphore:
            adl=adl_risks.get(symbol,{"risk":"unknown","fresh":False,"age_minutes":9999})
            d=await asyncio.wait_for(get_derivatives_snapshot(symbol,adl),timeout=18)
        if not d.get("deep_data"):
            log.info("skip %s: incomplete derivatives snapshot (%s/%s; missing %s)",
                     symbol,d.get("data_quality",0),d.get("data_quality_total",9),
                     ",".join(d.get("missing",[])))
            return None,None,None
        oi_notional=float(d.get("open_interest",0))*float(d.get("mark_price",0))
        d.update(liquidation_snapshot(symbol,oi_notional))
        penalty=calibration_penalty(symbol,preliminary.side,"1H")
        threshold=min(95,min_score+penalty)
        result=analyze(symbol,"1H",a,b,threshold,lower,market_context.get("bias"),d,
                       for_symbol(news,symbol),market_context)
        shadow=reason=None
        if result is None and (str(d.get("adl_risk","unknown")).lower()!="low" or not d.get("adl_fresh")):
            baseline=dict(d); baseline.update(adl_risk="low",adl_fresh=True,adl_age_minutes=0)
            shadow=analyze(symbol,"1H",a,b,threshold,lower,market_context.get("bias"),baseline,
                           for_symbol(news,symbol),market_context)
            reason=_adl_shadow_reason(d) if shadow else None
        return result,shadow,reason
    except Exception as exc:
        log.debug("deep analysis failed for %s: %s",symbol,exc)
        return None,None,None

async def short_technical_candidate(symbol,market_context=None,semaphore=None,news=None,min_score=MIN_SIGNAL_SCORE):
    """Fast setup: 5m entry, 15m setup, 1h trend confirmation."""
    try:
        async with semaphore:
            lower,base,higher=await asyncio.gather(
                get_klines(symbol,"5m",300),get_klines(symbol,"15m",350),get_klines(symbol,"1h",350))
        market_bias=(market_context or {}).get("bias")
        preliminary=analyze(symbol,"15M",base,higher,max(65,min_score-12),lower,market_bias,None,
                            for_symbol(news or {},symbol),market_context)
        return (symbol,lower,base,higher,preliminary) if preliminary else None
    except Exception as exc:
        log.debug("short technical prefilter failed for %s: %s",symbol,exc)
        return None

async def short_deep_candidate(candidate,market_context,semaphore,news,adl_risks,min_score=MIN_SIGNAL_SCORE):
    symbol,lower,base,higher,preliminary=candidate
    try:
        async with semaphore:
            adl=adl_risks.get(symbol,{"risk":"unknown","fresh":False,"age_minutes":9999})
            derivatives=await asyncio.wait_for(get_derivatives_snapshot(symbol,adl),timeout=18)
        if not derivatives.get("deep_data"):
            log.info("skip short %s: incomplete derivatives snapshot (%s/%s)",
                     symbol,derivatives.get("data_quality",0),derivatives.get("data_quality_total",9))
            return None,None,None
        # Short-term entries must clear a stricter score than swing entries.
        oi_notional=float(derivatives.get("open_interest",0))*float(derivatives.get("mark_price",0))
        derivatives.update(liquidation_snapshot(symbol,oi_notional))
        penalty=calibration_penalty(symbol,preliminary.side,"15M")
        threshold=min(95,min_score+4+penalty)
        result=analyze(symbol,"15M",base,higher,threshold,lower,market_context.get("bias"),
                       derivatives,for_symbol(news,symbol),market_context)
        shadow=reason=None
        if result is None and (str(derivatives.get("adl_risk","unknown")).lower()!="low"
                               or not derivatives.get("adl_fresh")):
            baseline=dict(derivatives); baseline.update(adl_risk="low",adl_fresh=True,adl_age_minutes=0)
            shadow=analyze(symbol,"15M",base,higher,threshold,lower,market_context.get("bias"),
                           baseline,for_symbol(news,symbol),market_context)
            reason=_adl_shadow_reason(derivatives) if shadow else None
        if result:
            result.expected_window="30 минут–4 часа"
        if shadow:
            shadow.expected_window="30 минут–4 часа"
        return result,shadow,reason
    except Exception as exc:
        log.debug("short deep analysis failed for %s: %s",symbol,exc)
        return None,None,None

async def market_state(tickers=None):
    if tickers is None:
        a,b,tickers=await asyncio.gather(get_klines("BTCUSDT","1h",260),
                                         get_klines("BTCUSDT","4h",260),get_tickers())
    else:
        a,b=await asyncio.gather(get_klines("BTCUSDT","1h",260),get_klines("BTCUSDT","4h",260))
    from .indicators import enrich
    base=enrich(a); higher=enrich(b)
    x=base.iloc[-1]; h=higher.iloc[-1]; hp=higher.iloc[-2]
    bias="NEUTRAL"
    # Regime persistence filter: the 4H state must already have been aligned
    # on the previous closed candle, not merely flip on the latest bar.
    if (x.close>x.ema200 and x.ema20>x.ema50 and x.supertrend_dir>0 and x.adx>=18
            and h.close>h.ema200 and h.ema20>h.ema50 and h.supertrend_dir>0
            and hp.close>hp.ema200 and hp.ema20>hp.ema50):
        bias="LONG"
    if (x.close<x.ema200 and x.ema20<x.ema50 and x.supertrend_dir<0 and x.adx>=18
            and h.close<h.ema200 and h.ema20<h.ema50 and h.supertrend_dir<0
            and hp.close<hp.ema200 and hp.ema20<hp.ema50):
        bias="SHORT"
    raw_bias=bias
    breadth=market_breadth(tickers)
    breadth_blocked=breadth_is_extreme_against(bias,breadth)
    if breadth_blocked:
        bias="NEUTRAL"
    atr_pct=float(x.atr_pct); adjustment=0; label="нормальный"
    if atr_pct>=1.5: adjustment=6; label="повышенная волатильность"
    elif atr_pct<=0.30 or (float(x.adx)<16 and raw_bias=="NEUTRAL"): adjustment=4; label="флэт/низкий импульс"
    elif raw_bias=="NEUTRAL": adjustment=2; label="неопределённый тренд"
    base_adjustment=adjustment
    if breadth_blocked:
        adjustment=max(adjustment,6); label="ширина рынка резко против режима BTC"
    return {"bias":bias,"btc_bias_raw":raw_bias,"score_adjustment":adjustment,
            "base_score_adjustment":base_adjustment,"label":label,
            "btc_atr_pct":atr_pct,"breadth":breadth,"breadth_blocked":breadth_blocked}

async def market_regime():
    return (await market_state())["bias"]

async def scan():
    symbols,tickers,news,adl_risks=await asyncio.gather(
        get_symbols(),get_tickers(),get_news_sentiment(),get_adl_risks())
    state=await market_state(tickers)
    bias=state["bias"]
    if state["btc_bias_raw"]=="NEUTRAL":
        log.info("main scan skipped: BTC regime is neutral")
        return []
    breadth_shadow=bool(state.get("breadth_blocked"))
    analysis_state=dict(state)
    if breadth_shadow:
        analysis_state["bias"]=state["btc_bias_raw"]
        analysis_state["label"]="теневая проверка экстремальной ширины рынка"
    min_score=min(92,MIN_SIGNAL_SCORE+(state["base_score_adjustment"] if breadth_shadow
                                      else state["score_adjustment"]))
    if int(news.get("sources",0))<1:
        log.warning("main scan skipped: news-risk sources unavailable")
        return []
    symbols=[s for s in symbols if tickers.get(s,{}).get("quote_volume",0)>=MIN_24H_QUOTE_VOLUME]
    symbols.sort(key=lambda s:tickers[s]["quote_volume"],reverse=True)
    if MAX_SYMBOLS_TO_SCAN>0: symbols=symbols[:MAX_SYMBOLS_TO_SCAN]
    semaphore=asyncio.Semaphore(SCAN_CONCURRENCY)
    scanned=len(symbols)
    candidates=await asyncio.gather(*(technical_candidate(s,analysis_state,semaphore,news,min_score) for s in symbols))
    candidates=[x for x in candidates if x]
    candidates.sort(key=lambda x:x[4].score,reverse=True)
    if DEEP_ANALYSIS_LIMIT>0:
        candidates=candidates[:DEEP_ANALYSIS_LIMIT]
    deep_count=len(candidates)
    frames={candidate[0]:candidate[2] for candidate in candidates}
    results=await asyncio.gather(*(deep_candidate(x,analysis_state,semaphore,news,adl_risks,min_score) for x in candidates))
    live=[row[0] for row in results if row and row[0]]
    if breadth_shadow:
        _store_shadows([(signal,"BREADTH_EXTREME") for signal in live])
        final=[]
    else:
        _store_shadows([(row[1],row[2]) for row in results if row and row[1] and row[2]])
        final=sorted(live,key=lambda x:x.score,reverse=True)
    annotate_correlation_clusters(final,frames)
    log.info("main scan: liquid=%s prefiltered=%s final=%s regime=%s threshold=%s",
             scanned,deep_count,len(final),bias,min_score)
    return final

async def scan_short():
    symbols,tickers,news,adl_risks=await asyncio.gather(
        get_symbols(),get_tickers(),get_news_sentiment(),get_adl_risks())
    state=await market_state(tickers)
    bias=state["bias"]
    if state["btc_bias_raw"]=="NEUTRAL":
        log.info("short scan skipped: BTC regime is neutral")
        return []
    breadth_shadow=bool(state.get("breadth_blocked"))
    analysis_state=dict(state)
    if breadth_shadow:
        analysis_state["bias"]=state["btc_bias_raw"]
        analysis_state["label"]="теневая проверка экстремальной ширины рынка"
    min_score=min(94,MIN_SIGNAL_SCORE+(state["base_score_adjustment"] if breadth_shadow
                                      else state["score_adjustment"]))
    if int(news.get("sources",0))<1:
        log.warning("short scan skipped: news-risk sources unavailable")
        return []
    symbols=[s for s in symbols if tickers.get(s,{}).get("quote_volume",0)>=max(MIN_24H_QUOTE_VOLUME,30_000_000)]
    symbols.sort(key=lambda s:tickers[s]["quote_volume"],reverse=True)
    if MAX_SYMBOLS_TO_SCAN>0:
        symbols=symbols[:MAX_SYMBOLS_TO_SCAN]
    semaphore=asyncio.Semaphore(SCAN_CONCURRENCY)
    candidates=await asyncio.gather(*(short_technical_candidate(s,analysis_state,semaphore,news,min_score) for s in symbols))
    candidates=sorted([x for x in candidates if x],key=lambda x:x[4].score,reverse=True)
    if DEEP_ANALYSIS_LIMIT>0:
        candidates=candidates[:DEEP_ANALYSIS_LIMIT]
    deep_count=len(candidates)
    frames={candidate[0]:candidate[3] for candidate in candidates}
    results=await asyncio.gather(*(short_deep_candidate(x,analysis_state,semaphore,news,adl_risks,min_score) for x in candidates))
    live=[row[0] for row in results if row and row[0]]
    if breadth_shadow:
        _store_shadows([(signal,"BREADTH_EXTREME") for signal in live])
        final=[]
    else:
        _store_shadows([(row[1],row[2]) for row in results if row and row[1] and row[2]])
        final=sorted(live,key=lambda x:x.score,reverse=True)
    annotate_correlation_clusters(final,frames)
    log.info("short scan: liquid=%s prefiltered=%s final=%s regime=%s threshold=%s",
             len(symbols),deep_count,len(final),bias,min_score+4)
    return final
