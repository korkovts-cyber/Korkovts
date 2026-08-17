import asyncio
import copy
import logging
from datetime import datetime, timezone

from .config import (
    DEEP_ANALYSIS_LIMIT,
    MAX_SYMBOLS_TO_SCAN,
    MIN_24H_QUOTE_VOLUME,
    MIN_SIGNAL_SCORE,
    NEUTRAL_REGIME_MAX_SIGNALS,
    NEUTRAL_REGIME_SCORE_PENALTY,
    SCAN_CONCURRENCY,
)
from .db import calibration_penalty, save_shadow, was_shadowed_recently
from .liquidations import snapshot as liquidation_snapshot
from .market import (
    get_adl_risks,
    get_derivatives_snapshot,
    get_klines,
    get_symbols,
    get_tickers,
)
from .news import for_symbol, get_news_sentiment
from .research import (
    annotate_correlation_clusters,
    breadth_is_extreme_against,
    market_breadth,
)
from .strategy import analyze

log=logging.getLogger(__name__)
_last_scan={"main":{"status":"idle"},"short":{"status":"idle"}}

class ScanUnavailable(RuntimeError):
    """A mandatory source failed, so an empty result would be misleading."""

def _begin_diagnostics(kind):
    return {"kind":kind,"status":"running","reason":"","started_at":datetime.now(timezone.utc).isoformat(),
            "finished_at":None,"liquid":0,"prefiltered":0,"deep_checked":0,"final":0,
            "technical_rejected":0,"technical_errors":0,"derivatives_incomplete":0,
            "deep_rejected":0,"deep_errors":0,"news_sources":0,"regime":"UNKNOWN",
            "threshold":None,"independent_mode":False,"error_examples":[],
            "near_candidates":[],"deep_rejections":[]}

def _bump(diagnostics,key):
    if diagnostics is not None:
        diagnostics[key]=int(diagnostics.get(key,0))+1

def _record_error(diagnostics,key,symbol,exc):
    _bump(diagnostics,key)
    if diagnostics is not None and len(diagnostics["error_examples"])<5:
        diagnostics["error_examples"].append(f"{symbol}: {type(exc).__name__}: {exc}")
    log.warning("%s failed for %s: %s",key,symbol,exc)

def _record_decision(diagnostics,key,audit):
    if diagnostics is None or not audit or not audit.get("symbol"):
        return
    row={name:audit.get(name) for name in
         ("symbol","timeframe","side","raw","threshold","setup","distance_atr","adx","htf","quality")}
    row["issues"]=list(audit.get("issues") or ["соотношение входа и стопа не прошло"])
    rows=diagnostics.setdefault(key,[])
    rows[:]=[existing for existing in rows if existing.get("symbol")!=row["symbol"]]
    rows.append(row)
    rows.sort(key=lambda item:float(item.get("raw",0)),reverse=True)
    del rows[5:]

def _finish_diagnostics(diagnostics,status,reason=""):
    diagnostics["status"]=status
    diagnostics["reason"]=str(reason)
    diagnostics["finished_at"]=datetime.now(timezone.utc).isoformat()
    _last_scan[diagnostics["kind"]]=copy.deepcopy(diagnostics)

def scan_status():
    return copy.deepcopy(_last_scan)

def _too_many_errors(errors,total):
    return total>0 and (errors>=total or errors/total>0.25)

def scan_thresholds(state):
    """Return quality gates for the current market regime."""
    breadth_blocked=bool(state.get("breadth_blocked"))
    adjustment=(state.get("base_score_adjustment",0) if breadth_blocked
                else state.get("score_adjustment",0))
    neutral_mode=state.get("btc_bias_raw")=="NEUTRAL"
    if neutral_mode:
        adjustment=max(float(adjustment),NEUTRAL_REGIME_SCORE_PENALTY)
    main=min(92,MIN_SIGNAL_SCORE+float(adjustment))
    short_base=min(94,MIN_SIGNAL_SCORE+float(adjustment))
    return {"main":main,"short_base":short_base,"short":min(95,short_base+4),
            "neutral_mode":neutral_mode}

def market_analysis_state(state):
    """Allow strong coins to prove themselves when BTC direction is not trustworthy."""
    analysis_state=dict(state)
    neutral_mode=state.get("btc_bias_raw")=="NEUTRAL"
    breadth_conflict=bool(state.get("breadth_blocked"))
    if neutral_mode:
        analysis_state["bias"]=None
        analysis_state["independent_mode"]=True
        analysis_state["label"]="нейтральный BTC: поиск независимых сетапов"
    elif breadth_conflict:
        # V10R.6: breadth conflict is a risk flag, not a whole-market kill switch.
        # Do not force every coin to follow BTC when most of the market disagrees.
        analysis_state["bias"]=None
        analysis_state["independent_mode"]=True
        analysis_state["breadth_risk"]=True
        analysis_state["label"]="конфликт BTC и ширины рынка: независимая проверка"
    return analysis_state,neutral_mode

def _limit_live_results(results,neutral_mode):
    # One priority signal + up to three alternatives.
    limit=NEUTRAL_REGIME_MAX_SIGNALS if neutral_mode else 4
    return results[:max(1,limit)]

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

async def technical_candidate(symbol,market_context=None,semaphore=None,news=None,
                              min_score=MIN_SIGNAL_SCORE,diagnostics=None):
    try:
        async with semaphore:
            lower,a,b=await asyncio.gather(get_klines(symbol,"15m",260),get_klines(symbol,"1h",350),
                get_klines(symbol,"4h",350))
        market_bias=(market_context or {}).get("bias")
        audit={}
        preliminary=analyze(symbol,"1H",a,b,max(60,min_score-15),lower,market_bias,None,
                            for_symbol(news or {},symbol),market_context,audit=audit)
        if preliminary:
            return symbol,lower,a,b,preliminary
        _bump(diagnostics,"technical_rejected")
        _record_decision(diagnostics,"near_candidates",audit)
        return None
    except Exception as exc:
        _record_error(diagnostics,"technical_errors",symbol,exc)
        return None

async def deep_candidate(candidate,market_context,semaphore,news,adl_risks,
                         min_score=MIN_SIGNAL_SCORE,diagnostics=None):
    symbol,lower,a,b,preliminary=candidate
    try:
        async with semaphore:
            adl=adl_risks.get(symbol,{"risk":"unknown","fresh":False,"age_minutes":9999})
            d=await asyncio.wait_for(get_derivatives_snapshot(symbol,adl),timeout=18)
        if not d.get("deep_data"):
            _bump(diagnostics,"derivatives_incomplete")
            log.info("skip %s: incomplete derivatives snapshot (%s/%s; missing %s)",
                     symbol,d.get("data_quality",0),d.get("data_quality_total",9),
                     ",".join(d.get("missing",[])))
            return None,None,None
        oi_notional=float(d.get("open_interest",0))*float(d.get("mark_price",0))
        d.update(liquidation_snapshot(symbol,oi_notional))
        penalty=calibration_penalty(symbol,preliminary.side,"1H")
        threshold=min(95,min_score+penalty)
        audit={}
        result=analyze(symbol,"1H",a,b,threshold,lower,market_context.get("bias"),d,
                       for_symbol(news,symbol),market_context,audit=audit)
        shadow=reason=None
        if result is None and (str(d.get("adl_risk","unknown")).lower()!="low" or not d.get("adl_fresh")):
            baseline=dict(d); baseline.update(adl_risk="low",adl_fresh=True,adl_age_minutes=0)
            shadow=analyze(symbol,"1H",a,b,threshold,lower,market_context.get("bias"),baseline,
                           for_symbol(news,symbol),market_context)
            reason=_adl_shadow_reason(d) if shadow else None
        if result is None:
            _bump(diagnostics,"deep_rejected")
            _record_decision(diagnostics,"deep_rejections",audit)
        return result,shadow,reason
    except Exception as exc:
        _record_error(diagnostics,"deep_errors",symbol,exc)
        return None,None,None

async def short_technical_candidate(symbol,market_context=None,semaphore=None,news=None,
                                    min_score=MIN_SIGNAL_SCORE,diagnostics=None):
    """Fast setup: 5m entry, 15m setup, 1h trend confirmation."""
    try:
        async with semaphore:
            lower,base,higher=await asyncio.gather(
                get_klines(symbol,"5m",300),get_klines(symbol,"15m",350),get_klines(symbol,"1h",350))
        market_bias=(market_context or {}).get("bias")
        audit={}
        preliminary=analyze(symbol,"15M",base,higher,max(65,min_score-12),lower,market_bias,None,
                            for_symbol(news or {},symbol),market_context,audit=audit)
        if preliminary:
            return symbol,lower,base,higher,preliminary
        _bump(diagnostics,"technical_rejected")
        _record_decision(diagnostics,"near_candidates",audit)
        return None
    except Exception as exc:
        _record_error(diagnostics,"technical_errors",symbol,exc)
        return None

async def short_deep_candidate(candidate,market_context,semaphore,news,adl_risks,
                               min_score=MIN_SIGNAL_SCORE,diagnostics=None):
    symbol,lower,base,higher,preliminary=candidate
    try:
        async with semaphore:
            adl=adl_risks.get(symbol,{"risk":"unknown","fresh":False,"age_minutes":9999})
            derivatives=await asyncio.wait_for(get_derivatives_snapshot(symbol,adl),timeout=18)
        if not derivatives.get("deep_data"):
            _bump(diagnostics,"derivatives_incomplete")
            log.info("skip short %s: incomplete derivatives snapshot (%s/%s)",
                     symbol,derivatives.get("data_quality",0),derivatives.get("data_quality_total",9))
            return None,None,None
        oi_notional=float(derivatives.get("open_interest",0))*float(derivatives.get("mark_price",0))
        derivatives.update(liquidation_snapshot(symbol,oi_notional))
        penalty=calibration_penalty(symbol,preliminary.side,"15M")
        threshold=min(95,min_score+4+penalty)
        audit={}
        result=analyze(symbol,"15M",base,higher,threshold,lower,market_context.get("bias"),
                       derivatives,for_symbol(news,symbol),market_context,audit=audit)
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
        if result is None:
            _bump(diagnostics,"deep_rejected")
            _record_decision(diagnostics,"deep_rejections",audit)
        return result,shadow,reason
    except Exception as exc:
        _record_error(diagnostics,"deep_errors",symbol,exc)
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
    diagnostics=_begin_diagnostics("main")
    try:
        symbols,tickers,news,adl_risks=await asyncio.gather(
            get_symbols(),get_tickers(),get_news_sentiment(),get_adl_risks())
        diagnostics["news_sources"]=int(news.get("sources",0))
        state=await market_state(tickers)
        bias=state["bias"]
        diagnostics["regime"]=bias
        if diagnostics["news_sources"]<1:
            raise ScanUnavailable("all news-risk sources are unavailable")
        breadth_risk=bool(state.get("breadth_blocked"))
        analysis_state,neutral_mode=market_analysis_state(state)
        thresholds=scan_thresholds(state)
        min_score=thresholds["main"]
        diagnostics["independent_mode"]=bool(neutral_mode or breadth_risk)
        diagnostics["threshold"]=min_score
        symbols=[s for s in symbols if tickers.get(s,{}).get("quote_volume",0)>=MIN_24H_QUOTE_VOLUME]
        symbols.sort(key=lambda s:tickers[s]["quote_volume"],reverse=True)
        if MAX_SYMBOLS_TO_SCAN>0:
            symbols=symbols[:MAX_SYMBOLS_TO_SCAN]
        if not symbols:
            raise ScanUnavailable("no liquid symbols available from Binance")
        semaphore=asyncio.Semaphore(SCAN_CONCURRENCY)
        diagnostics["liquid"]=len(symbols)
        candidates=await asyncio.gather(*(technical_candidate(
            s,analysis_state,semaphore,news,min_score,diagnostics) for s in symbols))
        if _too_many_errors(diagnostics["technical_errors"],len(symbols)):
            raise ScanUnavailable(
                f"technical data failed for {diagnostics['technical_errors']}/{len(symbols)} symbols")
        candidates=sorted([x for x in candidates if x],key=lambda x:x[4].score,reverse=True)
        diagnostics["prefiltered"]=len(candidates)
        if DEEP_ANALYSIS_LIMIT>0:
            candidates=candidates[:DEEP_ANALYSIS_LIMIT]
        diagnostics["deep_checked"]=len(candidates)
        frames={candidate[0]:candidate[2] for candidate in candidates}
        results=await asyncio.gather(*(deep_candidate(
            x,analysis_state,semaphore,news,adl_risks,min_score,diagnostics) for x in candidates))
        if diagnostics["deep_checked"] and diagnostics["derivatives_incomplete"]>=diagnostics["deep_checked"]:
            raise ScanUnavailable("derivatives data incomplete for every deep candidate")
        if _too_many_errors(diagnostics["deep_errors"],diagnostics["deep_checked"]):
            raise ScanUnavailable(
                f"deep analysis failed for {diagnostics['deep_errors']}/{diagnostics['deep_checked']} candidates")
        live=[row[0] for row in results if row and row[0]]
        _store_shadows([(row[1],row[2]) for row in results if row and row[1] and row[2]])
        final=sorted(live,key=lambda x:x.score,reverse=True)
        annotate_correlation_clusters(final,frames)
        final=_limit_live_results(final,neutral_mode)
        diagnostics["final"]=len(final)
        if neutral_mode:
            reason=(f"BTC нейтрален; независимый поиск с порогом {min_score:.0f}, "
                    f"максимум {NEUTRAL_REGIME_MAX_SIGNALS} сигнала за скан")
        elif breadth_risk:
            reason="конфликт BTC и ширины рынка: сигнал не блокируется, каждая монета проверена независимо"
        else:
            reason=""
        _finish_diagnostics(diagnostics,"ok",reason)
        log.info("main scan: liquid=%s prefiltered=%s deep=%s final=%s regime=%s independent=%s threshold=%s errors=%s",
                 diagnostics["liquid"],diagnostics["prefiltered"],diagnostics["deep_checked"],
                 diagnostics["final"],bias,diagnostics["independent_mode"],min_score,
                 diagnostics["technical_errors"]+diagnostics["deep_errors"])
        return final
    except Exception as exc:
        _finish_diagnostics(diagnostics,"error",exc)
        raise

async def scan_short():
    diagnostics=_begin_diagnostics("short")
    try:
        symbols,tickers,news,adl_risks=await asyncio.gather(
            get_symbols(),get_tickers(),get_news_sentiment(),get_adl_risks())
        diagnostics["news_sources"]=int(news.get("sources",0))
        state=await market_state(tickers)
        bias=state["bias"]
        diagnostics["regime"]=bias
        if diagnostics["news_sources"]<1:
            raise ScanUnavailable("all news-risk sources are unavailable")
        breadth_risk=bool(state.get("breadth_blocked"))
        analysis_state,neutral_mode=market_analysis_state(state)
        thresholds=scan_thresholds(state)
        min_score=thresholds["short_base"]
        diagnostics["independent_mode"]=bool(neutral_mode or breadth_risk)
        diagnostics["threshold"]=thresholds["short"]
        # V10R.6: scan substantially more short-term markets, but still require meaningful liquidity.
        symbols=[s for s in symbols if tickers.get(s,{}).get("quote_volume",0)>=max(MIN_24H_QUOTE_VOLUME,10_000_000)]
        symbols.sort(key=lambda s:tickers[s]["quote_volume"],reverse=True)
        if MAX_SYMBOLS_TO_SCAN>0:
            symbols=symbols[:MAX_SYMBOLS_TO_SCAN]
        if not symbols:
            raise ScanUnavailable("no liquid short-term symbols available from Binance")
        diagnostics["liquid"]=len(symbols)
        semaphore=asyncio.Semaphore(SCAN_CONCURRENCY)
        candidates=await asyncio.gather(*(short_technical_candidate(
            s,analysis_state,semaphore,news,min_score,diagnostics) for s in symbols))
        if _too_many_errors(diagnostics["technical_errors"],len(symbols)):
            raise ScanUnavailable(
                f"technical data failed for {diagnostics['technical_errors']}/{len(symbols)} symbols")
        candidates=sorted([x for x in candidates if x],key=lambda x:x[4].score,reverse=True)
        diagnostics["prefiltered"]=len(candidates)
        if DEEP_ANALYSIS_LIMIT>0:
            candidates=candidates[:DEEP_ANALYSIS_LIMIT]
        diagnostics["deep_checked"]=len(candidates)
        frames={candidate[0]:candidate[3] for candidate in candidates}
        results=await asyncio.gather(*(short_deep_candidate(
            x,analysis_state,semaphore,news,adl_risks,min_score,diagnostics) for x in candidates))
        if diagnostics["deep_checked"] and diagnostics["derivatives_incomplete"]>=diagnostics["deep_checked"]:
            raise ScanUnavailable("derivatives data incomplete for every deep candidate")
        if _too_many_errors(diagnostics["deep_errors"],diagnostics["deep_checked"]):
            raise ScanUnavailable(
                f"deep analysis failed for {diagnostics['deep_errors']}/{diagnostics['deep_checked']} candidates")
        live=[row[0] for row in results if row and row[0]]
        _store_shadows([(row[1],row[2]) for row in results if row and row[1] and row[2]])
        final=sorted(live,key=lambda x:x.score,reverse=True)
        annotate_correlation_clusters(final,frames)
        final=_limit_live_results(final,neutral_mode)
        diagnostics["final"]=len(final)
        if neutral_mode:
            reason=(f"BTC нейтрален; независимый поиск с порогом {thresholds['short']:.0f}, "
                    f"максимум {NEUTRAL_REGIME_MAX_SIGNALS} сигнала за скан")
        elif breadth_risk:
            reason="конфликт BTC и ширины рынка: краткосрочные сетапы проверены независимо"
        else:
            reason=""
        _finish_diagnostics(diagnostics,"ok",reason)
        log.info("short scan: liquid=%s prefiltered=%s deep=%s final=%s regime=%s independent=%s threshold=%s errors=%s",
                 diagnostics["liquid"],diagnostics["prefiltered"],diagnostics["deep_checked"],
                 diagnostics["final"],bias,diagnostics["independent_mode"],thresholds["short"],
                 diagnostics["technical_errors"]+diagnostics["deep_errors"])
        return final
    except Exception as exc:
        _finish_diagnostics(diagnostics,"error",exc)
        raise
