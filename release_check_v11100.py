"""Fail-fast compatibility gate for V11.10.0 Competitive Edge. No external network calls."""

import ast
import inspect
import os
from pathlib import Path

import app.bot as bot
import app.config as config
import app.db as db
import app.market as market
import app.scanner as scanner
import app.tracker as tracker
from app.strategy import Signal
from v1171_sqlite import db_session
from v113_meta import FEATURE_NAMES
from v113_tracking import effective_created_at, effective_last_checked_at
from v113_micro import evaluate as evaluate_micro
from v113_execution import evaluate_quote
from v113_biascheck import run as indicator_selftest
from v114_db import harden_database, status as db_runtime_status, install_connect_wrapper
from v114_news import neutral_snapshot
from v11_liquidity import _book_state
from v113_execution import _cost_components
from v1141_integrity import _apply_rounding, invariant_errors
from v1141_governor import _priority
from v1142_entry_now import (
    init as init_entry_now,
    evaluate as evaluate_entry_now,
    FUTURES_RELEASE_KEY as ENTRY_RELEASE_KEY,
)
from v1171_delivery import init as init_futures_delivery
from v1142_risk import (
    init as init_futures_safety,
    status as futures_safety_status,
    MAX_CONSECUTIVE_LIVE_LOSSES,
    MAX_CONCURRENT_LIVE,
)
from spot_db import init as init_spot_db, SPOT_RELEASE_VERSION
from spot_watch import (
    init as init_spot_watch,
    record_ready as record_spot_ready,
    reset_ready as reset_spot_ready,
)
from v1170_calibration import (
    wilson_interval, MIN_PROVISIONAL, MIN_CALIBRATED,
    _estimate_from_values, short_text as calibration_text,
)
from v1170_evidence import (
    futures as futures_evidence_audit,
    spot as spot_evidence_audit,
)
from spot_microstructure import analyze_book as analyze_spot_book
from spot_news import assess as assess_spot_news
from spot_market import BASE_URL as SPOT_BASE_URL
from spot_orderbook import LocalBook
from v1180_lab import init as init_v1180_lab, MIN_CHALLENGER_RESOLVED, MIN_REJECTED_RESOLVED
from v1180_manager import init as init_v1180_manager
from v11100_edge import MIN_HISTORY as V1190_MIN_HISTORY, MIN_POSITIVE_HISTORY as V1190_MIN_POSITIVE_HISTORY, MIN_POSITIVE_DAYS as V11100_MIN_POSITIVE_DAYS
from v11100_blackbox import init as init_v11100_blackbox, DECISION_RELEASE as V1190_DECISION_RELEASE
from v11100_protections import BASE_RELEASE as V11100_PROTECTION_BASE
from v11100_data import PROFILE as V11100_DATA_PROFILE
from v11100_base_contract import assert_compatible as assert_v11100_base_compatible
from v11100_stability import annotate as annotate_v11100_stability
from v11100_policy import contract as v11100_policy_contract

REQUIRED_BOT=[
    "scan","scan_short","get_klines","get_derivatives_snapshot","market_state",
    "get_news_sentiment","market_analysis_state","analyze","liquidation_snapshot",
    "for_symbol","pending_deliveries","expire_pending_deliveries",
    "mark_delivery_failed","mark_delivery_sent","enqueue_delivery","subscribers",
    "was_sent_recently","save","save_pending","subscribe","scan_status",
    "_delivery_lock","_scan_lock","_decision_details","callback","signal","status",
    "system_status","post_init","post_shutdown","main","log",
    "SIGNAL_COOLDOWN_HOURS","AUTO_SCAN_INTERVAL_MIN",
]
REQUIRED_DB=[
    "EXTRA_SIGNAL_COLUMNS","save","save_pending","save_shadow","was_sent_recently",
    "was_shadowed_recently","open_signals","subscribers","activate_signal",
]
REQUIRED_SCANNER=["_last_scan","_limit_live_results","scan_thresholds"]
REQUIRED_SIGNAL=[
    "symbol","timeframe","side","score","entry_low","entry_high","stop",
    "tp1","tp2","tp3","rr","reasons","feature_snapshot","market_context",
    "setup_type","review_window","expected_window","leverage","data_quality",
    "data_quality_total","estimated_cost_r","adl_risk","cluster_id",
    "cluster_size","cluster_rank","cluster_correlation",
]
REQUIRED_CONFIG=[
    "AUTO_SCAN_INTERVAL_MIN","SIGNAL_COOLDOWN_HOURS","MIN_SIGNAL_SCORE",
    "NEUTRAL_REGIME_SCORE_PENALTY","NEUTRAL_REGIME_MAX_SIGNALS",
    "DATABASE_PATH","TELEGRAM_BOT_TOKEN",
]

missing=[]
try:
    assert_v11100_base_compatible(Path(__file__).resolve().parent)
except Exception as exc:
    missing.append(str(exc))
for module,names in ((bot,REQUIRED_BOT),(db,REQUIRED_DB),(scanner,REQUIRED_SCANNER)):
    for name in names:
        if not hasattr(module,name):
            missing.append(f"{module.__name__}.{name}")

for name in REQUIRED_CONFIG:
    if not hasattr(config,name):
        missing.append(f"app.config.{name}")

fields=getattr(Signal,"__dataclass_fields__",{})
for name in REQUIRED_SIGNAL:
    if name not in fields:
        missing.append(f"Signal.{name}")

sig=inspect.signature(market.get_klines)
if list(sig.parameters)[:2] != ["symbol","interval"]:
    missing.append("market.get_klines signature")

try:
    from websockets.asyncio.client import connect  # noqa: F401
except Exception as exc:
    missing.append(f"websockets asyncio client: {exc}")

if int(config.AUTO_SCAN_INTERVAL_MIN)<=0:
    missing.append("AUTO_SCAN_INTERVAL_MIN must be positive")
if not str(getattr(config,"TELEGRAM_BOT_TOKEN","")).strip():
    missing.append("TELEGRAM_BOT_TOKEN is empty")
if not hasattr(config,"NEUTRAL_REGIME_MAX_SIGNALS"):
    missing.append("config.NEUTRAL_REGIME_MAX_SIGNALS")

required_db_columns={
    "strategy_version","setup_type","feature_json","release_version",
    "is_shadow","shadow_reason","delivery_state","delivered_at",
}
extra=getattr(db,"EXTRA_SIGNAL_COLUMNS",{})
if not required_db_columns.issubset(set(extra)):
    missing.append("app.db production signal columns")

for name in (
    "update_outcomes","open_signals","_iso","_utc","_until","_cost_r",
    "activate_signal","close_signal","checkpoint","get_klines_since",
    "ENTRY_EXPIRY_HOURS","SIGNAL_MAX_AGE_HOURS"
):
    if not hasattr(tracker,name):
        missing.append(f"app.tracker.{name} ambiguity-patch contract")

# Guard against a future accidental switch to the still-open candle.
try:
    kline_source=inspect.getsource(market.get_klines)
    if "close_time" not in kline_source or "Timestamp.now" not in kline_source:
        missing.append("market.get_klines closed-candle guard")
except Exception:
    missing.append("cannot inspect market.get_klines")

if len(FEATURE_NAMES)<10:
    missing.append("V11.7.1 meta feature contract")

# Critical tracker regression: delivery anchors created_at once; checkpoints
# are allowed to advance only last_checked_at.
_probe_created="2026-01-01T00:00:00+00:00"
_probe_delivered="2026-01-01T00:05:00+00:00"
_probe_checked="2026-01-01T00:20:00+00:00"
if effective_created_at(_probe_created,_probe_delivered,False)!=_probe_delivered:
    missing.append("delivery-aware created_at anchor")
if effective_last_checked_at(
    _probe_created,_probe_delivered,_probe_checked,False
)!=_probe_checked:
    missing.append("delivery-aware last_checked_at boundary")

# V11.7.1 deterministic contracts; no external network calls.
class _Long:
    side="LONG"; entry_low=100.01; entry_high=100.09; stop=98.03
    tp1=102.07; tp2=104.11; tp3=106.19
    estimated_cost_r=.10; buy_1k_bps=1; sell_1k_bps=9
    funding=0; timeframe="15M"

_probe=_apply_rounding(_Long(),{"tick_size":"0.1"})
if invariant_errors(_probe):
    missing.append("exchange tick-size rounding/invariant contract")
cost=float(_cost_components(_probe)["total_r"])
# Final rounded entry=100.1, risk=2.1:
# configured 12bps round-trip ~= .0572R + two-sided 10bps impact ~= .0477R.
if not (.10<cost<.11):
    missing.append(f"two-sided execution-cost contract ({cost})")
if _book_state(1.0,0.0,8_000,1.0,5000)!="THIN":
    missing.append("scale-aware liquidity coverage contract")
if _priority("/fapi/v1/depth")!="high" or _priority("/fapi/v1/aggTrades")!="low":
    missing.append("request-governor priority contract")

runtime_path=Path(__file__).with_name("bot_v11100.py")
runtime_source=runtime_path.read_text(encoding="utf-8") if runtime_path.exists() else ""
if "_revalidation_candidates(prepared)" not in runtime_source:
    missing.append("full qualified revalidation pool contract")
if 'APP_VERSION="11.10.0"' not in runtime_source:
    missing.append("V11.10.0 application version contract")
if "annotate_decision_edges(protection_valid)" not in runtime_source:
    missing.append("V11.10 block-bootstrap forward-edge selector is not wired")
if '"FINAL_DECISION"' not in runtime_source or "record_many_v11100_blackbox" not in runtime_source:
    missing.append("V11.10 decision black-box final-set contract")
if "init_v11100_blackbox()" not in runtime_source:
    missing.append("V11.10 black-box initialization contract")
if V1190_MIN_HISTORY<20 or V1190_MIN_POSITIVE_HISTORY<50:
    missing.append("V11.10 edge sample floors weakened")
if V11100_MIN_POSITIVE_DAYS<12:
    missing.append("V11.10 block-day diversity floor weakened")
if V11100_PROTECTION_BASE!="11.7.1-futures-evidence":
    missing.append(f"V11.10 protection evidence cohort ({V11100_PROTECTION_BASE})")
if set(V11100_DATA_PROFILE)!={"15M","1H"}:
    missing.append("V11.10 market-data coherence profile contract")
if "validate_v11100_snapshot" not in runtime_source or "market-data coherence" not in runtime_source:
    missing.append("V11.10 causal market-data contract is not wired")
if "apply_v11100_protections(meta_valid)" not in runtime_source:
    missing.append("V11.10 adaptive protection matrix is not wired")
if '"PROTECTION_REJECT"' not in runtime_source:
    missing.append("V11.10 protection black-box lineage is not wired")
if "annotate_v11100_stability(chosen,protection_valid)" not in runtime_source:
    missing.append("V11.10 #1 selection-stability diagnostic is not wired")
policy_probe=v11100_policy_contract()
if policy_probe.get("schema")!="11.10.0-policy-v1" or len(str(policy_probe.get("fingerprint") or ""))!=64:
    missing.append("V11.10 decision-policy fingerprint contract")
blackbox_source=Path(__file__).with_name("v11100_blackbox.py").read_text(encoding="utf-8")
if "decision_policy_contract()" not in blackbox_source or 'payload["decision_contract"]' not in blackbox_source:
    missing.append("V11.10 Black Box policy fingerprint is not wired")
spot_scanner_source=Path(__file__).with_name("spot_scanner.py").read_text(encoding="utf-8")
if "validate_spot_frame" not in spot_scanner_source or "data_coherence_v11100" not in spot_scanner_source:
    missing.append("V11.10 Spot market-data coherence contract is not wired")
if V1190_DECISION_RELEASE!="11.10.0-competitive-edge":
    missing.append(f"V11.10 decision release key ({V1190_DECISION_RELEASE})")
if 'FUTURES_RELEASE_VERSION="11.7.1-futures-evidence"' not in runtime_source:
    missing.append("isolated V11.7.1 Futures evidence cohort contract")
if "db.APP_VERSION=FUTURES_RELEASE_VERSION" not in runtime_source:
    missing.append("runtime must stamp Futures rows with isolated evidence cohort")
if ENTRY_RELEASE_KEY!="11.7.1-futures-evidence":
    missing.append(f"ENTRY NOW release isolation key ({ENTRY_RELEASE_KEY})")
manual_start=runtime_source.find("async def analyze_symbol_v112")
manual_end=runtime_source.find("core._analyze_symbol=analyze_symbol_v112",manual_start)
manual_source=runtime_source[manual_start:manual_end] if manual_start>=0 and manual_end>manual_start else ""
if 'if state.get("breadth_blocked")' in manual_source:
    missing.append("manual breadth-conflict parity regression")

# ENTRY NOW must be a distinct persistent gate, not just a different Telegram label.
if "entry_now_monitor_job" not in runtime_source:
    missing.append("ENTRY NOW 30-second monitor contract")
if "_arm_and_check" not in runtime_source or "_trigger_arm" not in runtime_source:
    missing.append("ENTRY NOW arm/trigger state-machine contract")

auto_start=runtime_source.find("async def run_automatic_scan_v1123")
auto_end=runtime_source.find("core._run_automatic_scan=run_automatic_scan_v1123",auto_start)
auto_source=runtime_source[auto_start:auto_end] if auto_start>=0 and auto_end>auto_start else ""
if "_arm_and_check" not in auto_source:
    missing.append("automatic scan must arm qualified setups")
if "core.save_pending(result)" in auto_source:
    missing.append("automatic scan still creates production before ENTRY NOW")

manual_scan_start=runtime_source.find("async def scan_cmd_v1142")
manual_scan_end=runtime_source.find("async def deliver_pending_v1123",manual_scan_start)
manual_scan_block=(
    runtime_source[manual_scan_start:manual_scan_end]
    if manual_scan_start>=0 and manual_scan_end>manual_scan_start else ""
)
if "core.scan_cmd=scan_cmd_v1142" not in runtime_source:
    missing.append("manual /scan override missing")
if "core.save(" in manual_scan_block or "core.save_pending(" in manual_scan_block:
    missing.append("manual /scan still creates Production before ENTRY NOW")

if "futures_safety_status()" not in runtime_source:
    missing.append("CANARY/CIRCUIT safety governor not wired into ENTRY NOW")
if "db.activate_signal(" not in runtime_source:
    missing.append("delivered ENTRY NOW is not forced ACTIVE immediately")
if "futures_active_text" not in runtime_source:
    missing.append("immutable ACTIVE FUTURES registry not wired")
if int(MAX_CONSECUTIVE_LIVE_LOSSES)!=3:
    missing.append("loss circuit breaker must trip at 3 consecutive live losses")
if int(MAX_CONCURRENT_LIVE)!=1:
    missing.append("V11.7.1 must allow at most one concurrent live Futures trade")

# Synthetic closed 1m/3m trend + live aggressive flow should produce READY;
# the same candles without live flow must remain WAIT.
import pandas as pd
def _trend():
    rows=[]
    for i in range(40):
        close=100+i*.03
        rows.append({
            "open_time":pd.Timestamp("2026-01-01T00:00:00Z")+pd.Timedelta(minutes=i),
            "close_time":pd.Timestamp("2026-01-01T00:00:59Z")+pd.Timedelta(minutes=i),
            "open":close-.02,"high":close+.02,"low":close-.04,"close":close,
            "volume":1000.0,"taker_buy_base":600.0,
        })
    return pd.DataFrame(rows)

entry_probe={
    "side":"LONG","timeframe":"1H","setup_type":"CONTINUATION",
    "entry_low":100.8,"entry_high":101.2,"stop":99.2,"tp1":103.2,
}
frame=_trend()
ready=evaluate_entry_now(
    entry_probe,frame,frame,px=101.0,
    bk={"bid":100.99,"ask":101.01},
    flow_row={"total_notional":50000,"trades":30,"age_sec":1,"buy_share":.60},
)
no_live=evaluate_entry_now(
    entry_probe,frame,frame,px=101.0,
    bk={"bid":100.99,"ask":101.01},flow_row=None,
)
if ready.state!="READY" or float(ready.score)<80:
    missing.append(f"ENTRY NOW positive trigger contract ({ready})")
if no_live.state=="READY":
    missing.append("ENTRY NOW must require live aggTrade flow")

integrity_source=Path(__file__).with_name("v1141_integrity.py").read_text(encoding="utf-8")
if '"feature_schema":"11.7.1"' not in integrity_source:
    missing.append("V11.7.1 decision-lineage feature schema")

# Adaptive learners for this materially different delivery regime must not mix
# the pre-ENTRY-NOW cohort.
for module_name in ("v11_engine.py","v112_lab.py","v113_meta.py","v113_robustness.py"):
    source=Path(__file__).with_name(module_name).read_text(encoding="utf-8")
    if "11.7.1%" not in source:
        missing.append(f"{module_name} release isolation")

risk_source=Path(__file__).with_name("v1142_risk.py").read_text(encoding="utf-8")
for contract in (
    "MIN_PROBE_DISTINCT_SYMBOLS=3","MIN_PROBE_SPAN_HOURS=2.0",
    "ROLLING_DRAWDOWN_PAUSE_R=-2.0","ROLLING_DRAWDOWN_MIN_TRADES=4",
    "distinct_symbols","span_hours","rolling_net_r"
):
    if contract not in risk_source:
        missing.append(f"Futures CANARY diversity contract {contract}")

# Spot is a separate universe: public Binance Spot source of truth, its own DB,
# its own strategy and forward journal. Futures data may only be a crowding overlay.
spot_market_source=Path(__file__).with_name("spot_market.py").read_text(encoding="utf-8")
spot_scanner_source=Path(__file__).with_name("spot_scanner.py").read_text(encoding="utf-8")
spot_strategy_source=Path(__file__).with_name("spot_strategy.py").read_text(encoding="utf-8")
spot_news_source=Path(__file__).with_name("spot_news.py").read_text(encoding="utf-8")
spot_db_source=Path(__file__).with_name("spot_db.py").read_text(encoding="utf-8")
spot_tracker_source=Path(__file__).with_name("spot_tracker.py").read_text(encoding="utf-8")
spot_micro_source=Path(__file__).with_name("spot_microstructure.py").read_text(encoding="utf-8")
spot_watch_source=Path(__file__).with_name("spot_watch.py").read_text(encoding="utf-8")
calibration_source=Path(__file__).with_name("v1170_calibration.py").read_text(encoding="utf-8")
evidence_source=Path(__file__).with_name("v1170_evidence.py").read_text(encoding="utf-8")
spot_ui_source=Path(__file__).with_name("spot_ui.py").read_text(encoding="utf-8")
futures_ui_source=Path(__file__).with_name("v11_ui.py").read_text(encoding="utf-8")
delivery_source=Path(__file__).with_name("v1171_delivery.py").read_text(encoding="utf-8")
orderbook_source=Path(__file__).with_name("spot_orderbook.py").read_text(encoding="utf-8")
live_source=Path(__file__).with_name("v11_live.py").read_text(encoding="utf-8")
edge_lab_source=Path(__file__).with_name("v1180_lab.py").read_text(encoding="utf-8")
manager_source=Path(__file__).with_name("v1180_manager.py").read_text(encoding="utf-8")
for contract in (
    "class LocalBook","last_update_id","sequence gap","@depth@100ms",
    "rest_depth","U>self.last_update_id+1","stability_score",
    "bid_replenishment_ratio","Spot depth stream silent >15s",
    "self.history.clear()","_MIN_STABILITY_SAMPLES=8",
    "_MIN_STABILITY_COVERAGE=5.0","_RECENT_GAP_BLOCK_SEC=30.0",
    "_MAX_EXCHANGE_LAG_SEC=2.5","last_exchange_event_ms","exchange_lag_sec",
    "local depth stability warming up","recent local depth sequence gap"
):
    if contract not in orderbook_source:
        missing.append(f"Spot local order-book contract {contract}")
for contract in (
    "_MAX_EXCHANGE_LAG_SEC=2.5","exchange_lag_sec","event_ts",
    "Reject out-of-order stale buckets"
):
    if contract not in live_source:
        missing.append(f"Futures live exchange-time freshness contract {contract}")
for contract in (
    "CREATE TABLE IF NOT EXISTS v1180_compare","CHALLENGER_VERSION",
    "MIN_CHALLENGER_RESOLVED=50","MIN_REJECTED_RESOLVED=15",
    "promotion_candidate","brier","_selection_gap","lower90","_prune_nonproduction"
):
    if contract not in edge_lab_source:
        missing.append(f"Champion/Challenger lab contract {contract}")
for contract in (
    "CREATE TABLE IF NOT EXISTS v1180_manager","CREATE TABLE IF NOT EXISTS v1180_failures",
    "RISK_WARNING","original STOP crossed","BAD_HEADROOM","FLOW_REVERSAL",
    "def reconcile_closed","max_favorable_r","tp1_hit",
    "EXIT already issued","release_version",
    "11.7.1-futures-evidence","11.8.1-market-intelligence"
):
    if contract not in manager_source:
        missing.append(f"Active Manager/failure-intelligence contract {contract}")

if SPOT_BASE_URL!="https://data-api.binance.vision":
    missing.append("Spot public market-data base URL")
for contract in ("/api/v3/klines","/api/v3/depth","/api/v3/aggTrades","/api/v3/ticker/24hr","/api/v3/exchangeInfo"):
    if contract not in spot_market_source:
        missing.append(f"Spot market endpoint {contract}")
if "async def klines_range" not in spot_market_source:
    missing.append("Spot bounded historical kline reconstruction contract")
for contract in (
    "def _corr30","def _portfolio_diversify","async def recheck_watch",
    "async def fresh_derivatives_risk","async def active_correlation_risk",
    "local_book_snapshot","local_book_stability","local order book not ready"
):
    if contract not in spot_scanner_source:
        missing.append(f"Spot WATCHTOWER/portfolio scanner contract {contract}")
for contract in (
    "spot_watchlist","def upsert","def active","def close",
    "def record_ready","def reset_ready","def reconcile_pending_delivery",
    "confirm_streak","last_ready_at","candidate_state","release_key",
    "SPOT_RELEASE_KEY"
):
    if contract not in spot_watch_source:
        missing.append(f"Spot persistent WATCH contract {contract}")
for contract in (
    'klines(symbol,"1d"','klines(symbol,"4h"','klines(symbol,"1h"',
    'klines(symbol,"1m",90)','depth(symbol,100)','agg_trades(symbol,1000)'
):
    if contract not in spot_scanner_source:
        missing.append(f"Spot multi-layer scan contract {contract}")
spot_auto_start=runtime_source.find("async def spot_auto_job")
spot_auto_end=runtime_source.find("async def spot_tracker_job",spot_auto_start)
spot_auto_source=runtime_source[spot_auto_start:spot_auto_end] if spot_auto_start>=0 and spot_auto_end>spot_auto_start else ""
if 's.status!="BUY"' not in spot_auto_source or 's.status=="WATCH"' in spot_auto_source:
    missing.append("Spot auto must consider BUY candidates only, never deliver WATCH")
if "enqueue_spot_delivery" not in spot_auto_source or "spot_delivery_retry_job" not in runtime_source:
    missing.append("Spot durable multi-recipient outbox contract")
for contract in (
    "_validate_futures_delivery","mark_entry_pending_delivery",
    "FUTURES_DELIVERY_MAX_AGE_MIN","pending_futures_deliveries",
    "futures_delivery_age","_deliver_forced_futures",
    "expire_all_futures_deliveries","claim_futures_delivery",
    "expire_stuck_futures_sending"
):
    if contract not in runtime_source:
        missing.append(f"Futures stale-delivery suppression contract {contract}")
for contract in (
    "_refresh_spot_payload","spot_fresh_derivatives_risk",
    "forced_chat_ids","recipient AUTO alerts disabled before delivery",
    "reconcile_spot_watch_delivery","claim_spot_delivery",
    "expire_stuck_spot_sending","claimed=False"
):
    if contract not in runtime_source:
        missing.append(f"Spot final-delivery revalidation contract {contract}")
if "spot_auto_job" not in runtime_source or "spot_tracker_job" not in runtime_source:
    missing.append("Spot scheduled scan/tracker wiring")
for contract in (
    "spot_book_monitor","set_spot_book_symbol_provider","_spot_orderbook_symbols",
    "v118-spot-local-orderbook","market_intelligence_job","edge_lab_job",
    "v118:edgelab","v118:manager"
):
    if contract not in runtime_source:
        missing.append(f"V11.8 market-intelligence runtime contract {contract}")
for contract in (
    "_arm_spot_candidate","record_spot_ready","reset_spot_ready",
    "if streak<2:","READY_PENDING","BUY_NOW",
    "Broad discovery is allowed to create READY 1/2 only"
):
    if contract not in runtime_source:
        missing.append(f"Spot persistent 2/2 BUY NOW contract {contract}")
if "expire_stuck_futures_sending(0)" not in runtime_source:
    missing.append("Futures startup ambiguous-send suppression")
if "expire_stuck_spot_sending(0)" not in runtime_source:
    missing.append("Spot startup ambiguous-send suppression")
if "futures_evidence_audit(signal)" not in runtime_source:
    missing.append("Futures independent-evidence audit wiring")
if "V1170_EVIDENCE_CONFLICT" not in runtime_source:
    missing.append("Futures evidence-conflict shadow contract")
if "v117:spotactive" not in runtime_source:
    missing.append("ACTIVE SPOT Telegram callback")
for contract in (
    "spot_watch_job","SPOT_WATCH_INTERVAL_MIN","spot_recheck_watch",
    "_spot_candidate_lock","spot_reserved_count","spot_reserved_signals",
    "spot_active_correlation_risk","active_count>=2","PENDING_DELIVERY"
):
    if contract not in runtime_source:
        missing.append(f"Spot WATCHTOWER runtime contract {contract}")
if "spot_signals" not in spot_db_source:
    missing.append("independent spot_signals journal")
for contract in ("first_tp1_at","first_invalidation_at"):
    if contract not in spot_db_source or contract not in spot_tracker_source:
        missing.append(f"Spot first-event chronology persistence {contract}")
if SPOT_RELEASE_VERSION!="11.8.1-market-intelligence":
    missing.append(f"Spot release constant ({SPOT_RELEASE_VERSION})")
for contract in (
    'SPOT_RELEASE_VERSION="11.8.1-market-intelligence"',
    "release_version TEXT NOT NULL",
    "SPOT_RELEASE_VERSION,",
    "def save(signal,delivered=False)",
    "portfolio_reserved_signals","portfolio_reserved_count",
    "active_portfolio_clusters"
):
    if contract not in spot_db_source:
        missing.append(f"Spot release/portfolio DB contract {contract}")
for contract in (
    'regime!="BEAR"','not bool(news.get("block"))','not bool(news.get("degraded"))',
    'not bool(crowd.get("extreme"))','not bool(crowd.get("degraded"))',
    'bool(micro.get("healthy"))','float(micro.get("buy_share",.5))>=.52',
    'hourly_ok','execution_ok','not overextended','in_zone',
    'daily_stack','posdays>=.50','_finite(a.min_day14)>=-12',
    'not bool(market.get("dispersion_risk"))','not bool(market.get("risk_off"))',
    'not bool(news.get("recent_negative"))','not bool(news.get("global_breaking"))',
    'bool(micro.get("closed_flow_ok"))','headroom_ok','required_rp','required_score'
):
    if contract not in spot_strategy_source:
        missing.append(f"Spot BUY hard gate {contract}")
if (
    "len(pos_sources)>=2" not in spot_news_source
    or "len(pos_events)>=2" not in spot_news_source
    or "severe_recent" not in spot_news_source
    or '"recent_negative":bool(recent_negative)' not in spot_news_source
):
    missing.append("Spot news independent-event catalyst / negative BUY veto")
if 'age_raw=item.get("age_minutes")' not in spot_news_source:
    missing.append("Spot fresh-news age=0 handling")
if "expire_pending_deliveries" not in spot_db_source or "expired_at" not in spot_db_source:
    missing.append("Spot stale outbox expiry contract")
for contract in (
    "partial_hour_processed","delivery_context","expire_delivery",
    "sending_at","def claim_delivery","def expire_stuck_sending"
):
    if contract not in spot_db_source:
        missing.append(f"Spot DB hardening contract {contract}")
if "delivered_at IS NOT NULL" not in spot_db_source:
    missing.append("Spot history/statistics must exclude undelivered BUY rows")
if 'row.get("delivered_at") or row["created_at"]' not in spot_tracker_source:
    missing.append("Spot tracker delivery-time anchor")
if "klines_range" not in spot_tracker_source or "partial_hour_processed" not in spot_tracker_source:
    missing.append("Spot post-delivery partial-hour reconstruction contract")
if "AMBIGUOUS_INVALIDATION_TP" not in spot_tracker_source or "first terminal event" not in spot_tracker_source:
    missing.append("Spot lifecycle chronology/ambiguity contract")
if "deep_errors" not in spot_scanner_source or "Spot deep data degraded" not in spot_scanner_source:
    missing.append("Spot deep-source failure must not masquerade as NO EDGE")
if "daily_errors" not in spot_scanner_source or "Spot daily data degraded" not in spot_scanner_source:
    missing.append("Spot daily-source degradation guard")
if "futures_overlay_ok" not in spot_scanner_source or '"degraded":True' not in spot_scanner_source:
    missing.append("Spot whole-Futures-overlay degradation guard")
if "def _futures_counterpart" not in spot_scanner_source or "prefix.isdigit()" not in spot_scanner_source:
    missing.append("Spot multiplier-prefixed Futures counterpart guard")
for contract in (
    "expired_at","sending_at","subscriber_enabled","other_live_count",
    "expire_all_for_signal","FUTURES_DELIVERY_TTL_MIN=3",
    "def claim(","def expire_stuck_sending","def reconcile_failed_arms"
):
    if contract not in delivery_source:
        missing.append(f"Futures durable-delivery module contract {contract}")
if "Tie-aware cross-sectional percentile" not in spot_scanner_source:
    missing.append("Spot relative-strength tie-aware percentile contract")
if "spot_evidence_audit" not in spot_scanner_source:
    missing.append("Spot independent-evidence audit wiring")
if "independent evidence gate" not in spot_scanner_source.lower():
    missing.append("Spot evidence-conflict downgrade contract")
for contract in (
    "latest_trade_age_sec<=90.0","closed_buy_share_5m","closed_buy_share_15m",
    "closed_flow_reliable","live_flow_reliable"
):
    if contract not in spot_micro_source:
        missing.append(f"Spot dual-clock flow contract {contract}")
if 'logging.getLogger("httpx").setLevel(logging.WARNING)' not in runtime_source:
    missing.append("Telegram token log hardening for httpx")
if "SPOT_DELIVERY_MAX_AGE_MIN" not in runtime_source:
    missing.append("Spot delivery freshness TTL contract")
if 'SPOT_DELIVERY_MAX_AGE_MIN=max(5,min(10,' not in runtime_source:
    missing.append("Spot delivery TTL must never be loosened above 10 minutes")
if 'FUTURES_DELIVERY_MAX_AGE_MIN=max(2,min(3,' not in runtime_source:
    missing.append("Futures ENTRY NOW delivery TTL must never be loosened above 3 minutes")
if "spot_local_book" not in runtime_source or 'float(ctx.get("entry_low") or 0)<=ask<=float(ctx.get("entry_high") or 0)' not in runtime_source:
    missing.append("Spot queued BUY must re-check local-book ask inside original entry zone")
for contract in (
    "spot_book_stability","stability_score","bid_replenishment_ratio",
    "local Spot order book unavailable"
):
    if contract not in runtime_source:
        missing.append(f"Spot local-book final-delivery gate {contract}")
for contract in (
    "spot_analyze_book","flow_reliable","closed_flow_ok",
    'float(micro.get("buy_share",.5))>=.52',
    'float(micro.get("closed_buy_share_15m",.5))>=.50',
    "spot_assess_news","spot_klines"
):
    if contract not in runtime_source:
        missing.append(f"Spot queued BUY live revalidation contract {contract}")
if "fresh Spot news/event risk invalidated BUY" not in runtime_source:
    missing.append("Spot queued BUY fresh news veto contract")
if "Spot release changed before Telegram delivery" not in runtime_source:
    missing.append("Spot queued BUY release-change suppression")
if "Spot portfolio cap reached before actual delivery" not in runtime_source:
    missing.append("Spot final-send portfolio-cap recheck")
if "mark_spot_delivery_sent(delivery_id,signal_id,ask)" not in runtime_source:
    missing.append("Spot forward journal must anchor entry to actual first delivery ask")
if "WHEN delivered_at IS NULL AND ? IS NOT NULL THEN ?" not in spot_db_source:
    missing.append("Spot DB first-delivery entry-price update contract")

spot_book={"bids":[(100.0,100),(99.95,100)],"asks":[(100.02,100),(100.05,100)]}
spot_trades=[{"time_ms":1_000_000-i*1000,"notional":1000,"buyer_taker":i%3!=0} for i in range(30)]
spot_micro=analyze_spot_book(spot_book,spot_trades,now_ms=1_000_000)
if not spot_micro.get("healthy") or float(spot_micro.get("impact_5k_bps",999))>=20:
    missing.append("Spot L2/impact deterministic contract")
news_ok=assess_spot_news({"sources":2,"items":[
    {"title":"Solana integration expands adoption","source":"A","age_minutes":30},
    {"title":"SOL partnership launches payment integration","source":"B","age_minutes":40},
]},"SOL")
news_bad=assess_spot_news({"sources":1,"items":[
    {"title":"Solana exploit causes stolen funds","source":"A","age_minutes":20},
]},"SOL")
news_zero=assess_spot_news({"sources":1,"items":[
    {"title":"Solana exploit causes stolen funds","source":"A","age_minutes":0},
]},"SOL")
if not news_ok.get("catalyst") or news_ok.get("block"):
    missing.append("Spot positive catalyst source-diversity contract")
if not news_bad.get("block"):
    missing.append("Spot severe negative-news veto contract")
if not news_zero.get("block"):
    missing.append("Spot age=0 negative-news veto contract")

# Local-book sequence contract: bridge accepted, missing update rejected.
_ob=LocalBook("TESTUSDT")
_ob.load_snapshot({"lastUpdateId":100,"bids":[(99,10)],"asks":[(101,10)],"fetched_at":1})
if _ob.apply_event({"U":101,"u":102,"b":[["99.5","5"]],"a":[]},now=2)!="APPLIED":
    missing.append("Spot local book snapshot->diff bridge")
if _ob.apply_event({"U":104,"u":104,"b":[],"a":[]},now=3)!="GAP":
    missing.append("Spot local book sequence-gap fail closed")
_lag=LocalBook("LAGUSDT")
_lag.load_snapshot({"lastUpdateId":200,"bids":[(99,10)],"asks":[(101,10)],"fetched_at":10})
_lag.apply_event({"E":1000,"U":201,"u":201,"b":[],"a":[]},now=10)
if float(_lag.last_exchange_lag_sec)<=2.5:
    missing.append("Spot local book exchange-time lag probe")
if int(MIN_CHALLENGER_RESOLVED)<50:
    missing.append("Challenger promotion sample floor")
if int(MIN_REJECTED_RESOLVED)<15:
    missing.append("Challenger rejected-sample floor")

# V11.7 probability is empirical only. A perfect-looking small sample must
# never be displayed as certainty.
lo,hi=wilson_interval(30,30)
if not (0.0<lo<0.90 and hi<=1.0):
    missing.append(f"Wilson calibration small-sample protection ({lo},{hi})")
perfect_est=_estimate_from_values([1.0]*30,"release-probe")
if perfect_est.probability is None or perfect_est.probability>=1.0:
    missing.append("perfect 30/30 sample must be probability-shrunk below 100%")
if calibration_text(perfect_est).startswith("100%"):
    missing.append("probability UI must never render 30/30 as 100% point estimate")
if int(MIN_PROVISIONAL)<30 or int(MIN_CALIBRATED)<50:
    missing.append("forward calibration sample floors")
for contract in (
    "MIN_PROVISIONAL","MIN_CALIBRATED","wilson_interval",
    "forward выборка","95% CI"
):
    if contract not in calibration_source:
        missing.append(f"calibration contract {contract}")
for contract in (
    "def futures(signal:Any)","def spot(signal:Any)",
    "hard_conflict","support","conflict"
):
    if contract not in evidence_source:
        missing.append(f"independent evidence contract {contract}")
if "99.9" in calibration_source:
    missing.append("calibration module contains forbidden fake 99.9 probability")
if "forward-оценка" not in spot_ui_source.lower():
    missing.append("Spot calibrated forward-estimate UI")
for contract in (
    "first_tp1_at","first_invalidation_at","tp1_at.timestamp()>horizon",
    "invalid_at<=tp1_at"
):
    if contract not in calibration_source:
        missing.append(f"Spot calibrated TP1-before-stop chronology contract {contract}")
if 'SPOT_RELEASE_KEYS=("11.8.1-market-intelligence",)' not in calibration_source:
    missing.append("Spot calibration release isolation")
if "forward-оценка" not in futures_ui_source.lower():
    missing.append("Futures calibrated forward-estimate UI")
# V11.10.0 premium presentation must preserve unambiguous safety language.
for contract in ("НЕ ВХОДИТЬ СЕЙЧАС","ENTRY NOW CONFIRMED","<blockquote>","<pre>"):
    if contract not in futures_ui_source:
        missing.append(f"Futures premium UI safety contract {contract}")
for contract in ("ПОКА НЕ ПОКУПАТЬ","ВХОД РАЗРЕШЁН СЕЙЧАС","forward-оценка","<blockquote>","<pre>"):
    if contract not in spot_ui_source:
        missing.append(f"Spot premium UI safety contract {contract}")

# High-frequency SQLite contexts must deterministically close. Python's native
# Connection context commits/rolls back but does not close the handle. Detect
# actual ``with sqlite3.connect(...)`` AST nodes rather than comments/docstrings.
def _legacy_sqlite_with(source):
    tree=ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node,(ast.With,ast.AsyncWith)):
            continue
        for item in node.items:
            call=item.context_expr
            if not isinstance(call,ast.Call):
                continue
            fn=call.func
            if (isinstance(fn,ast.Attribute) and fn.attr=="connect"
                    and isinstance(fn.value,ast.Name) and fn.value.id=="sqlite3"):
                return True
    return False

for module_name in (
    "bot_v11100.py","spot_db.py","spot_watch.py","v112_details.py","v112_health.py","v112_lab.py",
    "v113_meta.py","v113_robustness.py","v113_tracking.py","v114_entry.py",
    "v11_engine.py","v11_ui.py","v114_db.py","v1142_entry_now.py","v1142_risk.py",
    "v1171_delivery.py","v11100_edge.py","v11100_blackbox.py","v11100_replay.py",
    "v11100_protections.py","v11100_data.py","v11100_base_contract.py","v11100_stability.py","v11100_policy.py"
):
    source=Path(__file__).with_name(module_name).read_text(encoding="utf-8")
    if _legacy_sqlite_with(source):
        missing.append(f"SQLite connection leak risk in {module_name}")
if not Path(__file__).with_name("v1171_sqlite.py").exists():
    missing.append("deterministic SQLite session helper missing")

if missing:
    raise SystemExit("V11.10.0 RELEASE CHECK FAILED:\\n- "+"\\n- ".join(missing))

# Safe local migration + integrity verification. No Binance/Telegram network call.
# Close legacy app.db connection contexts even during this pre-start process.
install_connect_wrapper()
db.init()
init_futures_delivery()
init_entry_now()
init_futures_safety()
init_spot_db()
init_spot_watch()
init_v1180_lab()
init_v1180_manager()
init_v11100_blackbox()
hard=harden_database()
dbs=db_runtime_status()
if hard.get("journal_mode")!="wal" or str(dbs.get("journal","")).lower()!="wal":
    raise SystemExit(f"V11.10.0 SQLITE WAL FAILED: {hard} / {dbs}")
if not dbs.get("ok"):
    raise SystemExit(f"V11.10.0 SQLITE HEALTH FAILED: {dbs}")

import sqlite3
with db_session(timeout=10) as c:
    quick=c.execute("PRAGMA quick_check").fetchone()
    if not quick or str(quick[0]).lower()!="ok":
        raise SystemExit(f"V11.10.0 SQLITE QUICK_CHECK FAILED: {quick}")
    tables={r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    required_tables={
        "signals","signal_deliveries","subscribers","v114_db_health",
        "v1142_armed","v1142_safety","spot_signals","spot_deliveries","spot_watchlist",
        "v1180_compare","v1180_manager","v1180_failures","v1190_blackbox",
        "v11100_policy_contract"
    }
    if not required_tables.issubset(tables):
        raise SystemExit(f"V11.10.0 SQLITE TABLES MISSING: {required_tables-tables}")
    core_delivery_cols={r[1] for r in c.execute("PRAGMA table_info(signal_deliveries)").fetchall()}
    for required in ("expired_at","sending_at"):
        if required not in core_delivery_cols:
            raise SystemExit(
                f"V11.7.1 FUTURES DELIVERY MIGRATION FAILED: {required} missing"
            )
    armed_cols={r[1] for r in c.execute("PRAGMA table_info(v1142_armed)").fetchall()}
    if "release_key" not in armed_cols:
        raise SystemExit("V11.7.1 FUTURES ARM MIGRATION FAILED: release_key missing")
    spot_signal_cols={r[1] for r in c.execute("PRAGMA table_info(spot_signals)").fetchall()}
    if "partial_hour_processed" not in spot_signal_cols:
        raise SystemExit("V11.7.1 SPOT SIGNAL MIGRATION FAILED: partial_hour_processed missing")
    spot_delivery_cols={r[1] for r in c.execute("PRAGMA table_info(spot_deliveries)").fetchall()}
    for required in ("expired_at","sending_at"):
        if required not in spot_delivery_cols:
            raise SystemExit(
                f"V11.7.1 SPOT DELIVERY MIGRATION FAILED: {required} missing"
            )
    watch_cols={r[1] for r in c.execute("PRAGMA table_info(spot_watchlist)").fetchall()}
    required_watch_cols={
        "candidate_state","confirm_streak","last_ready_at","ready_score","release_key"
    }
    if not required_watch_cols.issubset(watch_cols):
        raise SystemExit(
            f"V11.7.1 SPOT WATCH MIGRATION FAILED: {required_watch_cols-watch_cols}"
        )
    safety_cols={r[1] for r in c.execute("PRAGMA table_info(v1142_safety)").fetchall()}
    if "release_key" not in safety_cols:
        raise SystemExit("V11.7.1 FUTURES SAFETY MIGRATION FAILED: release_key missing")

# Never silently run a learning release on ephemeral Railway storage.
railway=any(os.getenv(k) for k in (
    "RAILWAY_ENVIRONMENT","RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID","RAILWAY_SERVICE_ID",
))
if railway and not str(config.DATABASE_PATH).startswith("/data/"):
    raise SystemExit(
        "V11.10.0 RAILWAY PERSISTENCE FAILED: set a Volume at /data and "
        "DATABASE_PATH=/data/signals.db"
    )

# News fallback must be directionally neutral and explicitly synthetic.
fallback=neutral_snapshot("release-check",6)
if (
    float(fallback.get("global",1))!=0
    or fallback.get("assets")
    or fallback.get("breaking_events")
    or int(fallback.get("real_sources",-1))!=0
    or not fallback.get("v114_news_degraded")
):
    raise SystemExit("V11.10.0 NEWS FAILOVER CONTRACT FAILED")

indicator_result=indicator_selftest()
if not indicator_result["lookahead_ok"] or not indicator_result["recursive_ok"]:
    raise SystemExit(
        "V11.10.0 INDICATOR SELF-TEST FAILED:\n- "
        +"\n- ".join(indicator_result["issues"])
    )

print("V11.10.0 RELEASE CHECK: OK")
print("indicator lookahead/recursive self-test: OK")
print("SQLite WAL/quick_check: OK")
print("neutral news failover contract: OK")
print(f"auto interval={config.AUTO_SCAN_INTERVAL_MIN}m; database={config.DATABASE_PATH}")
if not str(config.DATABASE_PATH).startswith("/data/"):
    print("WARNING: local/non-Railway database path is not persistent /data.")
