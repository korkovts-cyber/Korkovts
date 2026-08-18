"""Korkovts Signal AI V11.4.1 PRECISION AUDIT."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from html import escape

from telegram.constants import ParseMode

import app.bot as core
import app.config as config
import app.db as db
import app.scanner as scanner

from v11_engine import attach, classify_regime, init_rank_audit, record_rank_audit
from v11_liquidity import annotate as annotate_liquidity
from v11_live import monitor as live_monitor, health as live_health
from v11_manager import init as init_lifecycle, observe as observe_lifecycle, structure_watch
from v11_ui import (
    card, detail_by_signal_id, detail_by_snapshot_id, life_text, main_menu,
    risk_text, signal_actions, stats_text, system_extra, why_text,
)
from v112_details import init as init_details, save_snapshot
from v113_execution import revalidate_many, revalidate
from v113_tracking import delivery_aware_open_signals
import app.tracker as tracker_module
from v112_alpha import annotate as annotate_alpha
from v112_health import check as health_check, text as health_text
from v112_lab import lab_text, weighted_adjustment, weights as factor_weights
from v113_meta import decide as meta_decide, report_text as meta_report_text, model as meta_model
from v113_robustness import text as robustness_text
from v114_db import (
    harden_database, backup_if_due, checkpoint as db_checkpoint,
    status as db_runtime_status,
)
from v114_news import (
    safe_fetch as safe_news_fetch, state as news_runtime_state,
    begin_context as begin_news_context, end_context as end_news_context,
)
from v114_entry import (
    text as entry_quality_text,
    negative_penalty as entry_negative_penalty,
)
from v1141_integrity import (
    clock_status as integrity_clock_status, exchange_metadata,
    normalize_signal, stamp_lineage, invariant_errors,
)
from v1141_tracking import install as install_ambiguous_tracker
from v1141_governor import install as install_request_governor, status as request_governor_status

tracker_module.open_signals=delivery_aware_open_signals
install_ambiguous_tracker()
install_request_governor()

APP_VERSION="11.4.1"
STRATEGY_VERSION="11.4.1-precision-audit"

config.APP_VERSION=APP_VERSION
config.STRATEGY_VERSION=STRATEGY_VERSION
core.APP_VERSION=APP_VERSION
core.STRATEGY_VERSION=STRATEGY_VERSION
db.APP_VERSION=APP_VERSION
db.STRATEGY_VERSION=STRATEGY_VERSION

_last_regime=None
_last_health=None
_last_integrity_clock=None
_last_neutral=False
_live_task=None

# The core scanner has already completed expensive strategy/derivatives gates.
# Keep a wider *qualified* pool for Production comparison, while Telegram still
# publishes only 3–4. Default 12 is intentionally bounded to control REST load.
V11_CANDIDATE_POOL=max(4,min(20,int(os.getenv("V11_CANDIDATE_POOL","20"))))
_original_limit_live_results=getattr(scanner,"_limit_live_results",None)
if _original_limit_live_results is not None:
    scanner._limit_live_results=lambda results,neutral_mode: results[:V11_CANDIDATE_POOL]

_raw_scan=core.scan
_raw_short=core.scan_short
_raw_save=core.save
_raw_save_pending=core.save_pending
_original_callback=core.callback
_original_post_init=core.post_init
_original_post_shutdown=core.post_shutdown
_original_news_status=getattr(core,"news_status",None)


_original_scanner_news=getattr(scanner,"get_news_sentiment",None)
_original_core_news=getattr(core,"get_news_sentiment",None)
_original_scanner_derivatives=getattr(scanner,"get_derivatives_snapshot",None)
_original_core_derivatives=getattr(core,"get_derivatives_snapshot",None)
_original_analyze=getattr(core,"analyze",None)


async def _safe_scanner_news(*args,**kwargs):
    return await safe_news_fetch(_original_scanner_news,*args,**kwargs)


async def _safe_core_news(*args,**kwargs):
    return await safe_news_fetch(_original_core_news,*args,**kwargs)


if _original_scanner_news is not None:
    scanner.get_news_sentiment=_safe_scanner_news
if _original_core_news is not None:
    core.get_news_sentiment=_safe_core_news


async def _timed_derivatives(fetcher,*args,**kwargs):
    started_wall=time.time()*1000
    started=time.perf_counter()
    result=await fetcher(*args,**kwargs)
    finished=time.time()*1000
    payload=dict(result or {})
    payload["v1141_acquire_started_ms"]=started_wall
    payload["v1141_acquire_finished_ms"]=finished
    payload["v1141_acquire_duration_ms"]=(time.perf_counter()-started)*1000
    return payload


async def _scanner_derivatives(*args,**kwargs):
    return await _timed_derivatives(_original_scanner_derivatives,*args,**kwargs)


async def _core_derivatives(*args,**kwargs):
    return await _timed_derivatives(_original_core_derivatives,*args,**kwargs)


if _original_scanner_derivatives is not None:
    scanner.get_derivatives_snapshot=_scanner_derivatives
if _original_core_derivatives is not None:
    core.get_derivatives_snapshot=_core_derivatives


def _analyze_with_freshness(*args,**kwargs):
    derivatives=kwargs.get("derivatives")
    timeframe=kwargs.get("timeframe")
    if derivatives is None and len(args)>7:
        derivatives=args[7]
    if timeframe is None and len(args)>1:
        timeframe=args[1]

    if derivatives is not None:
        acquire=float((derivatives or {}).get("v1141_acquire_duration_ms",0) or 0)
        max_ms=8000.0 if str(timeframe).upper()=="15M" else 12000.0
        if acquire>max_ms:
            audit=kwargs.get("audit")
            if audit is None and len(args)>10:
                audit=args[10]
            if isinstance(audit,dict):
                audit.setdefault("issues",[]).append(
                    f"derivatives snapshot acquisition {acquire/1000:.1f}s is stale"
                )
            return None

    result=_original_analyze(*args,**kwargs)
    if result is not None and derivatives is not None:
        acquire=float((derivatives or {}).get("v1141_acquire_duration_ms",0) or 0)
        result.feature_snapshot.setdefault("data_freshness_v1141",{}).update({
            "derivatives_acquire_ms":acquire,
            "acquire_started_ms":float((derivatives or {}).get("v1141_acquire_started_ms",0) or 0),
            "acquire_finished_ms":float((derivatives or {}).get("v1141_acquire_finished_ms",0) or 0),
            "adl_age_minutes":float((derivatives or {}).get("adl_age_minutes",9999) or 9999),
            "status":"GOOD" if acquire<=(
                8000.0 if str(timeframe).upper()=="15M" else 12000.0
            ) else "STALE",
        })
    return result


if _original_analyze is not None:
    scanner.analyze=_analyze_with_freshness
    core.analyze=_analyze_with_freshness


def thresholds_v112(state):
    global _last_regime,_last_neutral
    regime=classify_regime(state)
    _last_regime=regime
    neutral=state.get("btc_bias_raw")=="NEUTRAL"
    _last_neutral=bool(neutral)
    adjustment=max(float(state.get("score_adjustment",0) or 0),regime.penalty)
    if news_runtime_state().get("degraded"):
        # News is not a directional source in degraded mode. Continue scanning
        # Binance, but require +2 additional deterministic quality points.
        adjustment+=2.0
    if neutral:
        adjustment=max(adjustment,float(config.NEUTRAL_REGIME_SCORE_PENALTY))
    main=min(94,float(config.MIN_SIGNAL_SCORE)+adjustment)
    short_base=min(94,float(config.MIN_SIGNAL_SCORE)+adjustment)
    return {
        "main":main,"short_base":short_base,"short":min(96,short_base+4),
        "neutral_mode":neutral,"regime_profile":regime.name,
    }


scanner.scan_thresholds=thresholds_v112
core.scan_thresholds=thresholds_v112


def combine_rank(base_rank,alpha_adjustment,base_eligible=True):
    """Alpha may demote/reject a valid signal; it can never rescue an invalid base signal."""
    if not base_eligible:
        return None
    final=max(0.0,min(99.0,float(base_rank)+float(alpha_adjustment)))
    return final if final>=75.0 else None


def grade_for_rank(rank):
    rank=float(rank)
    return (
        "A+" if rank>=90 else
        "A" if rank>=85 else
        "B+" if rank>=80 else
        "B" if rank>=75 else
        "WATCH"
    )


def _freeze_final_rank(signal,rank):
    rank=max(0.0,min(99.0,float(rank)))
    signal.professional_rank=rank
    signal.professional_grade=grade_for_rank(rank)
    v11=signal.feature_snapshot.setdefault("v11",{})
    v11["champion_rank"]=rank
    v11["grade"]=signal.professional_grade
    return signal


def _refresh_cluster_ranks(rows):
    """Re-rank members inside each correlation cluster after PRO scoring."""
    grouped={}
    for signal in rows:
        cid=int(getattr(signal,"cluster_id",0) or 0)
        if cid:
            grouped.setdefault(cid,[]).append(signal)
    for cid,members in grouped.items():
        members.sort(
            key=lambda s:(float(getattr(s,"professional_rank",0)),
                          float(getattr(s,"score",0))),
            reverse=True,
        )
        for rank,signal in enumerate(members,1):
            signal.cluster_rank=rank
            signal.feature_snapshot.setdefault("portfolio",{}).update({
                "cluster_id":cid,
                "cluster_size":len(members),
                "cluster_rank":rank,
            })
    return rows


def _portfolio_select(rows,max_results=4):
    rows=sorted(
        rows,key=lambda s:(float(getattr(s,"professional_rank",0)),float(getattr(s,"score",0))),
        reverse=True
    )
    if not rows:
        return []
    selected=[rows[0]]
    used={int(getattr(rows[0],"cluster_id",0) or 0)}
    sides={str(getattr(rows[0],"side","")):1}
    for s in rows[1:]:
        cluster=int(getattr(s,"cluster_id",0) or 0)
        side=str(getattr(s,"side",""))
        if cluster and cluster in used: continue
        if sides.get(side,0)>=3: continue
        selected.append(s)
        if cluster: used.add(cluster)
        sides[side]=sides.get(side,0)+1
        if len(selected)>=max_results: return selected
    existing={id(x) for x in selected}
    for s in rows[1:]:
        if id(s) in existing: continue
        side=str(getattr(s,"side",""))
        if sides.get(side,0)>=3: continue
        selected.append(s); sides[side]=sides.get(side,0)+1
        if len(selected)>=max_results: break
    return selected


def _store_counterfactual(signal,reason,hours=12):
    try:
        if not db.was_shadowed_recently(signal.symbol,signal.side,signal.timeframe,reason,hours):
            db.save_shadow(signal,reason)
            return True
    except Exception:
        core.log.exception("V11.4.1 counterfactual shadow save failed")
    return False


def _pipeline_latency_penalty(raw_scan_sec,kind):
    grace=120.0 if str(kind)=="short" else 180.0
    return min(2.0,max(0.0,float(raw_scan_sec)-grace)/60.0)


def _revalidation_candidates(rows):
    return sorted(
        list(rows or []),
        key=lambda s:(float(getattr(s,"professional_rank",0)),
                      float(getattr(s,"score",0))),
        reverse=True,
    )


async def _prepare(results,kind):
    if not results:
        return []

    source_context=dict(getattr(results[0],"market_context",{}) or {})
    source_regime=classify_regime(source_context)
    neutral_context=source_context.get("btc_bias_raw")=="NEUTRAL"

    # First snapshot: liquidity + L2 state. Alpha obtains closed-flow context.
    results=await asyncio.wait_for(annotate_liquidity(results),timeout=45)
    results=await asyncio.wait_for(annotate_alpha(results),timeout=60)

    prepared=[]
    alpha_rejected=[]
    for s in results:
        signal_context=dict(getattr(s,"market_context",{}) or source_context)
        signal_regime=classify_regime(signal_context)
        s.production_regime=signal_regime.name
        s=attach(s,signal_regime)

        base=float(getattr(s,"professional_rank",0))
        base_ok=bool(getattr(s,"professional_eligible",False))
        s.base_professional_rank=base
        if not base_ok:
            continue

        alpha=weighted_adjustment(s)
        final=combine_rank(base,alpha,True)
        s.feature_snapshot.setdefault("alpha_v112",{}).update({
            "base_professional_rank":base,
            "candidate_pool":V11_CANDIDATE_POOL,
        })
        s.feature_snapshot.setdefault("delivery_meta",{}).setdefault("source","scanner_pool")
        if final is None:
            s.feature_snapshot["alpha_v112"].update(
                rejected_after_alpha=True,
                final_professional_rank=max(0.0,base+alpha),
            )
            alpha_rejected.append(s)
            continue

        _freeze_final_rank(s,final)
        v11=s.feature_snapshot.setdefault("v11",{})
        v11["base_champion_rank"]=base
        s.feature_snapshot["alpha_v112"]["final_professional_rank"]=final
        prepared.append(s)

    final_limit=int(config.NEUTRAL_REGIME_MAX_SIGNALS) if neutral_context else 4

    # Binance exchange filters are part of the decision contract. Normalize
    # every qualified candidate before execution checks, never only the top few.
    metadata_valid=[]
    metadata_rejected=[]
    metadata_error=None
    try:
        # One shared exchangeInfo fetch/cache per pipeline, never one retry per
        # candidate when Binance metadata is unavailable.
        await exchange_metadata()
    except Exception as exc:
        metadata_error=exc

    if metadata_error is not None and prepared:
        # Metadata is a mandatory exchange contract, not a candidate-quality
        # failure. Do not turn an exchangeInfo outage into "no market signals".
        raise RuntimeError(
            f"mandatory Binance exchangeInfo unavailable: "
            f"{type(metadata_error).__name__}: {metadata_error}"
        )

    for signal in prepared:
        try:
            metadata_valid.append(await normalize_signal(signal))
        except Exception as exc:
            signal.feature_snapshot.setdefault("exchange_meta_v1141",{}).update({
                "eligible":False,"reason":f"{type(exc).__name__}: {exc}",
            })
            metadata_rejected.append(signal)
    prepared=metadata_valid

    # Every qualified candidate is revalidated. Publication is capped only
    # AFTER execution/meta/portfolio decisions, so candidate #13 cannot be lost
    # merely because the first 12 became stale.
    revalidation_pool=_revalidation_candidates(prepared)
    checked=await asyncio.wait_for(revalidate_many(revalidation_pool),timeout=90)
    execution_valid=[]
    execution_rejected=[]
    entry_rejected=[]
    for signal,result in checked:
        if not result.eligible:
            execution_rejected.append(signal)
            continue

        distance=float(result.distance_r)
        freshness_penalty=(
            max(0.0,distance)*3.0
            + max(0.0,-distance)*1.5
            + max(0.0,float(result.spread_bps)-1.0)*.20
        )
        signal.pre_execution_rank=float(signal.professional_rank)
        signal.execution_freshness_penalty=min(2.0,freshness_penalty)
        signal.live_cost_penalty=min(
            1.0,max(0.0,float(result.total_cost_r)-.15)*5.0
        )
        raw_scan_sec=float(
            (signal.feature_snapshot.get("pipeline_v1141") or {}).get("raw_scan_sec",0) or 0
        )
        signal.pipeline_latency_penalty=_pipeline_latency_penalty(raw_scan_sec,kind)

        # State-first microstructure is allowed a small positive boost but a
        # materially larger contradiction penalty.
        micro=float(getattr(signal,"micro_adjustment",0) or 0)
        final_rank=(
            signal.pre_execution_rank
            + micro
            - signal.execution_freshness_penalty
            - signal.live_cost_penalty
            - signal.pipeline_latency_penalty
        )
        if final_rank<75:
            signal.feature_snapshot.setdefault("execution_revalidation",{})["rank_below_75"]=True
            execution_rejected.append(signal)
            continue

        _freeze_final_rank(signal,min(99.0,final_rank))
        signal.feature_snapshot.setdefault("execution_revalidation",{}).update({
            "pre_execution_rank":signal.pre_execution_rank,
            "freshness_penalty":signal.execution_freshness_penalty,
            "micro_adjustment":micro,
            "pipeline_latency_penalty":signal.pipeline_latency_penalty,
            "post_micro_rank":signal.professional_rank,
        })
        # Entry realizability is allowed to demote only after sufficient
        # resolved forward observations for the same setup/timeframe/side.
        entry_penalty,entry_stats=entry_negative_penalty(signal,30)
        signal.feature_snapshot.setdefault("entry_quality_v1141",{}).update({
            **entry_stats,"penalty":entry_penalty,
        })
        if entry_penalty<0:
            _freeze_final_rank(signal,float(signal.professional_rank)+entry_penalty)
            if signal.professional_rank<75:
                signal.feature_snapshot["entry_quality_v1141"]["rejected"]=True
                entry_rejected.append(signal)
                continue

        execution_valid.append(signal)

    # Meta-label layer is shadow-only until its own chronological OOS tests say
    # READY. Once READY, it can reject low-confidence candidates; it still cannot
    # rescue any candidate rejected above.
    meta_valid=[]
    meta_rejected=[]
    for signal in execution_valid:
        signal,decision=meta_decide(signal)
        if decision.ready and not decision.eligible:
            meta_rejected.append(signal)
            continue
        meta_valid.append(signal)

    _refresh_cluster_ranks(meta_valid)
    chosen=_portfolio_select(meta_valid,final_limit)
    chosen_ids={id(x) for x in chosen}

    # Counterfactuals are labelled by the exact layer that removed them.
    for s in alpha_rejected:
        _store_counterfactual(s,"V1141_ALPHA_REJECT")
    for s in execution_rejected:
        _store_counterfactual(s,"V1141_EXECUTION_REJECT")
    for s in entry_rejected:
        _store_counterfactual(s,"V1141_ENTRY_REJECT")
    for s in meta_rejected:
        _store_counterfactual(s,"V1141_META_REJECT")
    for s in meta_valid:
        if id(s) not in chosen_ids:
            _store_counterfactual(s,"V1141_PORTFOLIO")

    try:
        d=scanner._last_scan[kind]
        d["production_pool"]=len(results)
        d["pre_v1141_final"]=len(results)
        d["final"]=len(chosen)
        d["v1141_filtered"]=len(results)-len(chosen)
        d["alpha_rejected"]=len(alpha_rejected)
        d["metadata_rejected"]=len(metadata_rejected)
        d["execution_rejected"]=len(execution_rejected)
        d["entry_rejected"]=len(entry_rejected)
        d["meta_rejected"]=len(meta_rejected)
        d["execution_revalidation_pool"]=len(revalidation_pool)
        d["portfolio_shadowed"]=sum(1 for s in meta_valid if id(s) not in chosen_ids)
        d["regime_profile"]=getattr(chosen[0],"production_regime",source_regime.name) if chosen else source_regime.name
        d["factor_weights"]=factor_weights("15M" if kind=="short" else "1H")
        meta_report,_=meta_model("15M" if kind=="short" else "1H")
        d["meta_status"]=meta_report.status
        d["meta_samples"]=meta_report.n
        d["v1141_top"]=[
            (
                s.symbol,
                round(float(s.professional_rank),1),
                round(float(getattr(s,"alpha_adjustment",0)),1),
                str(getattr(s,"l2_state","—")),
                round(float(getattr(s,"meta_score",.5)),2),
            )
            for s in chosen
        ]
    except Exception:
        core.log.exception("V11.4.1 diagnostics update failed")
    return chosen


async def _health_gate(kind):
    global _last_health,_last_integrity_clock
    _last_health=await health_check()
    clock=await integrity_clock_status()
    _last_integrity_clock=clock
    if not clock.ok:
        try:
            d=scanner._last_scan.setdefault(kind,{})
            d["status"]="ok"; d["reason"]="BINANCE CLOCK PAUSE"; d["final"]=0
            d["clock_offset_ms"]=round(clock.offset_ms,1)
            d["clock_rtt_ms"]=round(clock.rtt_ms,1)
        except Exception:
            pass
        return False
    if _last_health.hard_pause:
        try:
            d=scanner._last_scan.get(kind,{})
            d["status"]="ok"; d["reason"]="PRODUCTION HEALTH PAUSE"; d["final"]=0
            d["health"]=_last_health.status
        except Exception:
            pass
        return False
    return True


async def _run_scan_with_watchdog(kind,raw,timeout_sec):
    started=time.perf_counter()
    news_token=begin_news_context()
    try:
        results=await asyncio.wait_for(raw(),timeout=timeout_sec)
        raw_scan_sec=time.perf_counter()-started
        for signal in results or []:
            signal.feature_snapshot.setdefault("pipeline_v1141",{}).update({
                "raw_scan_sec":raw_scan_sec,
                "kind":kind,
            })
        news_state=news_runtime_state()
        if news_state.get("degraded"):
            try:
                d=scanner._last_scan.setdefault(kind,{})
                d["news_degraded"]=True
                d["news_sources"]=0
                d["news_reason"]=str(news_state.get("reason") or "unavailable")
            except Exception:
                pass
            for signal in results or []:
                signal.market_context=dict(getattr(signal,"market_context",{}) or {})
                signal.market_context["news_degraded"]=True
                signal.feature_snapshot.setdefault("news",{})["degraded"]=True
                signal.feature_snapshot["news"]["sources"]=0
                signal.feature_snapshot["news"]["real_sources"]=0
                signal.feature_snapshot["news"]["degraded_reason"]=str(
                    news_state.get("reason") or "unavailable"
                )
        return await _prepare(results,kind)
    except Exception as exc:
        try:
            d=scanner._last_scan.setdefault(kind,{})
            d["status"]="error"
            d["reason"]=f"V11.4.1 Production: {type(exc).__name__}: {exc}"
            nstate=news_runtime_state()
            if nstate.get("degraded"):
                d["news_degraded"]=True
                d["news_sources"]=0
                d["news_reason"]=str(nstate.get("reason") or "unavailable")
        except Exception:
            pass
        raise
    finally:
        try:
            scanner._last_scan.setdefault(kind,{})["v1141_duration_sec"]=round(
                time.perf_counter()-started,1
            )
        except Exception:
            pass
        end_news_context(news_token)


async def scan_v112():
    if not await _health_gate("main"):
        return []
    return await _run_scan_with_watchdog("main",_raw_scan,480)


async def short_v112():
    if not await _health_gate("short"):
        return []
    return await _run_scan_with_watchdog("short",_raw_short,420)


core.scan=scan_v112
core.scan_short=short_v112


def display_signal(signal):
    # Rank-freeze safety: do not call attach() again on a signal whose Alpha-
    # adjusted rank is already frozen. Auto delivery therefore shows the same
    # rank that was actually selected.
    if not hasattr(signal,"professional_rank"):
        signal=attach(signal,_last_regime)
    return signal


core.fmt=lambda s,priority=False: card(display_signal(s),priority)
core.menu=main_menu


async def analyze_symbol_v112(symbol):
    if not await _health_gate("main"):
        return None
    news_token=begin_news_context()
    try:
        lower,hourly,higher,derivatives,state,news=await asyncio.gather(
            core.get_klines(symbol,"15m",300),
            core.get_klines(symbol,"1h",400),
            core.get_klines(symbol,"4h",400),
            core.get_derivatives_snapshot(symbol),
            core.market_state(),
            core.get_news_sentiment(),
        )
        oi_notional=float(derivatives.get("open_interest",0))*float(derivatives.get("mark_price",0))
        derivatives.update(core.liquidation_snapshot(symbol,oi_notional))
        analysis_state,_=core.market_analysis_state(state)
        # Same breadth/neutral decision path as the whole-market scanner.
        threshold=thresholds_v112(state)["main"]
        result=core.analyze(
            symbol,"1H",hourly,higher,threshold,lower,
            analysis_state["bias"],derivatives,core.for_symbol(news,symbol),analysis_state
        )
        if not result:
            return None
        if bool((news or {}).get("v114_news_degraded")):
            result.market_context=dict(getattr(result,"market_context",{}) or {})
            result.market_context["news_degraded"]=True
            result.feature_snapshot.setdefault("news",{})["degraded"]=True
            result.feature_snapshot["news"]["sources"]=0
            result.feature_snapshot["news"]["real_sources"]=0
            result.feature_snapshot["news"]["degraded_reason"]=str(
                (news or {}).get("v114_news_reason") or "unavailable"
            )
        result=(await annotate_liquidity([result]))[0]
        result=(await annotate_alpha([result]))[0]
        result=attach(result,classify_regime(state))
        if not result.professional_eligible:
            return None
        result.base_professional_rank=float(result.professional_rank)
        alpha=weighted_adjustment(result)
        final=combine_rank(result.base_professional_rank,alpha,True)
        if final is None:
            return None
        _freeze_final_rank(result,final)
        v11=result.feature_snapshot.setdefault("v11",{})
        v11["base_champion_rank"]=result.base_professional_rank
        result.feature_snapshot.setdefault("alpha_v112",{}).update({
            "base_professional_rank":result.base_professional_rank,
            "final_professional_rank":final,
        })
        return await normalize_signal(result)
    finally:
        end_news_context(news_token)


core._analyze_symbol=analyze_symbol_v112


def _delivery_sent(signal_id,chat_id):
    try:
        with sqlite3.connect(config.DATABASE_PATH,timeout=10) as c:
            return c.execute(
                "SELECT 1 FROM signal_deliveries WHERE signal_id=? AND chat_id=? AND delivered_at IS NOT NULL",
                (int(signal_id),int(chat_id)),
            ).fetchone() is not None
    except Exception:
        return False


async def signal_v1123(update,context):
    symbol=(context.args[0] if context.args else "BTCUSDT").upper().replace("/","")
    if not symbol.endswith("USDT"):
        symbol+="USDT"
    msg=update.effective_message
    await msg.reply_text(f"🧠 Анализирую <b>{symbol}</b>…",parse_mode=ParseMode.HTML)
    try:
        result=await core._analyze_symbol(symbol)
        if not result:
            return await msg.reply_text(
                f"⚪ <b>{symbol}</b>: сейчас нет сделки, прошедшей Production + Alpha контроль.",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(symbol)
            )

        result.feature_snapshot.setdefault("delivery_meta",{})["source"]="manual_symbol"
        result,execution_check=await revalidate(result)
        if not execution_check.eligible:
            return await msg.reply_text(
                f"⚪ <b>{symbol}</b>: базовый сигнал был найден, но финальная проверка исполнения его отменила.\n"
                f"Причина: <b>{escape(execution_check.reason)}</b>",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(symbol)
            )

        # Apply fresh microstructure rank correction, then optional READY meta gate.
        micro=float(getattr(result,"micro_adjustment",0) or 0)
        distance=float(execution_check.distance_r)
        freshness=min(
            2.0,
            max(0.0,distance)*3.0
            +max(0.0,-distance)*1.5
            +max(0.0,float(execution_check.spread_bps)-1.0)*.20
        )
        live_cost_penalty=min(
            1.0,max(0.0,float(execution_check.total_cost_r)-.15)*5.0
        )
        pre_execution_rank=float(result.professional_rank)
        new_rank=pre_execution_rank+micro-freshness-live_cost_penalty
        result.feature_snapshot.setdefault("execution_revalidation",{}).update({
            "pre_execution_rank":pre_execution_rank,
            "freshness_penalty":freshness,
            "live_cost_penalty":live_cost_penalty,
            "post_micro_rank":new_rank,
        })
        if new_rank<75:
            return await msg.reply_text(
                f"⚪ <b>{symbol}</b>: live microstructure снизила качество входа ниже Production-порога.",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(symbol)
            )
        _freeze_final_rank(result,min(99.0,new_rank))

        entry_penalty,entry_stats=entry_negative_penalty(result,30)
        result.feature_snapshot.setdefault("entry_quality_v1141",{}).update({
            **entry_stats,"penalty":entry_penalty,
        })
        if entry_penalty<0:
            _freeze_final_rank(result,float(result.professional_rank)+entry_penalty)
            if result.professional_rank<75:
                return await msg.reply_text(
                    f"⚪ <b>{symbol}</b>: исторически этот тип Entry слишком редко реализуется.",
                    parse_mode=ParseMode.HTML,reply_markup=main_menu(symbol)
                )

        result,meta=meta_decide(result)
        if meta.ready and not meta.eligible:
            return await msg.reply_text(
                f"⚪ <b>{symbol}</b>: Meta Precision отклонил сделку после walk-forward проверки.",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(symbol)
            )
        if not core.was_sent_recently(
            result.symbol,result.side,core.SIGNAL_COOLDOWN_HOURS,result.timeframe
        ):
            signal_id=core.save_pending(result)
            payload=core.fmt(result,True)
            core.enqueue_delivery(signal_id,update.effective_chat.id,payload)
            await core._deliver_pending(context.bot)
            if not _delivery_sent(signal_id,update.effective_chat.id):
                await msg.reply_text(
                    "📨 Сигнал сохранён в очереди доставки. Telegram будет перепроверен автоматически.",
                    reply_markup=main_menu()
                )
        else:
            # Do not create a second production trade inside cooldown. Preserve
            # the exact current analysis in a separate detail snapshot instead.
            snapshot_id=save_snapshot(result,None)
            await msg.reply_text(
                core.fmt(result,True),
                parse_mode=ParseMode.HTML,
                reply_markup=signal_actions(snapshot_id=snapshot_id)
            )
    except Exception:
        core.log.exception("V11.4.1 symbol analysis failed for %s",symbol)
        await msg.reply_text("⚠️ Не удалось получить полный набор рыночных данных.",reply_markup=main_menu())


core.signal=signal_v1123


def _ready(signal):
    return signal if hasattr(signal,"professional_rank") else attach(signal,_last_regime)


def save_v112(signal,chat_id=None,shadow_reason=None):
    signal=stamp_lineage(_ready(signal))
    signal_id=_raw_save(signal,chat_id,shadow_reason)
    record_rank_audit(signal_id,signal)
    return signal_id


def save_pending_v112(signal):
    signal=stamp_lineage(_ready(signal))
    signal_id=_raw_save_pending(signal)
    record_rank_audit(signal_id,signal)
    return signal_id


core.save=save_v112
core.save_pending=save_pending_v112


async def start_v112(update,context):
    core.subscribe(update.effective_chat.id,True)
    text=(
        "⚡ <b>KORKOVTS SIGNAL AI · V11.4.1 PRECISION AUDIT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏆 Frozen Production Champion\n"
        "🧠 Orthogonal Alpha: Fresh · RelMomentum · OFI · Squeeze · BTC residual\n"
        "🧬 Factor Lab: forward edge + FDR-защита от случайных находок\n"
        "📡 Health Gate: latency · свежесть BTC 1m · clock · WebSocket\n"
        "🎯 Full-pool Revalidation: live bid/ask · two-sided cost · freshness\n"
        "📚 L2 State Engine: scale-aware depth · imbalance · microprice\n"
        "🧠 Meta Precision: walk-forward gate + OOD ABSTAIN\n"
        "📰 News failover: task-local, без cross-scan race · degraded +2\n"
        "💾 SQLite WAL · backup · Binance clock + exchange-filter guard\n"
        "🕒 Delivery-aware + ambiguous-candle journal\n"
        "💧 Liquidity Impact · 🧬 Drift · 🧺 Portfolio Filter\n"
        "🔄 Live lifecycle активных сигналов\n"
        f"⏱ Автоскан каждые {core.AUTO_SCAN_INTERVAL_MIN} минут · только новые сигналы\n\n"
        "Новый фактор не получает усиленный вес, пока не накопит достаточную статистику.\n"
        "⚠️ Бот не исполняет сделки. PRO-рейтинг не является вероятностью прибыли."
    )
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=main_menu())


core.start=start_v112


async def send_results_v112(bot,chat_id,results,automatic=False,short=False,diagnostics=None):
    if not results:
        if automatic: return None
        if _last_health is not None and _last_health.hard_pause:
            return await bot.send_message(
                chat_id,health_text(_last_health),
                parse_mode=ParseMode.HTML,reply_markup=main_menu(),
            )
        if _last_integrity_clock is not None and not _last_integrity_clock.ok:
            return await bot.send_message(
                chat_id,
                "⚠️ <b>PRODUCTION PAUSE · BINANCE CLOCK</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"Offset <b>{_last_integrity_clock.offset_ms:.0f}ms</b> · "
                f"RTT <b>{_last_integrity_clock.rtt_ms:.0f}ms</b>\n"
                "Сигнал не выдаётся, пока временная синхронизация не восстановится.",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(),
            )
        details=""
        if diagnostics and diagnostics.get("status")=="ok":
            details=(
                f"\n\nВоронка: <b>{diagnostics.get('liquid',0)}</b> → "
                f"<b>{diagnostics.get('prefiltered',0)}</b> → "
                f"<b>{diagnostics.get('pre_v1141_final',diagnostics.get('final',0))}</b> → "
                f"<b>{diagnostics.get('final',0)} V11.4.1</b>"
            )
            details+=core._decision_details(diagnostics)
        return await bot.send_message(
            chat_id,
            "⚪ <b>СИГНАЛОВ НЕТ</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Сейчас нет сделки, прошедшей Production + Alpha контроль."
            f"{details}",
            parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )

    mode="⚡ SHORT" if short else ("🤖 AUTO" if automatic else "🏆 MAIN")
    top=results[0]
    margin=(
        float(top.professional_rank)-float(results[1].professional_rank)
        if len(results)>1 else 99.0
    )
    margin_text=(
        "явное преимущество" if margin>=3 else
        "умеренное преимущество" if margin>=1.5 else
        "небольшое преимущество над №2"
    )
    await bot.send_message(
        chat_id,
        f"{mode} <b>· V11.4.1</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Сигналов: <b>{len(results)}</b>\n"
        f"№1 <b>{top.symbol}</b> · PRO <b>{top.professional_rank:.1f}/{top.professional_grade}</b> · {margin_text}\n"
        f"Alpha <b>{float(getattr(top,'alpha_adjustment',0)):+.1f}</b> · "
        f"Regime <b>{escape(str(getattr(top,'production_regime',getattr(_last_regime,'name','UNKNOWN'))))}</b>\n"
        "№1 выбран после core, L2 state, execution, drift, Alpha, Meta и portfolio контроля.",
        parse_mode=ParseMode.HTML
    )

    # Automatic delivery is handled by app.bot's durable scan path and doesn't
    # normally call this branch. Keep a safe direct fallback anyway.
    if automatic:
        for i,s in enumerate(results):
            await bot.send_message(
                chat_id,card(s,i==0),parse_mode=ParseMode.HTML,
                reply_markup=signal_actions(s.symbol,s.timeframe)
            )
        return

    queued=0
    repeat_cards=[]
    for i,s in enumerate(results):
        s.feature_snapshot.setdefault("delivery_meta",{})["source"]="manual_scan"
        if not core.was_sent_recently(s.symbol,s.side,core.SIGNAL_COOLDOWN_HOURS,s.timeframe):
            signal_id=core.save_pending(s)
            core.enqueue_delivery(signal_id,chat_id,core.fmt(s,i==0))
            queued+=1
        else:
            snapshot_id=save_snapshot(s,None)
            repeat_cards.append((s,i==0,snapshot_id))

    if queued:
        await core._deliver_pending(bot)

    # Cooldown repeats are explicitly shown as analysis snapshots, not new
    # production trades.
    for s,priority,snapshot_id in repeat_cards:
        await bot.send_message(
            chat_id,card(s,priority),parse_mode=ParseMode.HTML,
            reply_markup=signal_actions(snapshot_id=snapshot_id)
        )


core._send_results=send_results_v112


async def deliver_pending_v1123(bot):
    """Durable auto delivery with buttons tied to the exact signal row."""
    async with core._delivery_lock:
        core.expire_pending_deliveries(core.SIGNAL_COOLDOWN_HOURS)
        delivered=0
        for delivery_id,signal_id,chat_id,payload,attempts,symbol in core.pending_deliveries(100):
            try:
                await bot.send_message(
                    chat_id,payload,parse_mode=ParseMode.HTML,
                    reply_markup=signal_actions(signal_id=signal_id)
                )
            except Exception as exc:
                core.mark_delivery_failed(delivery_id,exc)
                core.log.exception(
                    "V11.4 signal delivery failed: signal=%s chat=%s attempt=%s",
                    signal_id,chat_id,attempts+1
                )
            else:
                core.mark_delivery_sent(delivery_id)
                delivered+=1
        return delivered


core._deliver_pending=deliver_pending_v1123


async def run_automatic_scan_v1123(context,scanner_fn,label):
    """Wait briefly for a concurrent scan instead of dropping the cycle immediately."""
    try:
        chats=core.subscribers()
        if not chats:
            return

        waited=0
        while core._scan_lock.locked() and waited<90:
            await asyncio.sleep(5)
            waited+=5
        if core._scan_lock.locked():
            core.log.warning("V11.4.1 automatic %s skipped after waiting %ss",label,waited)
            return

        async with core._scan_lock:
            all_results=await scanner_fn()

        fresh=[
            r for r in all_results
            if not core.was_sent_recently(
                r.symbol,r.side,core.SIGNAL_COOLDOWN_HOURS,r.timeframe
            )
        ]
        if not fresh:
            core.log.info("automatic %s scan completed: no new signals",label)
            return

        for index,result in enumerate(fresh):
            result.feature_snapshot.setdefault("delivery_meta",{})["source"]="auto_scan"
            signal_id=core.save_pending(result)
            payload=core.fmt(result,index==0)
            for chat_id in chats:
                core.enqueue_delivery(signal_id,chat_id,payload)

        delivered=await core._deliver_pending(context.bot)
        core.log.info(
            "V11.4.1 automatic %s signals queued=%s delivered=%s waited=%ss",
            label,len(fresh)*len(chats),delivered,waited
        )
    except Exception:
        core.log.exception("V11.4.1 automatic %s scan failed",label)


core._run_automatic_scan=run_automatic_scan_v1123


async def news_status_v114(update,context):
    snapshot=await core.get_news_sentiment(force=True)
    if snapshot.get("v114_news_degraded"):
        reason=escape(str(snapshot.get("v114_news_reason") or "источники недоступны"))
        return await update.effective_message.reply_text(
            "📰 <b>NEWS RADAR · DEGRADED</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Реальных источников: <b>0</b>\n"
            "Новостные бонусы и направления: <b>ОТКЛЮЧЕНЫ</b>\n"
            "Production-порог: <b>+2 пункта</b>\n"
            f"Причина: <b>{reason}</b>\n\n"
            "Binance price/OI/taker/ADL/L2 анализ продолжает работать. "
            "Бот не считает отсутствие новостей позитивным или негативным сигналом.",
            parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
    if _original_news_status is not None:
        return await _original_news_status(update,context)


if _original_news_status is not None:
    core.news_status=news_status_v114


async def callback_v112(update,context):
    query=update.callback_query
    data=str(query.data or "")
    if data.startswith("v11i:") or data.startswith("v11s:"):
        await query.answer()
        try:
            ref,action,ident=data.split(":",2)
            text=(
                detail_by_signal_id(action,int(ident))
                if ref=="v11i"
                else detail_by_snapshot_id(action,int(ident))
            )
            markup=(
                signal_actions(signal_id=int(ident))
                if ref=="v11i"
                else signal_actions(snapshot_id=int(ident))
            )
            return await query.message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=markup)
        except Exception:
            core.log.exception("V11.4 exact detail callback failed")
            return await query.message.reply_text("⚠️ Не удалось открыть точный снимок сигнала.",reply_markup=main_menu())
    if data=="v114:entry":
        await query.answer()
        return await query.message.reply_text(
            entry_quality_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
    if data=="v113:meta":
        await query.answer()
        return await query.message.reply_text(
            meta_report_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
    if data=="v113:robust":
        await query.answer()
        return await query.message.reply_text(
            robustness_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
    if data=="v112:lab":
        await query.answer()
        return await query.message.reply_text(lab_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v112:health":
        await query.answer()
        h=await health_check(force=True)
        return await query.message.reply_text(health_text(h),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v11:menu":
        await query.answer()
        return await query.message.reply_text("🏠 <b>ГЛАВНОЕ МЕНЮ V11.4.1</b>",parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data.startswith("v11:"):
        await query.answer()
        try:
            parts=data.split(":")
            if len(parts)<3:
                return
            action,symbol=parts[1],parts[2]
            timeframe=parts[3] if len(parts)>3 and parts[3] else None
            func={"why":why_text,"risk":risk_text,"stats":stats_text,"life":life_text}.get(action)
            if func:
                return await query.message.reply_text(
                    func(symbol,timeframe),
                    parse_mode=ParseMode.HTML,
                    reply_markup=signal_actions(symbol,timeframe)
                )
        except Exception:
            core.log.exception("V11.4 detail callback failed")
            return await query.message.reply_text("⚠️ Не удалось открыть детали.",reply_markup=main_menu())
        return
    return await _original_callback(update,context)


core.callback=callback_v112


async def history_v1123(update,context):
    """Show final PRO rank instead of confusing it with the core strategy score."""
    try:
        with sqlite3.connect(config.DATABASE_PATH,timeout=10) as c:
            rows=c.execute("""
                SELECT s.created_at,s.symbol,s.timeframe,s.side,s.score,s.status,s.result,s.pnl_r,
                       s.setup_type,a.champion_rank,a.challenger_rank,a.grade
                FROM signals s
                LEFT JOIN v11_rank_audit a ON a.signal_id=s.id
                WHERE COALESCE(s.is_shadow,0)=0
                  AND COALESCE(s.delivery_state,'DELIVERED')='DELIVERED'
                ORDER BY s.id DESC LIMIT 30
            """).fetchall()
        if not rows:
            text="🗂 История сигналов пока пуста."
        else:
            names={"TP2":"✅ TP2","SL":"🛑 STOP","ENTRY_EXPIRED":"⌛ НЕ АКТИВИРОВАН",
                   "INVALIDATED":"🚫 ОТМЕНЁН ДО ВХОДА","EXPIRED":"⏱ ЗАВЕРШЁН",
                   "AMBIGUOUS_ENTRY_STOP":"❔ НЕОДНОЗНАЧНАЯ 1M СВЕЧА: ENTRY/STOP",
                   "AMBIGUOUS_SL_TP":"❔ НЕОДНОЗНАЧНАЯ 1M СВЕЧА: SL/TP"}
            lines=["🗂 <b>ИСТОРИЯ · PRODUCTION</b>","━━━━━━━━━━━━━━━━━━"]
            for created,symbol,tf,side,core_score,status,result,pnl,setup,pro,challenger,grade in rows:
                icon="🟢" if side=="LONG" else "🔴"
                outcome=names.get(result,"🟡 ОЖИДАЕТ" if status in ("SENT","WAITING") else "🔵 АКТИВЕН")
                if pnl is not None and result not in (
                    "ENTRY_EXPIRED","INVALIDATED","AMBIGUOUS_ENTRY_STOP",
                    "AMBIGUOUS_SL_TP",None
                ):
                    outcome+=f" · {float(pnl):+.2f}R"
                rank=(f"PRO {float(pro):.1f}/{escape(str(grade or '—'))}" if pro is not None
                      else f"core {float(core_score):.0f}")
                lines.append(
                    f"{icon} {str(created)[:16]} · <b>{escape(str(symbol))}</b> · {escape(str(tf))} · {escape(str(side))}\n"
                    f"└ <b>{rank}</b> · {escape(str(setup or '—'))} · {outcome}"
                )
            text="\n".join(lines)
    except Exception:
        core.log.exception("V11.4 history failed")
        text="⚠️ Не удалось открыть Production-историю."
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=main_menu())


core.status=history_v1123


async def system_v112(update,context):
    global _last_health,_last_integrity_clock
    try:
        state,h,clock=await asyncio.gather(
            core.market_state(),health_check(),integrity_clock_status()
        )
        _last_health=h
        _last_integrity_clock=clock
        thresholds=thresholds_v112(state)
        breadth=state.get("breadth",{}) or {}
        scans=core.scan_status(); main=scans.get("main",{}); short=scans.get("short",{})
        w_main=factor_weights('1H'); w_short=factor_weights('15M')
        meta_main,_=meta_model("1H"); meta_short,_=meta_model("15M")
        dbs=db_runtime_status()
        nstate=news_runtime_state()
        gov=request_governor_status()
        text=(
            "🛡 <b>KORKOVTS V11.4.1 PRECISION AUDIT</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"BTC <b>{escape(str(state.get('bias','?')))}</b> · ATR <b>{float(state.get('btc_atr_pct',0)):.2f}%</b>\n"
            f"Regime <b>{escape(str(thresholds.get('regime_profile','?')))}</b> · "
            f"Breadth <b>{float(breadth.get('up_ratio',.5))*100:.0f}%</b>\n"
            f"Health <b>{h.status}</b> · REST <b>{h.rest_latency_ms:.0f}ms</b> · BTC age <b>{h.candle_age_sec:.0f}s</b>\n"
            f"Binance clock <b>{'OK' if (_last_integrity_clock and _last_integrity_clock.ok) else 'CHECK/PAUSE'}</b> · "
            f"offset <b>{float(getattr(_last_integrity_clock,'offset_ms',0)):.0f}ms</b> · "
            f"RTT <b>{float(getattr(_last_integrity_clock,'rtt_ms',0)):.0f}ms</b>\n"
            f"DB <b>{'PERSISTENT' if h.db_persistent else 'LOCAL'}</b> · "
            f"WAL <b>{escape(str(dbs.get('journal','?')).upper())}</b> · "
            f"busy <b>{int(dbs.get('busy_timeout_ms',0))}ms</b>\n"
            f"News <b>{'UNKNOWN' if not nstate.get('checked') else ('DEGRADED +2' if nstate.get('degraded') else 'HEALTHY')}</b> · "
            f"real sources <b>{int(nstate.get('healthy_sources',0))}</b>\n"
            f"Binance governor requests <b>{int(gov.get('requests',0))}</b> · "
            f"rate-limit failures <b>{int(gov.get('rate_limit_failures',0))}</b> · "
            f"cooldown <b>{float(gov.get('cooldown_seconds',0)):.0f}s</b>\n"
            f"{system_extra()}\n\n"
            f"🏆 Main <b>{main.get('liquid',0)} → {main.get('prefiltered',0)} → {main.get('final',0)}</b>\n"
            f"⚡ Short <b>{short.get('liquid',0)} → {short.get('prefiltered',0)} → {short.get('final',0)}</b>\n\n"
            f"Pool: <b>{V11_CANDIDATE_POOL}</b> qualified candidates · "
            f"execution rejects Main/Short <b>{main.get('execution_rejected',0)}/{short.get('execution_rejected',0)}</b> · "
            f"meta rejects <b>{main.get('meta_rejected',0)}/{short.get('meta_rejected',0)}</b>\n"
            f"Meta 1H/15M <b>{meta_main.status}/{meta_short.status}</b> · samples <b>{meta_main.n}/{meta_short.n}</b>\n"
            f"duration Main/Short <b>{main.get('v1141_duration_sec','—')}s/{short.get('v1141_duration_sec','—')}s</b>\n"
            "🧬 1H weights: " + " · ".join(f"{k} <b>{v:.2f}</b>" for k,v in w_main.items())
            + "\n🧬 15M weights: " + " · ".join(f"{k} <b>{v:.2f}</b>" for k,v in w_short.items())
        )
    except Exception:
        core.log.exception("V11.4 system status failed")
        text="⚠️ <b>DATA DEGRADED</b>\nПолный системный статус недоступен."
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=main_menu())


core.system_status=system_v112


async def meta_refresh_job(context=None):
    """Refresh walk-forward models in worker threads; never block Telegram scans."""
    try:
        await asyncio.gather(
            asyncio.to_thread(meta_model,"1H",True),
            asyncio.to_thread(meta_model,"15M",True),
        )
        core.log.info("V11.4.1 Meta Precision models refreshed")
    except Exception:
        core.log.exception("V11.4.1 Meta Precision refresh failed")


async def db_maintenance_job(context=None):
    try:
        await asyncio.to_thread(db_checkpoint)
        await asyncio.to_thread(backup_if_due,7)
    except Exception:
        core.log.exception("V11.4.1 database maintenance failed")


async def lifecycle_job(context):
    try: await observe_lifecycle(context.bot,notify=True)
    except Exception: core.log.exception("V11.4.1 lifecycle job failed")


async def structure_job(context):
    try: await structure_watch(context.bot,notify=True)
    except Exception: core.log.exception("V11.4.1 structure job failed")


async def post_init_v112(application):
    global _live_task
    await asyncio.to_thread(harden_database)
    init_rank_audit(); init_lifecycle(); init_details()
    await _original_post_init(application)
    await db_maintenance_job()

    # Build the research models before polling begins, but on worker threads so
    # CPU-heavy walk-forward validation never stalls the asyncio loop.
    await meta_refresh_job()

    _live_task=asyncio.create_task(live_monitor(),name="v114-binance-live-market")
    application.job_queue.run_repeating(lifecycle_job,interval=30,first=45,name="v114-live-lifecycle")
    application.job_queue.run_repeating(structure_job,interval=300,first=180,name="v114-structure-watch")
    application.job_queue.run_repeating(
        meta_refresh_job,interval=6*3600,first=6*3600,name="v114-meta-refresh"
    )
    application.job_queue.run_repeating(
        db_maintenance_job,interval=6*3600,first=6*3600,name="v114-db-maintenance"
    )


core.post_init=post_init_v112


async def post_shutdown_v112(application):
    global _live_task
    if _live_task:
        _live_task.cancel()
        try: await _live_task
        except asyncio.CancelledError: pass
        _live_task=None
    await _original_post_shutdown(application)


core.post_shutdown=post_shutdown_v112


if __name__=="__main__":
    core.main()
