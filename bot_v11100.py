"""Korkovts Signal AI V11.10.0 · COMPETITIVE EDGE."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from html import escape

from telegram.constants import ParseMode
from telegram.ext import CommandHandler

import app.bot as core
import app.config as config
import app.db as db
import app.scanner as scanner

from v1171_sqlite import db_session
from v11_engine import attach, classify_regime, init_rank_audit, record_rank_audit
from v11_liquidity import annotate as annotate_liquidity
from v11_live import (
    monitor as live_monitor, health as live_health,
    set_extra_symbol_provider as set_live_extra_symbol_provider,
)
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
    status as db_runtime_status, install_connect_wrapper,
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
from v1142_entry_now import (
    init as init_entry_now,
    arm as arm_entry_now,
    active_rows as active_entry_rows,
    active_symbols as active_entry_symbols,
    get_row as entry_row,
    assess_row as assess_entry_row,
    assess_signal as assess_entry_signal,
    record_check as record_entry_check,
    mark_pending_delivery as mark_entry_pending_delivery,
    mark_delivery_uncertain as mark_entry_delivery_uncertain,
    mark_triggered as mark_entry_triggered,
    mark_shadowed as mark_entry_shadowed,
    cancel as cancel_entry_arm,
    status_text as entry_now_status_text,
)
from v1142_risk import (
    init as init_futures_safety,
    status as futures_safety_status,
    shadow_reason_for as futures_shadow_reason,
    text as futures_safety_text,
    active_trades_text as futures_active_text,
)
from spot_scanner import (
    scan as spot_scan, status as spot_scan_status,
    recheck_watch as spot_recheck_watch,
    fresh_derivatives_risk as spot_fresh_derivatives_risk,
    active_correlation_risk as spot_active_correlation_risk,
)
from spot_db import (
    SPOT_RELEASE_VERSION,
    init as init_spot_db, save as save_spot_signal,
    was_sent_recently as spot_was_sent_recently,
    enqueue_delivery as enqueue_spot_delivery,
    pending_deliveries as pending_spot_deliveries,
    expire_pending_deliveries as expire_spot_deliveries,
    mark_delivery_sent as mark_spot_delivery_sent,
    mark_delivery_uncertain as mark_spot_delivery_uncertain,
    mark_delivery_failed as mark_spot_delivery_failed,
    claim_delivery as claim_spot_delivery,
    expire_stuck_sending as expire_stuck_spot_sending,
    delivery_context as spot_delivery_context,
    expire_delivery as expire_spot_delivery,
    active_portfolio_clusters as spot_active_clusters,
    portfolio_reserved_signals as spot_reserved_signals,
    portfolio_reserved_count as spot_reserved_count,
)
from spot_tracker import update_all as update_spot_outcomes
from spot_watch import (
    init as init_spot_watch,
    upsert as upsert_spot_watch,
    active as active_spot_watches,
    get as get_spot_watch,
    count_active as active_spot_watch_count,
    record_check as record_spot_watch_check,
    record_ready as record_spot_ready,
    reset_ready as reset_spot_ready,
    close as close_spot_watch,
    reconcile_pending_delivery as reconcile_spot_watch_delivery,
    text as spot_watch_text,
)
from v1170_evidence import futures as futures_evidence_audit
from v11100_edge import (
    annotate_many as annotate_decision_edges, selection_key as decision_selection_key,
)
from v11100_protections import apply_many as apply_v11100_protections
from v11100_data import validate_snapshot as validate_v11100_snapshot
from v11100_stability import annotate as annotate_v11100_stability
from v11100_blackbox import (
    init as init_v11100_blackbox, new_scan_id as new_v11100_scan_id,
    record as record_v11100_blackbox, record_many as record_many_v11100_blackbox,
)
from spot_ui import (
    card as spot_card, history_text as spot_history_text,
    stats_text as spot_stats_text, active_text as spot_active_text,
    system_text as spot_system_text,
)
from spot_market import (
    close as close_spot_http, book_ticker as spot_book_ticker,
    depth as spot_depth, agg_trades as spot_agg_trades,
    klines as spot_klines,
)
from spot_microstructure import analyze_book as spot_analyze_book
from spot_news import assess as spot_assess_news
from spot_orderbook import (
    monitor as spot_book_monitor, stop as stop_spot_book_monitor,
    set_symbol_provider as set_spot_book_symbol_provider,
    snapshot as spot_local_book, stability as spot_book_stability,
    health as spot_orderbook_health,
)
from v1180_lab import (
    init as init_v1180_lab, record as record_v1180_decision,
    sync_outcomes as sync_v1180_outcomes, text as v1180_lab_text,
)
from v1180_manager import (
    init as init_v1180_manager, observe as observe_v1180_manager,
    sync_failures as sync_v1180_failures, text as v1180_manager_text,
)
from v1171_delivery import (
    init as init_futures_delivery,
    pending as pending_futures_deliveries,
    context as futures_delivery_context,
    expire as expire_futures_delivery,
    mark_uncertain as mark_futures_delivery_uncertain,
    expire_all_for_signal as expire_all_futures_deliveries,
    delivery_id as futures_delivery_id,
    age_seconds as futures_delivery_age,
    other_live_count as futures_other_live_count,
    claim as claim_futures_delivery,
    expire_stuck_sending as expire_stuck_futures_sending,
    reconcile_failed_arms as reconcile_failed_futures_arms,
)

# Install the closing SQLite factory before core.main() can call app.db.init().
# harden_database() still performs WAL/quick_check later with the original driver.
install_connect_wrapper()
tracker_module.open_signals=delivery_aware_open_signals
install_ambiguous_tracker()
install_request_governor()

APP_VERSION="11.10.0"
# V11.7 independent-evidence gating materially changes Futures selection, so this release uses a fresh cohort.
FUTURES_RELEASE_VERSION="11.7.1-futures-evidence"
STRATEGY_VERSION=FUTURES_RELEASE_VERSION

config.APP_VERSION=APP_VERSION
config.STRATEGY_VERSION=FUTURES_RELEASE_VERSION
core.APP_VERSION=APP_VERSION
core.STRATEGY_VERSION=FUTURES_RELEASE_VERSION
# app.db stamps Futures rows with the isolated V11.7.1 evidence cohort; Spot has its own table/version.
db.APP_VERSION=FUTURES_RELEASE_VERSION
db.STRATEGY_VERSION=FUTURES_RELEASE_VERSION


# python-telegram-bot uses httpx. INFO-level httpx request lines include the Bot
# API URL, which contains the secret token. Never emit those URLs in Railway logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_last_regime=None
_last_health=None
_last_integrity_clock=None
_last_neutral=False
_live_task=None
_spot_book_task=None
_entry_now_lock=asyncio.Lock()
_spot_delivery_lock=asyncio.Lock()
_spot_candidate_lock=asyncio.Lock()
SPOT_AUTO_INTERVAL_MIN=max(15,min(180,int(os.getenv("SPOT_AUTO_INTERVAL_MIN","30"))))
# Safety caps: environment variables may make delivery stricter, never looser.
SPOT_DELIVERY_MAX_AGE_MIN=max(5,min(10,int(os.getenv("SPOT_DELIVERY_MAX_AGE_MIN","10"))))
FUTURES_DELIVERY_MAX_AGE_MIN=max(2,min(3,int(os.getenv("FUTURES_DELIVERY_MAX_AGE_MIN","3"))))
SPOT_WATCH_INTERVAL_MIN=max(2,min(15,int(os.getenv("SPOT_WATCH_INTERVAL_MIN","2"))))

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
    payload["v1142_acquire_started_ms"]=started_wall
    payload["v1142_acquire_finished_ms"]=finished
    payload["v1142_acquire_duration_ms"]=(time.perf_counter()-started)*1000
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
    base=kwargs.get("df")
    higher=kwargs.get("higher")
    lower=kwargs.get("lower")
    if derivatives is None and len(args)>7:
        derivatives=args[7]
    if timeframe is None and len(args)>1:
        timeframe=args[1]
    if base is None and len(args)>2:
        base=args[2]
    if higher is None and len(args)>3:
        higher=args[3]
    if lower is None and len(args)>5:
        lower=args[5]

    # V11.10 centralized causal market-data contract.  A candidate cannot be
    # created from a future/stale/gapped candle snapshot.  Missing timestamp
    # metadata is reported but kept fail-open for legacy tests; Binance live
    # frames always expose open_time.
    coherence=validate_v11100_snapshot(timeframe,lower,base,higher)
    if not coherence.eligible:
        audit=kwargs.get("audit")
        if audit is None and len(args)>10:
            audit=args[10]
        if isinstance(audit,dict):
            audit.setdefault("issues",[]).append(
                f"market-data coherence failed: {coherence.reason}"
            )
            audit["data_coherence_v11100"]=coherence.as_dict()
        return None

    if derivatives is not None:
        acquire=float((derivatives or {}).get("v1142_acquire_duration_ms",0) or 0)
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
    if result is not None:
        result.feature_snapshot.setdefault("data_coherence_v11100",{}).update(coherence.as_dict())
    if result is not None and derivatives is not None:
        acquire=float((derivatives or {}).get("v1142_acquire_duration_ms",0) or 0)
        result.feature_snapshot.setdefault("data_freshness_v1142",{}).update({
            "derivatives_acquire_ms":acquire,
            "acquire_started_ms":float((derivatives or {}).get("v1142_acquire_started_ms",0) or 0),
            "acquire_finished_ms":float((derivatives or {}).get("v1142_acquire_finished_ms",0) or 0),
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
        members.sort(key=decision_selection_key,reverse=True)
        for rank,signal in enumerate(members,1):
            signal.cluster_rank=rank
            signal.feature_snapshot.setdefault("portfolio",{}).update({
                "cluster_id":cid,
                "cluster_size":len(members),
                "cluster_rank":rank,
            })
    return rows


def _portfolio_select(rows,max_results=4):
    rows=sorted(rows,key=decision_selection_key,reverse=True)
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
        core.log.exception("V11.8.1 counterfactual shadow save failed")
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

    scan_id=new_v11100_scan_id(kind)
    for signal in results:
        signal.feature_snapshot["v11100_scan_id"]=scan_id
    record_many_v11100_blackbox(
        results,"RAW_QUALIFIED",scan_id=scan_id,pipeline=kind,
        extra={"candidate_count":len(results)},
    )

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
            signal.feature_snapshot.setdefault("exchange_meta_v1142",{}).update({
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
            (signal.feature_snapshot.get("pipeline_v1142") or {}).get("raw_scan_sec",0) or 0
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
        signal.feature_snapshot.setdefault("entry_quality_v1142",{}).update({
            **entry_stats,"penalty":entry_penalty,
        })
        if entry_penalty<0:
            _freeze_final_rank(signal,float(signal.professional_rank)+entry_penalty)
            if signal.professional_rank<75:
                signal.feature_snapshot["entry_quality_v1142"]["rejected"]=True
                entry_rejected.append(signal)
                continue

        execution_valid.append(signal)

    # Independent evidence-family audit. Correlated technical indicators do not
    # count as separate confirmations here; a hard disagreement from order flow,
    # market regime, execution, positioning or event risk vetoes the candidate.
    evidence_valid=[]
    evidence_rejected=[]
    for signal in execution_valid:
        audit=futures_evidence_audit(signal)
        if not audit.eligible:
            evidence_rejected.append(signal)
            continue
        # Negative-only redundancy haircut: correlated technical indicators may
        # create a high base score, but only independent families can preserve it.
        evidence_penalty=max(0.0,(6-int(audit.support))*1.5+int(audit.conflict)*2.0)
        signal.feature_snapshot.setdefault("evidence_v117",{})["rank_penalty"]=evidence_penalty
        if evidence_penalty>0:
            _freeze_final_rank(signal,float(signal.professional_rank)-evidence_penalty)
            if signal.professional_rank<75:
                evidence_rejected.append(signal)
                continue
        evidence_valid.append(signal)

    # Meta-label layer is shadow-only until its own chronological OOS tests say
    # READY. Once READY, it can reject low-confidence candidates; it still cannot
    # rescue any candidate rejected above.
    meta_valid=[]
    meta_rejected=[]
    for signal in evidence_valid:
        signal,decision=meta_decide(signal)
        if decision.ready and not decision.eligible:
            meta_rejected.append(signal)
            continue
        meta_valid.append(signal)

    # V11.10 adaptive protection matrix.  This is strictly negative-only:
    # repeated recent realised failures can temporarily de-risk or quarantine
    # a cohort, while positive history can never promote a candidate here.
    protection_valid=[]
    protection_rejected=[]
    for signal,protection in apply_v11100_protections(meta_valid):
        if not protection.eligible:
            protection_rejected.append(signal)
            continue
        if float(protection.penalty)>0:
            _freeze_final_rank(signal,float(signal.professional_rank)-float(protection.penalty))
            if signal.professional_rank<75:
                protection_rejected.append(signal)
                continue
        protection_valid.append(signal)

    # V11.10 correlation-aware forward-edge overlay. It cannot rescue a
    # rejected candidate. Positive history must survive UTC-day block bootstrap
    # so one correlated market burst cannot manufacture confidence.
    protection_valid=annotate_decision_edges(protection_valid)
    _refresh_cluster_ranks(protection_valid)
    chosen=_portfolio_select(protection_valid,final_limit)
    chosen=annotate_v11100_stability(chosen,protection_valid)
    chosen_ids={id(x) for x in chosen}

    # Append-only decision black box.  FINAL_DECISION stores the complete
    # candidate set, including portfolio shadows, so future releases can replay
    # exactly the same decision-layer inputs.
    record_many_v11100_blackbox(
        protection_valid,"FINAL_DECISION",selected_ids=chosen_ids,scan_id=scan_id,pipeline=kind,
        extra={"final_limit":final_limit,"source_regime":source_regime.name},
        selected_order=[id(x) for x in chosen],
    )
    record_many_v11100_blackbox(alpha_rejected,"ALPHA_REJECT",scan_id=scan_id,pipeline=kind)
    record_many_v11100_blackbox(metadata_rejected,"METADATA_REJECT",scan_id=scan_id,pipeline=kind)
    record_many_v11100_blackbox(execution_rejected,"EXECUTION_REJECT",scan_id=scan_id,pipeline=kind)
    record_many_v11100_blackbox(entry_rejected,"ENTRY_REJECT",scan_id=scan_id,pipeline=kind)
    record_many_v11100_blackbox(evidence_rejected,"EVIDENCE_REJECT",scan_id=scan_id,pipeline=kind)
    record_many_v11100_blackbox(meta_rejected,"META_REJECT",scan_id=scan_id,pipeline=kind)
    record_many_v11100_blackbox(protection_rejected,"PROTECTION_REJECT",scan_id=scan_id,pipeline=kind)

    # Counterfactuals are labelled by the exact layer that removed them.
    for s in alpha_rejected:
        _store_counterfactual(s,"V1142_ALPHA_REJECT")
    for s in execution_rejected:
        _store_counterfactual(s,"V1142_EXECUTION_REJECT")
    for s in entry_rejected:
        _store_counterfactual(s,"V1142_ENTRY_REJECT")
    for s in evidence_rejected:
        _store_counterfactual(s,"V1170_EVIDENCE_CONFLICT")
    for s in meta_rejected:
        _store_counterfactual(s,"V1142_META_REJECT")
    for s in protection_rejected:
        _store_counterfactual(s,"V11100_PROTECTION_REJECT")
    for s in protection_valid:
        if id(s) not in chosen_ids:
            _store_counterfactual(s,"V11100_PORTFOLIO")

    try:
        d=scanner._last_scan[kind]
        d["production_pool"]=len(results)
        d["pre_v1142_final"]=len(results)
        d["final"]=len(chosen)
        d["v1142_filtered"]=len(results)-len(chosen)
        d["alpha_rejected"]=len(alpha_rejected)
        d["metadata_rejected"]=len(metadata_rejected)
        d["execution_rejected"]=len(execution_rejected)
        d["entry_rejected"]=len(entry_rejected)
        d["meta_rejected"]=len(meta_rejected)
        d["protection_rejected"]=len(protection_rejected)
        d["execution_revalidation_pool"]=len(revalidation_pool)
        d["portfolio_shadowed"]=sum(1 for s in protection_valid if id(s) not in chosen_ids)
        d["regime_profile"]=getattr(chosen[0],"production_regime",source_regime.name) if chosen else source_regime.name
        d["factor_weights"]=factor_weights("15M" if kind=="short" else "1H")
        meta_report,_=meta_model("15M" if kind=="short" else "1H")
        d["meta_status"]=meta_report.status
        d["meta_samples"]=meta_report.n
        d["v1142_top"]=[
            (
                s.symbol,
                round(float(s.professional_rank),1),
                round(float(getattr(s,"alpha_adjustment",0)),1),
                str(getattr(s,"l2_state","—")),
                round(float(getattr(s,"meta_score",.5)),2),
                (None if getattr(s,"expected_net_r_lcb",None) is None
                 else round(float(s.expected_net_r_lcb),3)),
                int(getattr(s,"edge_sample_n",0) or 0),
            )
            for s in chosen
        ]
    except Exception:
        core.log.exception("V11.10.0 diagnostics update failed")
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
            signal.feature_snapshot.setdefault("pipeline_v1142",{}).update({
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
            d["reason"]=f"V11.10.0 Production: {type(exc).__name__}: {exc}"
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
            scanner._last_scan.setdefault(kind,{})["v1142_duration_sec"]=round(
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


def _decorate_entry_state(signal,state,assessment=None,arm_id=None,streak=0):
    signal.entry_now_state=str(state)
    signal.entry_now_arm_id=int(arm_id) if arm_id is not None else None
    signal.entry_now_streak=int(streak or 0)
    if assessment is not None:
        signal.entry_now_score=float(assessment.score)
        signal.entry_now_price=float(assessment.price)
        signal.entry_now_reason=str(assessment.reason)
        signal.entry_now_spread_bps=float(assessment.spread_bps)
        signal.entry_now_flow_share=float(assessment.flow_share)
        signal.entry_now_flow_source=str(assessment.flow_source)
        signal.feature_snapshot.setdefault("entry_now_v1142",{}).update({
            "state":str(state),
            "readiness_score":float(assessment.score),
            "price":float(assessment.price),
            "bid":float(assessment.bid),
            "ask":float(assessment.ask),
            "spread_bps":float(assessment.spread_bps),
            "distance_r":float(assessment.distance_r),
            "one_min_ok":bool(assessment.one_min_ok),
            "three_min_ok":bool(assessment.three_min_ok),
            "flow_ok":bool(assessment.flow_ok),
            "flow_share":float(assessment.flow_share),
            "flow_source":str(assessment.flow_source),
            "candle_ok":bool(assessment.candle_ok),
            "volume_ratio":float(assessment.volume_ratio),
            "reason":str(assessment.reason),
            "confirm_streak":int(streak or 0),
            "arm_id":int(arm_id) if arm_id is not None else None,
            "checked_at_epoch":float(assessment.checked_at),
        })
    return signal


async def _refresh_candidate_v1142(row):
    """Rebuild one ARMED candidate from fresh market data before ENTRY NOW."""
    symbol=str(row["symbol"]).upper()
    timeframe=str(row["timeframe"]).upper()
    kind="short" if timeframe=="15M" else "main"

    if not await _health_gate(kind):
        return None

    token=begin_news_context()
    try:
        if timeframe=="15M":
            lower_i,base_i,higher_i="5m","15m","1h"
            threshold_key="short"
        else:
            lower_i,base_i,higher_i="15m","1h","4h"
            threshold_key="main"

        lower,base,higher,derivatives,state,news=await asyncio.gather(
            core.get_klines(symbol,lower_i,320),
            core.get_klines(symbol,base_i,400),
            core.get_klines(symbol,higher_i,400),
            core.get_derivatives_snapshot(symbol),
            core.market_state(),
            core.get_news_sentiment(),
        )
        if not derivatives.get("deep_data"):
            return None

        oi_notional=(
            float(derivatives.get("open_interest",0))
            *float(derivatives.get("mark_price",0))
        )
        derivatives.update(core.liquidation_snapshot(symbol,oi_notional))
        analysis_state,_=core.market_analysis_state(state)
        threshold=float(thresholds_v112(state)[threshold_key])

        result=core.analyze(
            symbol,timeframe,base,higher,threshold,lower,
            analysis_state["bias"],derivatives,core.for_symbol(news,symbol),
            analysis_state
        )
        if not result:
            return None

        # Apply the same mature calibration penalty used by the broad scanner.
        penalty=float(db.calibration_penalty(symbol,result.side,timeframe) or 0)
        if penalty>0:
            result=core.analyze(
                symbol,timeframe,base,higher,min(95.0,threshold+penalty),lower,
                analysis_state["bias"],derivatives,core.for_symbol(news,symbol),
                analysis_state
            )
            if not result:
                return None

        if bool((news or {}).get("v114_news_degraded")):
            result.market_context=dict(getattr(result,"market_context",{}) or {})
            result.market_context["news_degraded"]=True
            result.feature_snapshot.setdefault("news",{}).update({
                "degraded":True,"sources":0,"real_sources":0,
                "degraded_reason":str(
                    (news or {}).get("v114_news_reason") or "unavailable"
                ),
            })

        prepared=await _prepare([result],kind)
        if not prepared:
            return None
        fresh=prepared[0]
        if str(fresh.side).upper()!=str(row["side"]).upper():
            return None
        return fresh
    finally:
        end_news_context(token)


async def _arm_and_check(signal,source):
    arm_id=arm_entry_now(signal,source)
    assessment=await assess_entry_signal(signal)
    streak=record_entry_check(arm_id,assessment)
    state=(
        "READY_PENDING"
        if assessment.state=="READY" and streak<2
        else ("ARMED" if assessment.state!="CANCEL" else "CANCELLED")
    )
    return _decorate_entry_state(signal,state,assessment,arm_id,streak),assessment,streak


async def _trigger_arm(context,arm_id,initial_assessment=None,force_chat_id=None):
    """Convert an ARMED setup into a production signal only after fresh full revalidation."""
    async with _entry_now_lock:
        row=entry_row(arm_id)
        if not row or str(row.get("status"))!="ACTIVE":
            return None
        subscriber_chats=set(int(x) for x in core.subscribers())
        chats=list(subscriber_chats)
        forced=None
        if force_chat_id is not None:
            forced=int(force_chat_id)
            if forced not in chats:
                chats.append(forced)
        if not chats:
            # Auto alerts were disabled after the setup was armed. Keep the
            # journal intact but do not create an undeliverable production trade.
            return None

        if core.was_sent_recently(
            row["symbol"],row["side"],core.SIGNAL_COOLDOWN_HOURS,row["timeframe"]
        ):
            cancel_entry_arm(arm_id,"production cooldown already active")
            return None

        fresh=await _refresh_candidate_v1142(row)
        if fresh is None:
            cancel_entry_arm(arm_id,"fresh full Production revalidation failed")
            return None

        final_assessment=await assess_entry_signal(fresh)
        if final_assessment.state!="READY" or float(final_assessment.score)<80:
            record_entry_check(arm_id,final_assessment)
            return None

        # The historical streak proved persistence. The fresh full pipeline above
        # proves the setup still exists now.
        streak=max(2,int(row.get("confirm_streak") or 0))
        _decorate_entry_state(fresh,"ENTER_NOW",final_assessment,arm_id,streak)
        fresh.feature_snapshot.setdefault("delivery_meta",{})["source"]="entry_now_v1142"

        safety=futures_safety_status()
        if not safety.allow_live:
            reason=futures_shadow_reason(safety)
            if reason:
                shadow_id=None
                if not db.was_shadowed_recently(
                    fresh.symbol,fresh.side,fresh.timeframe,reason,1
                ):
                    shadow_id=db.save_shadow(fresh,reason)
                    # ENTRY NOW means the hypothetical trade begins now; shadow
                    # recovery must not wait for a later candle to "enter".
                    db.activate_signal(
                        shadow_id,datetime.now(timezone.utc).isoformat()
                    )
                mark_entry_shadowed(
                    arm_id,shadow_id,
                    f"{safety.mode}: {safety.reason}"
                )
                core.log.warning(
                    "V11.8 safety %s: shadowed %s %s %s instead of live delivery",
                    safety.mode,fresh.symbol,fresh.side,fresh.timeframe
                )
                return "SHADOW",fresh,shadow_id,safety

            # POSITION_BUSY: do not terminate the ARMED opportunity as a fake
            # loss/shadow. It may still trigger later while inside its TTL.
            return "BLOCKED",fresh,None,safety

        signal_id=core.save_pending(fresh)
        record_v1180_decision("FUTURES",signal_id,fresh)
        payload=core.fmt(fresh,True)
        for chat_id in chats:
            core.enqueue_delivery(signal_id,chat_id,payload)
        # Remove this arm from the 30-second trigger loop while Telegram delivery
        # is pending, but keep the symbol on the live WS for retry revalidation.
        mark_entry_pending_delivery(arm_id,signal_id)

        delivered=0
        if forced is not None and forced not in subscriber_chats:
            delivered+=await _deliver_forced_futures(
                context.bot,signal_id,forced,payload
            )
        delivered+=await core._deliver_pending(context.bot)

        if delivered>0:
            # Once the bot has told the user ENTRY NOW, the trade is immutable
            # and ACTIVE immediately. It will not disappear on the next scan.
            db.activate_signal(
                signal_id,datetime.now(timezone.utc).isoformat()
            )
            mark_entry_triggered(arm_id,signal_id)
            core.log.info(
                "V11.8 ENTRY NOW %s %s %s readiness=%.0f queued=%s delivered=%s",
                fresh.symbol,fresh.side,fresh.timeframe,
                float(final_assessment.score),len(chats),delivered
            )
            return "PRODUCTION",fresh,signal_id,safety

        # Delivery did not reach anyone. Do not claim an active trade.
        return "DELIVERY_PENDING",fresh,signal_id,safety


async def entry_now_monitor_job(context):
    """30-second watch of ARMED setups. Auto Telegram stays silent until ENTER_NOW."""
    try:
        rows=active_entry_rows(30)
        if not rows:
            return
        sem=asyncio.Semaphore(4)

        async def one(row):
            try:
                async with sem:
                    assessment=await asyncio.wait_for(assess_entry_row(row),timeout=20)
                streak=record_entry_check(row["id"],assessment)
                if assessment.state=="READY" and streak>=2:
                    await _trigger_arm(context,row["id"],assessment)
                return assessment.state
            except Exception:
                core.log.exception(
                    "V11.8 ENTRY NOW check failed for %s",row.get("symbol")
                )
                return "ERROR"

        await asyncio.gather(*(one(row) for row in rows))
    except Exception:
        core.log.exception("V11.8 ENTRY NOW monitor failed")


def _delivery_sent(signal_id,chat_id):
    try:
        with db_session(timeout=10) as c:
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
        result.feature_snapshot.setdefault("entry_quality_v1142",{}).update({
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
        if core.was_sent_recently(
            result.symbol,result.side,core.SIGNAL_COOLDOWN_HOURS,result.timeframe
        ):
            snapshot_id=save_snapshot(result,None)
            _decorate_entry_state(result,"COOLDOWN",None,None,0)
            return await msg.reply_text(
                "🧊 <b>НОВЫЙ ВХОД НЕ СОЗДАЁТСЯ</b>\n"
                "По этой монете/стороне/таймфрейму уже действует Production cooldown.\n\n"
                +core.fmt(result,True),
                parse_mode=ParseMode.HTML,
                reply_markup=signal_actions(snapshot_id=snapshot_id)
            )

        # V11.8.1: a good setup is first ARMED. Production history starts only
        # when a persistent micro trigger says ENTER NOW.
        result,assessment,streak=await _arm_and_check(result,"manual_symbol")
        if assessment.state=="CANCEL":
            return await msg.reply_text(
                f"⚪ <b>{symbol}</b>: сетап был сильным, но точка входа уже испорчена.\n"
                f"Причина: <b>{escape(assessment.reason)}</b>",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(symbol)
            )

        if assessment.state=="READY":
            await msg.reply_text(
                f"🟠 <b>{symbol} · ENTRY READY 1/2</b>\n"
                "Первое micro-подтверждение есть. Жду 20 секунд, чтобы не входить на одном случайном тике…",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(symbol)
            )
            await asyncio.sleep(22)
            row=entry_row(result.entry_now_arm_id)
            if row and row.get("status")=="ACTIVE":
                second=await assess_entry_row(row)
                streak=record_entry_check(row["id"],second)
                if second.state=="READY" and streak>=2:
                    triggered=await _trigger_arm(
                        context,row["id"],second,
                        force_chat_id=update.effective_chat.id
                    )
                    if triggered:
                        mode=triggered[0]
                        if mode=="PRODUCTION":
                            return
                        if mode=="SHADOW":
                            safety=triggered[3]
                            return await msg.reply_text(
                                "🛑 <b>РЕАЛЬНЫЙ FUTURES-ВХОД ЗАБЛОКИРОВАН</b>\n"
                                f"Safety mode: <b>{escape(safety.mode)}</b>\n"
                                f"Причина: {escape(safety.reason)}\n\n"
                                "Сетап был подтверждён как ENTRY NOW, но сохранён только "
                                "как shadow-forward test. Реальными деньгами этот сигнал "
                                "сейчас не открываем.",
                                parse_mode=ParseMode.HTML,
                                reply_markup=main_menu(symbol)
                            )
                        if mode=="BLOCKED":
                            safety=triggered[3]
                            return await msg.reply_text(
                                "🟡 <b>НОВЫЙ ВХОД ПОКА НЕ РАЗРЕШЁН</b>\n"
                                f"{escape(safety.reason)}",
                                parse_mode=ParseMode.HTML,
                                reply_markup=main_menu(symbol)
                            )
                        if mode=="DELIVERY_PENDING":
                            return await msg.reply_text(
                                "⚪ <b>ENTRY NOW НЕ ДОСТАВЛЕН</b>\n"
                                "Точка входа прошла анализ, но финальная live-проверка/"
                                "Telegram-доставка не завершилась успешно. <b>Не входить</b>, "
                                "пока отдельное 🚨 ENTRY NOW не пришло.",
                                parse_mode=ParseMode.HTML,
                                reply_markup=main_menu(symbol)
                            )
                assessment=second

        # Strong setup, but not a command to enter. Keep it under 30-second watch.
        _decorate_entry_state(
            result,
            "READY_PENDING" if assessment.state=="READY" else "ARMED",
            assessment,result.entry_now_arm_id,streak
        )
        snapshot_id=save_snapshot(result,None)
        return await msg.reply_text(
            "🟡 <b>СЕТАП СИЛЬНЫЙ, НО ВХОД НЕ СЕЙЧАС</b>\n"
            "Бот поставил монету в ARMED и будет проверять её каждые 30 секунд. "
            "Когда 1m + 3m + taker-flow + spread подтвердятся два раза подряд, "
            "придёт отдельное <b>🚨 ENTRY NOW</b>.\n\n"
            +core.fmt(result,True),
            parse_mode=ParseMode.HTML,
            reply_markup=signal_actions(snapshot_id=snapshot_id)
        )
    except Exception:
        core.log.exception("V11.10.0 symbol analysis failed for %s",symbol)
        await msg.reply_text("⚠️ Не удалось получить полный набор рыночных данных.",reply_markup=main_menu())


core.signal=signal_v1123


def _ready(signal):
    return signal if hasattr(signal,"professional_rank") else attach(signal,_last_regime)


def save_v112(signal,chat_id=None,shadow_reason=None):
    signal=stamp_lineage(_ready(signal))
    signal_id=_raw_save(signal,chat_id,shadow_reason)
    record_rank_audit(signal_id,signal)
    record_v11100_blackbox(
        signal,"PERSISTED",selected=True,pipeline="futures",
        extra={"signal_id":signal_id,"shadow_reason":shadow_reason},
    )
    return signal_id


def save_pending_v112(signal):
    signal=stamp_lineage(_ready(signal))
    signal_id=_raw_save_pending(signal)
    record_rank_audit(signal_id,signal)
    record_v11100_blackbox(
        signal,"PERSISTED_PENDING",selected=True,pipeline="futures",
        extra={"signal_id":signal_id},
    )
    return signal_id


core.save=save_v112
core.save_pending=save_pending_v112


async def start_v112(update,context):
    core.subscribe(update.effective_chat.id,True)
    text=(
        "◈ <b>YK CRYPTO SIGNAL AI</b>\n"
        "<i>Robust Edge · Private Trading System</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>🏆 <b>PRIME</b> — лучший Futures-кандидат\n"
        "⚡ <b>FAST</b> — краткосрочный 15M\n"
        "🟢 <b>SPOT</b> — swing 3–10 дней\n"
        "🚨 <b>ENTRY NOW</b> — только подтверждённый вход 2/2</blockquote>\n"
        "\n🧠 <b>AI CORE</b>\n"
        "Block-bootstrap · Cohort Guard · Data Coherence · Black Box · Replay\n"
        f"\n📡 Broad scan <b>{core.AUTO_SCAN_INTERVAL_MIN}m</b> · Entry monitor <b>30s</b>\n"
        "🛡 Safety gate · execution costs · freshness · circuit breaker\n"
        "\n<i>Выбери режим в YK Control Center.</i>"
    )
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=main_menu())


core.start=start_v112


async def send_results_v112(bot,chat_id,results,automatic=False,short=False,diagnostics=None):
    if not results:
        if automatic:
            return None
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
                "Новые входы не выдаются, пока временная синхронизация не восстановится.",
                parse_mode=ParseMode.HTML,reply_markup=main_menu(),
            )
        details=""
        if diagnostics and diagnostics.get("status")=="ok":
            details=(
                f"\n\nВоронка: <b>{diagnostics.get('liquid',0)}</b> → "
                f"<b>{diagnostics.get('prefiltered',0)}</b> → "
                f"<b>{diagnostics.get('pre_v1142_final',diagnostics.get('final',0))}</b> → "
                f"<b>{diagnostics.get('final',0)} Production</b>"
            )
            details+=core._decision_details(diagnostics)
        return await bot.send_message(
            chat_id,
            "⚪ <b>СЕТАПОВ НЕТ</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Сейчас нет кандидата, прошедшего Production + Alpha + execution контроль."
            f"{details}",
            parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )

    # Even a fully-qualified setup is no longer presented as an immediate trade.
    # Arm it first and show the exact trigger state.
    displayed=[]
    for result in results:
        if core.was_sent_recently(
            result.symbol,result.side,core.SIGNAL_COOLDOWN_HOURS,result.timeframe
        ):
            _decorate_entry_state(result,"COOLDOWN",None,None,0)
            displayed.append(result)
            continue
        try:
            result,assessment,streak=await _arm_and_check(
                result,"manual_scan" if not automatic else "auto_fallback"
            )
            displayed.append(result)
        except Exception:
            core.log.exception("V11.8.1 manual setup arming failed for %s",result.symbol)

    displayed=[s for s in displayed if getattr(s,"entry_now_state","")!="CANCELLED"]
    if not displayed:
        return await bot.send_message(
            chat_id,
            "⚪ <b>ГОТОВОГО ВХОДА НЕТ</b>\n"
            "Сетапы были найдены, но текущая micro-проверка их отменила.",
            parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )

    top=displayed[0]
    ready=sum(
        1 for s in displayed
        if str(getattr(s,"entry_now_state","")) in ("READY_PENDING","ENTER_NOW")
    )
    await bot.send_message(
        chat_id,
        f"{'⚡ SHORT' if short else '🏆 MAIN'} <b>· V11.10 ENTRY ENGINE</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Сильных сетапов: <b>{len(displayed)}</b> · почти готовы: <b>{ready}</b>\n"
        f"№1 <b>{top.symbol}</b> · PRO <b>{float(top.professional_rank):.1f}</b>\n\n"
        "🟡 <b>ARMED ≠ вход.</b> Бот продолжит live-наблюдение каждые 30 секунд.\n"
        "🚨 Сделка становится Production только после двух последовательных "
        "micro-подтверждений и повторного полного revalidation.",
        parse_mode=ParseMode.HTML
    )

    for i,s in enumerate(displayed):
        snapshot_id=save_snapshot(s,None)
        await bot.send_message(
            chat_id,card(s,i==0),parse_mode=ParseMode.HTML,
            reply_markup=signal_actions(snapshot_id=snapshot_id)
        )


core._send_results=send_results_v112


async def scan_cmd_v1142(update,context):
    """Manual broad scan shows/arms setups; it never starts Production early."""
    msg=update.effective_message
    if core._scan_lock.locked():
        return await msg.reply_text(
            "⏳ Сканирование уже выполняется. Дождись результата.",
            reply_markup=main_menu()
        )
    await msg.reply_text(
        "🔎 Ищу сильные Futures-сетапы. Найденные кандидаты сначала перейдут в ARMED; "
        "вход будет отдельным 🚨 ENTRY NOW."
    )
    try:
        async with core._scan_lock:
            results=await core.scan()
        await core._send_results(
            context.bot,update.effective_chat.id,results,
            diagnostics=core.scan_status().get("main")
        )
    except Exception:
        core.log.exception("V11.8.1 manual market scan failed")
        await msg.reply_text(
            "⚠️ Скан временно не выполнен: один из обязательных источников недоступен.",
            reply_markup=main_menu()
        )


async def short_scan_cmd_v1142(update,context):
    """Manual 15M scan also arms; no premature production save."""
    msg=update.effective_message
    if core._scan_lock.locked():
        return await msg.reply_text(
            "⏳ Другой скан уже выполняется. Дождись результата.",
            reply_markup=main_menu()
        )
    await msg.reply_text(
        "⏱ Ищу краткосрочные Futures-сетапы 5m → 15m → 1H. "
        "Сделкой они станут только после 🚨 ENTRY NOW."
    )
    try:
        async with core._scan_lock:
            results=await core.scan_short()
        await core._send_results(
            context.bot,update.effective_chat.id,results,short=True,
            diagnostics=core.scan_status().get("short")
        )
    except Exception:
        core.log.exception("V11.8.1 manual short scan failed")
        await msg.reply_text(
            "⚠️ Краткосрочный скан временно не выполнен.",
            reply_markup=main_menu()
        )


# app.bot.callback and app.bot.main resolve these module globals at runtime.
core.scan_cmd=scan_cmd_v1142
core.short_scan_cmd=short_scan_cmd_v1142


def _activate_delivered_entry_now(signal_id):
    """Retry-safe invariant: delivered ENTRY NOW rows become ACTIVE immediately."""
    try:
        with db_session(timeout=10) as c:
            row=c.execute(
                "SELECT feature_json,status FROM signals WHERE id=?",
                (int(signal_id),)
            ).fetchone()
        if not row:
            return False
        try:
            features=json.loads(row[0] or "{}")
        except Exception:
            features={}
        source=str((features.get("delivery_meta") or {}).get("source") or "")
        if source!="entry_now_v1142":
            return False
        activated_at=datetime.now(timezone.utc).isoformat()
        db.activate_signal(int(signal_id),activated_at)
        arm_id=(features.get("entry_now_v1142") or {}).get("arm_id")
        if arm_id is not None:
            mark_entry_triggered(int(arm_id),int(signal_id))
        return True
    except Exception:
        core.log.exception(
            "V11.8.1 failed to activate delivered ENTRY NOW signal=%s",signal_id
        )
        return False


async def _validate_futures_delivery(signal_id):
    """Revalidate time-sensitive ENTRY NOW immediately before Telegram send."""
    ctx=futures_delivery_context(signal_id)
    if not ctx:
        return False,"missing Futures signal context",None
    if str(ctx.get("release_version") or "")!=FUTURES_RELEASE_VERSION:
        return False,"Futures release changed before delivery",None
    age=futures_delivery_age(signal_id)
    if age is None:
        return False,"Futures delivery age unavailable",None
    if age>FUTURES_DELIVERY_MAX_AGE_MIN*60:
        return False,f"ENTRY NOW delivery stale ({age:.0f}s)",None

    features=dict(ctx.get("features") or {})
    if str((features.get("delivery_meta") or {}).get("source") or "")!="entry_now_v1142":
        return False,"not an ENTRY NOW delivery",None
    arm_id=(features.get("entry_now_v1142") or {}).get("arm_id")
    if arm_id is None:
        return False,"ENTRY NOW arm reference missing",None
    row=entry_row(int(arm_id))
    if not row:
        return False,"ENTRY NOW arm row missing",int(arm_id)
    if str(row.get("release_key") or "")!=FUTURES_RELEASE_VERSION:
        return False,"ENTRY NOW arm belongs to another release",int(arm_id)

    # Safety can change while Telegram is unavailable (for example a circuit
    # breaker may trip after another trade closes).
    safety=futures_safety_status()
    if safety.mode in ("CANARY","CIRCUIT_PAUSE"):
        return False,f"Futures safety changed to {safety.mode}",int(arm_id)

    # A second independent live trade may have appeared while Telegram was down.
    # This pending signal itself is excluded from the count.
    if ctx.get("delivered_at") is None and futures_other_live_count(signal_id)>0:
        return False,"another live/pending Futures trade now owns the risk slot",int(arm_id)

    try:
        assessment=await asyncio.wait_for(assess_entry_row(row),timeout=22)
    except Exception as exc:
        return False,f"live ENTRY NOW retry check unavailable: {type(exc).__name__}",int(arm_id)
    if assessment.state!="READY" or float(assessment.score)<80:
        return False,f"live ENTRY NOW retry check failed: {assessment.reason}",int(arm_id)

    # If delivery has been delayed beyond one normal monitor cycle, re-run the
    # full Production stack, not only the micro trigger.
    if age>=45:
        fresh=await _refresh_candidate_v1142(row)
        if fresh is None:
            return False,"fresh full Production retry revalidation failed",int(arm_id)
        second=await assess_entry_signal(fresh)
        if second.state!="READY" or float(second.score)<80:
            return False,f"fresh retry micro confirmation failed: {second.reason}",int(arm_id)
    return True,"fresh ENTRY NOW still valid",int(arm_id)


async def _deliver_forced_futures(bot,signal_id,chat_id,payload):
    """One synchronous explicit /signal delivery for a chat with AUTO disabled."""
    did=futures_delivery_id(signal_id,chat_id)
    if did is None:
        return 0
    ok,reason,arm_id=await _validate_futures_delivery(signal_id)
    if not ok:
        expire_futures_delivery(did,signal_id,reason)
        if arm_id is not None:
            cancel_entry_arm(arm_id,f"manual delivery suppressed: {reason}")
        return 0
    if not claim_futures_delivery(did):
        return 0
    try:
        await bot.send_message(
            int(chat_id),payload,parse_mode=ParseMode.HTML,
            reply_markup=signal_actions(signal_id=int(signal_id))
        )
    except Exception as exc:
        reason=(
            f"manual Telegram send outcome unknown: {type(exc).__name__}; "
            "retry suppressed, trade tracked as DELIVERY_UNCERTAIN"
        )
        mark_futures_delivery_uncertain(did,signal_id,int(chat_id),reason)
        if arm_id is not None:
            mark_entry_delivery_uncertain(arm_id,signal_id,reason)
        core.log.exception(
            "V11.8.1 manual ENTRY NOW delivery uncertain signal=%s chat=%s",
            signal_id,chat_id
        )
        return 0
    core.mark_delivery_sent(did)
    _activate_delivered_entry_now(signal_id)
    return 1


async def deliver_pending_v1123(bot):
    """Durable AUTO delivery, with freshness revalidation and per-recipient expiry."""
    async with core._delivery_lock:
        init_futures_delivery()
        stuck=expire_stuck_futures_sending(300)
        reconciled=reconcile_failed_futures_arms()
        if stuck or reconciled:
            core.log.warning(
                "V11.8.1 Futures delivery cleanup stuck=%s arms_reconciled=%s",
                stuck,reconciled
            )
        delivered=0
        validation={}
        for (
            delivery_id,signal_id,chat_id,payload,attempts,symbol,subscriber_enabled
        ) in pending_futures_deliveries(100):
            if not int(subscriber_enabled or 0):
                expire_futures_delivery(
                    delivery_id,signal_id,
                    "recipient AUTO alerts disabled before delivery"
                )
                continue
            if signal_id not in validation:
                validation[signal_id]=await _validate_futures_delivery(signal_id)
            ok,reason,arm_id=validation[signal_id]
            if not ok:
                expire_all_futures_deliveries(signal_id,reason)
                if arm_id is not None:
                    cancel_entry_arm(arm_id,f"delivery suppressed: {reason}")
                core.log.warning(
                    "V11.8.1 stale/invalid ENTRY NOW suppressed signal=%s %s: %s",
                    signal_id,symbol,reason
                )
                continue
            if not claim_futures_delivery(delivery_id):
                continue
            try:
                await bot.send_message(
                    chat_id,payload,parse_mode=ParseMode.HTML,
                    reply_markup=signal_actions(signal_id=signal_id)
                )
            except Exception as exc:
                reason=(
                    f"Telegram send outcome unknown: {type(exc).__name__}; "
                    "retry suppressed, trade tracked as DELIVERY_UNCERTAIN"
                )
                mark_futures_delivery_uncertain(
                    delivery_id,signal_id,int(chat_id),reason
                )
                if arm_id is not None:
                    mark_entry_delivery_uncertain(arm_id,signal_id,reason)
                core.log.exception(
                    "V11.8.1 Futures delivery uncertain; retry suppressed and risk reserved: "
                    "signal=%s chat=%s attempt=%s",
                    signal_id,chat_id,attempts+1
                )
            else:
                core.mark_delivery_sent(delivery_id)
                _activate_delivered_entry_now(signal_id)
                delivered+=1
        # Per-recipient expiry inside this loop may have converted a logical
        # signal to FAILED; release its PENDING_DELIVERY arm immediately.
        reconcile_failed_futures_arms()
        return delivered


core._deliver_pending=deliver_pending_v1123


async def run_automatic_scan_v1123(context,scanner_fn,label):
    """Broad scan discovers setups; Telegram fires only on persistent ENTRY NOW."""
    try:
        chats=core.subscribers()
        if not chats:
            return

        waited=0
        while core._scan_lock.locked() and waited<90:
            await asyncio.sleep(5)
            waited+=5
        if core._scan_lock.locked():
            core.log.warning("V11.8.1 automatic %s skipped after waiting %ss",label,waited)
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
            core.log.info("automatic %s scan completed: no new qualified setups",label)
            return

        armed=0
        cancelled=0
        triggered=0
        for result in fresh:
            result,assessment,streak=await _arm_and_check(
                result,f"auto_{label}"
            )
            if assessment.state=="CANCEL":
                cancelled+=1
                continue
            armed+=1
            if assessment.state=="READY" and streak>=2:
                outcome=await _trigger_arm(context,result.entry_now_arm_id,assessment)
                if outcome and outcome[0]=="PRODUCTION":
                    triggered+=1

        core.log.info(
            "V11.8.1 automatic %s setups=%s armed=%s cancelled=%s entry_now=%s waited=%ss",
            label,len(fresh),armed,cancelled,triggered,waited
        )
    except Exception:
        core.log.exception("V11.8.1 automatic %s scan failed",label)


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


def _spot_cluster_key(signal):
    try:
        return str(
            ((getattr(signal,"feature_snapshot",{}) or {}).get("portfolio") or {}).get("cluster_key")
            or signal.symbol
        ).upper()
    except Exception:
        return str(getattr(signal,"symbol","") or "").upper()


def _spot_orderbook_symbols(limit=10):
    """Prioritise active/pending positions, then highest-score WATCH candidates."""
    ordered=[]; seen=set()
    try:
        for row in spot_reserved_signals(10):
            s=str(row.get("symbol") or "").upper()
            if s and s not in seen:
                ordered.append(s); seen.add(s)
    except Exception:
        pass
    try:
        rows=sorted(active_spot_watches(20),key=lambda r:float(r.get("score") or 0),reverse=True)
        for row in rows:
            s=str(row.get("symbol") or "").upper()
            if s and s not in seen:
                ordered.append(s); seen.add(s)
            if len(ordered)>=int(limit):
                break
    except Exception:
        pass
    return tuple(ordered[:int(limit)])


def _decorate_spot_entry(signal,state,streak=0):
    signal.spot_entry_state=str(state)
    signal.spot_confirm_streak=int(streak or 0)
    signal.feature_snapshot.setdefault("spot_entry_v118",{}).update({
        "state":str(state),"confirm_streak":int(streak or 0),
        "required_streak":2,
    })
    return signal


def _arm_spot_candidate(signal):
    status=str(getattr(signal,"status","") or "").upper()
    if status not in {"BUY","WATCH"}:
        return 0
    if spot_was_sent_recently(signal.symbol,72):
        _decorate_spot_entry(signal,"COOLDOWN",0)
        return 0
    if upsert_spot_watch(signal) is None:
        return 0
    if status=="BUY":
        ask=float((getattr(signal,"micro",{}) or {}).get("ask") or 0)
        existing=get_spot_watch(signal.symbol) or {}
        prior=int(existing.get("confirm_streak") or 0)
        # Broad discovery is allowed to create READY 1/2 only. The second
        # confirmation must come from WATCHTOWER with a sequence-synchronised
        # local diff-depth book.
        streak=prior if prior>=1 else record_spot_ready(
            signal.symbol,float(signal.score),ask,60
        )
        streak=min(1,int(streak or 0))
        _decorate_spot_entry(signal,"READY_PENDING",streak)
        return streak
    reset_spot_ready(signal.symbol,"broad scan remains WATCH")
    _decorate_spot_entry(signal,"WATCH",0)
    return 0


def _store_spot_watches(results):
    stored=0
    for signal in results or ():
        status=str(getattr(signal,"status","") or "").upper()
        if status in {"BUY","WATCH"} and not spot_was_sent_recently(signal.symbol,72):
            before=get_spot_watch(signal.symbol)
            _arm_spot_candidate(signal)
            after=get_spot_watch(signal.symbol)
            if after is not None and (before is None or after.get("updated_at")!=before.get("updated_at")):
                stored+=1
    return stored


async def spot_watch_job(context):
    """Promote WATCH only after two serialized full confirmations."""
    try:
        chats=list(core.subscribers())
        if not chats:
            return
        promoted=0
        async with _spot_candidate_lock:
            rows=active_spot_watches(10)
            if not rows:
                return
            active_clusters=set(spot_active_clusters())
            active_positions=spot_reserved_signals(10)
            portfolio_symbols=[str(r.get("symbol") or "").upper() for r in active_positions]
            active_count=spot_reserved_count()
            if active_count>=2:
                core.log.info("V11.8 Spot WATCHTOWER portfolio cap active=%s",active_count)
                return

            for row in rows:
                symbol=str(row.get("symbol") or "").upper()
                if spot_was_sent_recently(symbol,72):
                    close_spot_watch(symbol,"COOLDOWN","recent delivered/pending BUY already exists")
                    continue
                local_book=spot_local_book(symbol,3.0,50)
                local_health=spot_book_stability(symbol,3.0)
                if local_book is None or not local_health.get("healthy"):
                    record_spot_watch_check(
                        symbol,None,
                        f"local depth not ready: {local_health.get('reason','unsynchronised')}"
                    )
                    continue
                ask=float(local_book["asks"][0][0])
                if ask<=float(row.get("invalidation") or 0):
                    close_spot_watch(symbol,"CANCELLED","price invalidated before BUY")
                    continue
                if not (float(row.get("entry_low") or 0)<=ask<=float(row.get("entry_high") or 0)):
                    reset_spot_ready(symbol,"waiting for original BUY zone",ask)
                    continue
                cluster=str(row.get("portfolio_cluster") or symbol).upper()
                if cluster in active_clusters:
                    record_spot_watch_check(
                        symbol,ask,f"portfolio cluster {cluster} already has OPEN Spot BUY"
                    )
                    continue
                corr_risk=await spot_active_correlation_risk(symbol,portfolio_symbols)
                if corr_risk.get("degraded"):
                    record_spot_watch_check(
                        symbol,ask,"active-position correlation check unavailable"
                    )
                    continue
                if corr_risk.get("blocked"):
                    record_spot_watch_check(
                        symbol,ask,
                        f"corr {float(corr_risk.get('corr',0)):.2f} with "
                        f"{corr_risk.get('with_symbol')} active/pending Spot position"
                    )
                    continue

                signal,error=await spot_recheck_watch(row)
                if signal is None:
                    reset_spot_ready(symbol,error or "fresh revalidation rejected",ask)
                    continue
                if signal.status!="BUY":
                    reset_spot_ready(symbol,"fresh full revalidation still WATCH",ask)
                    continue

                # Two confirmations count only if they refer to materially the
                # same entry/stop geometry. upsert resets the streak if the
                # freshly rebuilt setup moved.
                upsert_spot_watch(signal)
                streak=record_spot_ready(symbol,float(signal.score),ask,60)
                if streak<2:
                    _decorate_spot_entry(signal,"READY_PENDING",streak)
                    record_spot_watch_check(
                        symbol,ask,f"fresh BUY confirmation {streak}/2; wait for persistence"
                    )
                    continue

                _decorate_spot_entry(signal,"BUY_NOW",streak)
                signal_id=save_spot_signal(signal,delivered=False)
                record_v1180_decision("SPOT",signal_id,signal)
                payload=spot_card(signal,True)
                for chat_id in chats:
                    enqueue_spot_delivery(signal_id,chat_id,payload)
                close_spot_watch(
                    symbol,"PENDING_DELIVERY",
                    "WATCH -> BUY 2/2 passed; Telegram live revalidation pending",signal_id
                )
                active_clusters.add(_spot_cluster_key(signal))
                portfolio_symbols.append(symbol)
                promoted+=1
                active_count+=1
                if active_count>=2:
                    break

        if promoted:
            delivered=await _deliver_spot_pending(context.bot)
            core.log.info(
                "V11.8 Spot WATCHTOWER queued=%s delivered=%s",promoted,delivered
            )
    except Exception:
        core.log.exception("V11.8 Spot WATCHTOWER failed")


async def _send_spot_results(bot,chat_id,results,automatic=False):
    """Manual renderer. A BUY NOW is persisted only as pending until Telegram succeeds."""
    if not results:
        d=spot_scan_status()
        return await bot.send_message(
            chat_id,
            "⚪ <b>SPOT · NO EDGE</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Сейчас нет достаточно сильной покупки на горизонте 3–10 дней.\n"
            f"Проверено: <b>{int(d.get('liquid',0))}</b> ликвидных → "
            f"<b>{int(d.get('deep_checked',0))}</b> deep-кандидатов.\n"
            f"Market regime: <b>{escape(str(d.get('regime','?')))}</b>.",
            parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )

    queued=[]
    async with _spot_candidate_lock:
        _store_spot_watches(results)

        # This path must never bypass the durable/live-revalidated outbox.
        if automatic:
            return 0

        active_clusters=set(spot_active_clusters())
        portfolio_symbols=[
            str(r.get("symbol") or "").upper() for r in spot_reserved_signals(10)
        ]
        slots=max(0,2-spot_reserved_count())
        for s in results:
            state=str(getattr(s,"spot_entry_state","WATCH")).upper()
            if s.status!="BUY" or state!="BUY_NOW":
                continue
            if spot_was_sent_recently(s.symbol,72):
                _decorate_spot_entry(s,"COOLDOWN",0)
                continue
            cluster=_spot_cluster_key(s)
            if slots<=0 or cluster in active_clusters:
                _decorate_spot_entry(s,"COOLDOWN",0)
                s.risks.append("portfolio cap/correlation blocks another Spot position")
                continue
            corr_risk=await spot_active_correlation_risk(s.symbol,portfolio_symbols)
            if corr_risk.get("degraded"):
                _decorate_spot_entry(s,"COOLDOWN",0)
                s.risks.append("active-position correlation check unavailable")
                continue
            if corr_risk.get("blocked"):
                _decorate_spot_entry(s,"COOLDOWN",0)
                s.risks.append(
                    f"30D corr {float(corr_risk.get('corr',0)):.2f} with "
                    f"{corr_risk.get('with_symbol')} active/pending position"
                )
                continue

            signal_id=save_spot_signal(s,delivered=False)
            record_v1180_decision("SPOT",signal_id,s)
            payload=spot_card(s,not queued)
            enqueue_spot_delivery(signal_id,int(chat_id),payload)
            close_spot_watch(
                s.symbol,"PENDING_DELIVERY",
                "manual BUY NOW queued; live delivery revalidation pending",signal_id
            )
            queued.append((s,signal_id))
            active_clusters.add(cluster)
            portfolio_symbols.append(str(s.symbol).upper())
            slots-=1

    buys=sum(1 for s in results if s.status=="BUY")
    watches=sum(1 for s in results if s.status=="WATCH")
    ready=sum(
        1 for s in results
        if str(getattr(s,"spot_entry_state","")).upper()=="READY_PENDING"
    )
    buy_now=len(queued)

    await bot.send_message(
        chat_id,
        "🟢 <b>SPOT SCAN · 3–10 DAYS</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"BUY NOW <b>{buy_now}</b> · READY <b>{ready}</b> · WATCH <b>{watches}</b>.\n"
        "READY/WATCH = не покупать. BUY NOW отправляется отдельным сообщением "
        "только после последней live-проверки непосредственно перед Telegram.",
        parse_mode=ParseMode.HTML,
    )

    # Show analysis-only cards. Queued BUY NOW is sent only by the live outbox.
    queued_ids={id(s) for s,_ in queued}
    rank=0
    for s in results:
        if id(s) in queued_ids:
            continue
        await bot.send_message(
            chat_id,spot_card(s,rank==0),
            parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
        rank+=1

    if queued:
        delivered=await _deliver_spot_pending(bot,forced_chat_ids={int(chat_id)})
        if delivered<=0:
            await bot.send_message(
                chat_id,
                "⚪ <b>SPOT BUY NOW НЕ ДОСТАВЛЕН</b>\n"
                "Последняя live-проверка/доставка не завершилась успешно. "
                "<b>Не входить</b>, пока отдельное 🚨 SPOT BUY NOW не пришло.",
                parse_mode=ParseMode.HTML,reply_markup=main_menu()
            )
        return delivered
    return 0


async def spot_cmd(update,context):
    msg=update.effective_message
    await msg.reply_text(
        "🟢 Анализирую Binance Spot: 1D + 4H + 1H, relative strength, price-path, "
        "volume accumulation, Spot L2/aggTrades, news и futures crowding-risk. Это тяжелее обычного скана."
    )
    try:
        results=await spot_scan(force=False)
        await _send_spot_results(context.bot,update.effective_chat.id,results,automatic=False)
    except Exception as exc:
        core.log.exception("V11.8 Spot manual scan failed")
        await msg.reply_text(
            "⚠️ Spot scan не завершён: один из обязательных Spot-источников недоступен. "
            "Слабый/неполный результат бот не подменяет сигналом.",reply_markup=main_menu()
        )


def _refresh_spot_payload(payload,ask,micro,news,crowd,ob_health=None):
    text=str(payload)
    text=re.sub(
        r"📚 Spot spread <b>.*?</b> · \$5k impact <b>.*?</b>",
        f"📚 Spot spread <b>{float(micro.get('spread_bps',999)):.1f}bps</b> · "
        f"$5k impact <b>{float(micro.get('impact_5k_bps',999)):.1f}bps</b>",
        text,
    )
    text=re.sub(
        r"💧 L2 imbalance <b>.*?</b> · taker BUY <b>.*?</b>",
        f"💧 L2 imbalance <b>{float(micro.get('book_imbalance_20bps',0)):+.2f}</b> · "
        f"taker BUY <b>{float(micro.get('buy_share',.5))*100:.0f}%</b>",
        text,
    )
    text=re.sub(
        r"🕐 Closed flow 5m/15m <b>.*?</b>",
        f"🕐 Closed flow 5m/15m <b>{float(micro.get('closed_buy_share_5m',.5))*100:.0f}%/"
        f"{float(micro.get('closed_buy_share_15m',.5))*100:.0f}%</b>",
        text,
    )
    if crowd.get("available"):
        crowd_line=(
            f"🧯 Futures crowding: <b>{'EXTREME' if crowd.get('extreme') else 'OK'}</b> · "
            f"funding {float(crowd.get('funding',0))*100:+.3f}% · "
            f"OI {float(crowd.get('oi_change_pct',0)):+.1f}%"
        )
    elif crowd.get("degraded"):
        crowd_line="🧯 Futures crowding: <b>DEGRADED — BUY BLOCKED</b>"
    else:
        crowd_line="🧯 Futures crowding: <b>N/A (no USD-M counterpart)</b>"
    text=re.sub(r"🧯 Futures crowding:.*",crowd_line,text)

    if news.get("degraded"):
        news_line="📰 News: <b>DEGRADED — BUY BLOCKED</b>"
    elif news.get("block") or news.get("recent_negative"):
        news_line="📰 News: <b>NEGATIVE RISK — BUY BLOCKED</b>"
    elif news.get("global_breaking"):
        news_line="📰 News: <b>HIGH-IMPACT EVENT — BUY WAITS</b>"
    elif news.get("catalyst"):
        news_line="📰 News: <b>MULTI-SOURCE POSITIVE CATALYST</b>"
    else:
        news_line="📰 News: <b>neutral / no independent catalyst</b>"
    text=re.sub(r"📰 News:.*",news_line,text)
    ob_health=dict(ob_health or {})
    return (
        "✅ <b>LIVE REVALIDATION NOW</b> · "
        f"ask <b>{float(ask):.8g}</b> · local book <b>{float(ob_health.get('stability_score',0) or 0):.0f}/100</b> · "
        "execution/flow/news/crowding rechecked\n"
        + text
    )


async def _deliver_spot_pending(bot,forced_chat_ids=None):
    """Deliver queued Spot BUY only if every live execution/risk gate still passes."""
    forced={int(x) for x in (forced_chat_ids or ())}
    async with _spot_delivery_lock:
        stuck=expire_stuck_spot_sending(300)
        expired=expire_spot_deliveries(SPOT_DELIVERY_MAX_AGE_MIN)
        reconciled=reconcile_spot_watch_delivery()
        if stuck or expired or reconciled:
            core.log.warning(
                "V11.8 Spot delivery cleanup stuck=%s expired=%s watch_reconciled=%s",
                stuck,expired,reconciled
            )

        subscribers=set(int(x) for x in core.subscribers())
        delivered=0
        live_cache={}
        news_snapshot=None
        for delivery_id,signal_id,chat_id,payload,attempts in pending_spot_deliveries(100):
            chat_id=int(chat_id)
            if chat_id not in subscribers and chat_id not in forced:
                expire_spot_delivery(
                    delivery_id,signal_id,
                    "recipient AUTO alerts disabled before delivery"
                )
                reconcile_spot_watch_delivery()
                continue

            ctx=spot_delivery_context(signal_id)
            if not ctx:
                expire_spot_delivery(delivery_id,signal_id,"missing Spot signal context")
                continue
            symbol=str(ctx.get("symbol") or "")
            base=str(ctx.get("base_asset") or symbol.removesuffix("USDT"))
            if str(ctx.get("release_version") or "")!=SPOT_RELEASE_VERSION:
                reason="Spot release changed before Telegram delivery"
                expire_spot_delivery(delivery_id,signal_id,reason)
                close_spot_watch(symbol,"CANCELLED",reason,signal_id)
                continue

            # Terminal news/event vetoes have priority over transient execution/crowding
            # failures.  If fresh information already invalidates the BUY, expire the
            # pending delivery immediately instead of leaving it retryable.
            if news_snapshot is None:
                news_snapshot=await core.get_news_sentiment()
            news=spot_assess_news(news_snapshot,base)
            if news.get("degraded"):
                mark_spot_delivery_failed(
                    delivery_id,"fresh Spot news layer temporarily degraded"
                )
                continue
            if news.get("block") or news.get("recent_negative") or news.get("global_breaking"):
                reason="fresh Spot news/event risk invalidated BUY"
                expire_spot_delivery(delivery_id,signal_id,reason)
                close_spot_watch(symbol,"CANCELLED",reason,signal_id)
                continue

            # Re-check the hard portfolio cap at the actual send moment.
            # Another delivered/pending BUY may have appeared since this row
            # was queued; this signal itself is excluded from the reservation count.
            others=spot_reserved_signals(10,exclude_id=signal_id)
            if len(others)>=2:
                mark_spot_delivery_failed(
                    delivery_id,"Spot portfolio cap reached before actual delivery"
                )
                continue

            claimed=False
            try:
                if symbol not in live_cache:
                    book=spot_local_book(symbol,3.0,100)
                    ob_health=spot_book_stability(symbol,3.0)
                    if book is None or not ob_health.get("healthy"):
                        mark_spot_delivery_failed(
                            delivery_id,
                            f"local Spot order book unavailable: {ob_health.get('reason','unsynchronised')}"
                        )
                        continue
                    trades,minute_frame,crowd=await asyncio.gather(
                        spot_agg_trades(symbol,1000),spot_klines(symbol,"1m",90),
                        spot_fresh_derivatives_risk(symbol),
                    )
                    live_cache[symbol]={
                        "book":book,
                        "book_health":ob_health,
                        "micro":spot_analyze_book(book,trades,minute_frame=minute_frame),
                        "crowd":crowd,
                    }
                live=live_cache[symbol]
                book=live["book"]; ob_health=live["book_health"]
                micro=live["micro"]; crowd=live["crowd"]
                ask=float(book["asks"][0][0])

                if not (
                    ask>float(ctx.get("invalidation") or 0)
                    and float(ctx.get("entry_low") or 0)<=ask<=float(ctx.get("entry_high") or 0)
                ):
                    reason=f"fresh ask {ask:.12g} left BUY zone"
                    expire_spot_delivery(delivery_id,signal_id,reason)
                    close_spot_watch(symbol,"CANCELLED",reason,signal_id)
                    continue

                execution_ok=(
                    bool(micro.get("healthy"))
                    and bool(micro.get("flow_reliable"))
                    and bool(micro.get("closed_flow_ok"))
                    and float(micro.get("buy_share",.5))>=.52
                    and float(micro.get("closed_buy_share_5m",.5))>=.50
                    and float(micro.get("closed_buy_share_15m",.5))>=.50
                    and float(micro.get("spread_bps",999))<=6.0
                    and float(micro.get("impact_5k_bps",999))<=15.0
                    and float(micro.get("book_imbalance_20bps",0))>=-.30
                    and float(ob_health.get("stability_score",0) or 0)>=65
                    and float(ob_health.get("bid_replenishment_ratio",0) or 0)>=.40
                )
                if not execution_ok:
                    mark_spot_delivery_failed(
                        delivery_id,"fresh Spot L2/taker-flow no longer confirms BUY"
                    )
                    continue

                if crowd.get("degraded"):
                    mark_spot_delivery_failed(
                        delivery_id,"fresh Futures crowding layer temporarily degraded"
                    )
                    continue
                if crowd.get("extreme"):
                    reason="fresh Futures crowding became EXTREME"
                    expire_spot_delivery(delivery_id,signal_id,reason)
                    close_spot_watch(symbol,"CANCELLED",reason,signal_id)
                    continue


                active_symbols=[
                    str(r.get("symbol") or "").upper()
                    for r in spot_reserved_signals(10,exclude_id=signal_id)
                    if str(r.get("symbol") or "").upper()!=symbol
                ]
                corr_risk=await spot_active_correlation_risk(symbol,active_symbols)
                if corr_risk.get("degraded"):
                    mark_spot_delivery_failed(
                        delivery_id,"active-position correlation check temporarily unavailable"
                    )
                    continue
                if corr_risk.get("blocked"):
                    mark_spot_delivery_failed(
                        delivery_id,
                        f"30D corr {float(corr_risk.get('corr',0)):.2f} with "
                        f"{corr_risk.get('with_symbol')} active Spot position"
                    )
                    continue

                current_payload=_refresh_spot_payload(payload,ask,micro,news,crowd,ob_health)
                if not claim_spot_delivery(delivery_id):
                    continue
                claimed=True
                await bot.send_message(
                    chat_id,current_payload,parse_mode=ParseMode.HTML,reply_markup=main_menu()
                )
            except Exception as exc:
                if claimed:
                    # Telegram was invoked: timeout can mean "accepted but reply
                    # lost". Never resend and never forget a potentially received
                    # trading instruction. Track it as DELIVERY_UNCERTAIN.
                    reason=(
                        f"Telegram send outcome unknown: {type(exc).__name__}; "
                        "retry suppressed, trade tracked as DELIVERY_UNCERTAIN"
                    )
                    mark_spot_delivery_uncertain(
                        delivery_id,signal_id,ask,reason
                    )
                    close_spot_watch(
                        symbol,"DELIVERY_UNCERTAIN",reason,signal_id
                    )
                    core.log.exception(
                        "V11.8 Spot delivery uncertain; retry suppressed and risk reserved "
                        "signal=%s chat=%s attempt=%s",
                        signal_id,chat_id,attempts+1
                    )
                else:
                    # Failure happened before Telegram. A short retry remains
                    # safe while the Spot delivery TTL is still valid.
                    mark_spot_delivery_failed(delivery_id,exc)
                    core.log.exception(
                        "V11.8 Spot pre-send revalidation unavailable "
                        "signal=%s chat=%s attempt=%s",
                        signal_id,chat_id,attempts+1
                    )
            else:
                mark_spot_delivery_sent(delivery_id,signal_id,ask)
                close_spot_watch(
                    symbol,"PROMOTED","SPOT BUY NOW delivered after live revalidation",signal_id
                )
                delivered+=1
        return delivered


async def spot_delivery_retry_job(context):
    try:
        delivered=await _deliver_spot_pending(context.bot)
        if delivered:
            core.log.info("V11.8 Spot outbox retry delivered=%s",delivered)
    except Exception:
        core.log.exception("V11.8 Spot outbox retry failed")


async def spot_auto_job(context):
    """Broad Spot scan arms candidates; only serialized persistent 2/2 BUY NOW is queued."""
    try:
        chats=list(core.subscribers())
        if not chats:
            return

        results=await spot_scan(force=False)
        queued=0
        async with _spot_candidate_lock:
            _store_spot_watches(results)
            active_clusters=set(spot_active_clusters())
            portfolio_symbols=[
                str(r.get("symbol") or "").upper() for r in spot_reserved_signals(10)
            ]
            slots=max(0,2-spot_reserved_count())
            if slots<=0:
                core.log.info("V11.8 Spot auto: portfolio cap reached")
                return

            for s in results:
                if s.status!="BUY" or spot_was_sent_recently(s.symbol,72):
                    continue
                row=get_spot_watch(s.symbol) or {}
                streak=int(row.get("confirm_streak") or getattr(s,"spot_confirm_streak",0) or 0)
                if streak<2:
                    core.log.info(
                        "V11.8 Spot %s READY %s/2; no entry alert yet",s.symbol,streak
                    )
                    continue
                cluster=_spot_cluster_key(s)
                if cluster in active_clusters:
                    core.log.info(
                        "V11.8 Spot auto withheld correlated cluster=%s symbol=%s",
                        cluster,s.symbol
                    )
                    continue
                corr_risk=await spot_active_correlation_risk(s.symbol,portfolio_symbols)
                if corr_risk.get("degraded"):
                    core.log.info(
                        "V11.8 Spot auto withheld %s: active correlation unavailable",
                        s.symbol
                    )
                    continue
                if corr_risk.get("blocked"):
                    core.log.info(
                        "V11.8 Spot auto withheld %s: corr %.2f with %s",
                        s.symbol,float(corr_risk.get("corr",0)),corr_risk.get("with_symbol")
                    )
                    continue

                _decorate_spot_entry(s,"BUY_NOW",streak)
                signal_id=save_spot_signal(s,delivered=False)
                record_v1180_decision("SPOT",signal_id,s)
                payload=spot_card(s,queued==0)
                for chat_id in chats:
                    enqueue_spot_delivery(signal_id,chat_id,payload)
                close_spot_watch(
                    s.symbol,"PENDING_DELIVERY",
                    "broad Spot 2/2 passed; live Telegram revalidation pending",signal_id
                )
                active_clusters.add(cluster)
                portfolio_symbols.append(str(s.symbol).upper())
                queued+=1
                slots-=1
                if slots<=0:
                    break

        if not queued:
            core.log.info(
                "V11.8 Spot auto: no persistent 2/2 BUY NOW; WATCH/READY stays silent"
            )
            return

        delivered=await _deliver_spot_pending(context.bot)
        core.log.info(
            "V11.8 Spot auto queued=%s recipients=%s delivered=%s",
            queued,len(chats),delivered
        )
    except Exception:
        core.log.exception("V11.8 Spot automatic scan failed")


async def spot_tracker_job(context=None):
    try:
        updated=await update_spot_outcomes(30)
        if updated:
            core.log.info("V11.8 Spot forward journal updated=%s",updated)
    except Exception:
        core.log.exception("V11.8 Spot tracker failed")


async def market_intelligence_job(context):
    try:
        await observe_v1180_manager(context.bot,notify=True)
        await asyncio.to_thread(sync_v1180_failures,500)
    except Exception:
        core.log.exception("V11.8 Active Manager job failed")


async def edge_lab_job(context=None):
    try:
        updated=await asyncio.to_thread(sync_v1180_outcomes,500)
        if updated:
            core.log.info("V11.8 Champion/Challenger outcomes updated=%s",updated)
    except Exception:
        core.log.exception("V11.8 Edge Lab sync failed")


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
    if data=="v115:spot":
        await query.answer()
        return await spot_cmd(update,context)
    if data=="v117:spotactive":
        await query.answer()
        return await query.message.reply_text(spot_active_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v118:edgelab":
        await query.answer()
        return await query.message.reply_text(v1180_lab_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v118:manager":
        await query.answer()
        return await query.message.reply_text(v1180_manager_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v115:spotstats":
        await query.answer()
        return await query.message.reply_text(spot_stats_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v115:spothistory":
        await query.answer()
        return await query.message.reply_text(spot_history_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v115:spotsystem":
        await query.answer()
        return await query.message.reply_text(spot_system_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v116:spotwatch":
        await query.answer()
        return await query.message.reply_text(spot_watch_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v1142:entrynow":
        await query.answer()
        return await query.message.reply_text(
            entry_now_status_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
    if data=="v1142:active":
        await query.answer()
        return await query.message.reply_text(
            futures_active_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
    if data=="v1142:safety":
        await query.answer()
        return await query.message.reply_text(
            futures_safety_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )
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
        return await query.message.reply_text("◈ <b>YK CONTROL CENTER · V11.10.0</b>\n<i>Выбери режим или аналитику.</i>",parse_mode=ParseMode.HTML,reply_markup=main_menu())
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
        with db_session(timeout=10) as c:
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
        safety=futures_safety_status()
        arms=active_entry_rows(30)
        ready_arms=sum(
            1 for row in arms
            if str(row.get("last_state") or "")=="READY"
        )
        text=(
            "🛡 <b>KORKOVTS V11.8 ENTRY NOW</b>\n"
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
            f"{system_extra()}\n"
            f"🚨 Entry monitor ARMED <b>{len(arms)}</b> · READY 1/2 <b>{ready_arms}</b>\n"
            f"🛡 Futures safety <b>{escape(safety.mode)}</b> · "
            f"live entry <b>{'YES' if safety.allow_live else 'NO'}</b> · "
            f"loss streak <b>{safety.consecutive_losses}</b>\n\n"
            f"🏆 Main <b>{main.get('liquid',0)} → {main.get('prefiltered',0)} → {main.get('final',0)}</b>\n"
            f"⚡ Short <b>{short.get('liquid',0)} → {short.get('prefiltered',0)} → {short.get('final',0)}</b>\n\n"
            f"Pool: <b>{V11_CANDIDATE_POOL}</b> qualified candidates · "
            f"execution rejects Main/Short <b>{main.get('execution_rejected',0)}/{short.get('execution_rejected',0)}</b> · "
            f"meta rejects <b>{main.get('meta_rejected',0)}/{short.get('meta_rejected',0)}</b>\n"
            f"Meta 1H/15M <b>{meta_main.status}/{meta_short.status}</b> · samples <b>{meta_main.n}/{meta_short.n}</b>\n"
            f"duration Main/Short <b>{main.get('v1142_duration_sec','—')}s/{short.get('v1142_duration_sec','—')}s</b>\n"
            "🧬 1H weights: " + " · ".join(f"{k} <b>{v:.2f}</b>" for k,v in w_main.items())
            + "\n🧬 15M weights: " + " · ".join(f"{k} <b>{v:.2f}</b>" for k,v in w_short.items())
        )
    except Exception:
        core.log.exception("V11.8 system status failed")
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
        core.log.info("V11.8.1 Meta Precision models refreshed")
    except Exception:
        core.log.exception("V11.8.1 Meta Precision refresh failed")


async def db_maintenance_job(context=None):
    try:
        await asyncio.to_thread(db_checkpoint)
        await asyncio.to_thread(backup_if_due,7)
    except Exception:
        core.log.exception("V11.8.1 database maintenance failed")


async def lifecycle_job(context):
    try: await observe_lifecycle(context.bot,notify=True)
    except Exception: core.log.exception("V11.8.1 lifecycle job failed")


async def structure_job(context):
    try: await structure_watch(context.bot,notify=True)
    except Exception: core.log.exception("V11.8.1 structure job failed")


async def post_init_v112(application):
    global _live_task,_spot_book_task
    await asyncio.to_thread(harden_database)
    init_rank_audit(); init_lifecycle(); init_details(); init_entry_now(); init_futures_safety(); init_futures_delivery(); init_spot_db(); init_spot_watch(); init_v1180_lab(); init_v1180_manager(); init_v11100_blackbox()
    # If SENDING survived process startup, the prior Telegram result is
    # unknowable. Suppress rather than risk a duplicate entry instruction.
    expire_stuck_futures_sending(0)
    reconcile_failed_futures_arms()
    expire_stuck_spot_sending(0)
    reconcile_spot_watch_delivery()
    set_live_extra_symbol_provider(active_entry_symbols)
    set_spot_book_symbol_provider(_spot_orderbook_symbols)
    await _original_post_init(application)
    await db_maintenance_job()

    # Build the research models before polling begins, but on worker threads so
    # CPU-heavy walk-forward validation never stalls the asyncio loop.
    await meta_refresh_job()

    application.add_handler(CommandHandler("spot",spot_cmd))
    _live_task=asyncio.create_task(live_monitor(),name="v114-binance-live-market")
    _spot_book_task=asyncio.create_task(spot_book_monitor(),name="v118-spot-local-orderbook")
    application.job_queue.run_repeating(
        entry_now_monitor_job,interval=30,first=25,name="v1142-entry-now-monitor"
    )
    application.job_queue.run_repeating(
        spot_delivery_retry_job,interval=60,first=45,name="v115-spot-delivery-outbox"
    )
    application.job_queue.run_repeating(
        spot_auto_job,interval=SPOT_AUTO_INTERVAL_MIN*60,first=180,name="v115-spot-auto-scan"
    )
    application.job_queue.run_repeating(
        spot_watch_job,interval=SPOT_WATCH_INTERVAL_MIN*60,first=120,name="v116-spot-watchtower"
    )
    application.job_queue.run_repeating(
        spot_tracker_job,interval=3600,first=300,name="v116-spot-forward-tracker"
    )
    application.job_queue.run_repeating(lifecycle_job,interval=30,first=45,name="v114-live-lifecycle")
    application.job_queue.run_repeating(structure_job,interval=300,first=180,name="v114-structure-watch")
    application.job_queue.run_repeating(
        market_intelligence_job,interval=60,first=90,name="v118-active-manager"
    )
    application.job_queue.run_repeating(
        edge_lab_job,interval=1800,first=600,name="v118-edge-lab-sync"
    )
    application.job_queue.run_repeating(
        meta_refresh_job,interval=6*3600,first=6*3600,name="v114-meta-refresh"
    )
    application.job_queue.run_repeating(
        db_maintenance_job,interval=6*3600,first=6*3600,name="v114-db-maintenance"
    )


core.post_init=post_init_v112


async def post_shutdown_v112(application):
    global _live_task,_spot_book_task
    if _live_task:
        _live_task.cancel()
        try: await _live_task
        except asyncio.CancelledError: pass
        _live_task=None
    if _spot_book_task:
        await stop_spot_book_monitor()
        _spot_book_task.cancel()
        try: await _spot_book_task
        except asyncio.CancelledError: pass
        _spot_book_task=None
    await close_spot_http()
    await _original_post_shutdown(application)


core.post_shutdown=post_shutdown_v112


if __name__=="__main__":
    core.main()
