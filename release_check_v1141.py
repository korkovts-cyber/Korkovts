"""Fail-fast compatibility and SQLite gate for V11.4.1. No external network calls."""

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
from v113_meta import FEATURE_NAMES
from v113_tracking import effective_created_at, effective_last_checked_at
from v113_micro import evaluate as evaluate_micro
from v113_execution import evaluate_quote
from v113_biascheck import run as indicator_selftest
from v114_db import harden_database, status as db_runtime_status
from v114_news import neutral_snapshot
from v11_liquidity import _book_state
from v113_execution import _cost_components
from v1141_integrity import _apply_rounding, invariant_errors
from v1141_governor import _priority

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
    "was_shadowed_recently","open_signals","subscribers",
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
    missing.append("V11.4.1 meta feature contract")

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

# New V11.4.1 deterministic contracts; no external network calls.
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

runtime_path=Path(__file__).with_name("bot_v1141.py")
runtime_source=runtime_path.read_text(encoding="utf-8") if runtime_path.exists() else ""
if "_revalidation_candidates(prepared)" not in runtime_source:
    missing.append("full qualified revalidation pool contract")
manual_start=runtime_source.find("async def analyze_symbol_v112")
manual_end=runtime_source.find("core._analyze_symbol=analyze_symbol_v112",manual_start)
manual_source=runtime_source[manual_start:manual_end] if manual_start>=0 and manual_end>manual_start else ""
if 'if state.get("breadth_blocked")' in manual_source:
    missing.append("manual breadth-conflict parity regression")

if missing:
    raise SystemExit("V11.4.1 RELEASE CHECK FAILED:\\n- "+"\\n- ".join(missing))

# Safe local migration + integrity verification. No Binance/Telegram network call.
db.init()
hard=harden_database()
dbs=db_runtime_status()
if hard.get("journal_mode")!="wal" or str(dbs.get("journal","")).lower()!="wal":
    raise SystemExit(f"V11.4.1 SQLITE WAL FAILED: {hard} / {dbs}")
if not dbs.get("ok"):
    raise SystemExit(f"V11.4.1 SQLITE HEALTH FAILED: {dbs}")

import sqlite3
with sqlite3.connect(config.DATABASE_PATH,timeout=10) as c:
    quick=c.execute("PRAGMA quick_check").fetchone()
    if not quick or str(quick[0]).lower()!="ok":
        raise SystemExit(f"V11.4.1 SQLITE QUICK_CHECK FAILED: {quick}")
    tables={r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    required_tables={"signals","signal_deliveries","subscribers","v114_db_health"}
    if not required_tables.issubset(tables):
        raise SystemExit(f"V11.4.1 SQLITE TABLES MISSING: {required_tables-tables}")

# Never silently run a learning release on ephemeral Railway storage.
railway=any(os.getenv(k) for k in (
    "RAILWAY_ENVIRONMENT","RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID","RAILWAY_SERVICE_ID",
))
if railway and not str(config.DATABASE_PATH).startswith("/data/"):
    raise SystemExit(
        "V11.4.1 RAILWAY PERSISTENCE FAILED: set a Volume at /data and "
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
    raise SystemExit("V11.4.1 NEWS FAILOVER CONTRACT FAILED")

indicator_result=indicator_selftest()
if not indicator_result["lookahead_ok"] or not indicator_result["recursive_ok"]:
    raise SystemExit(
        "V11.4.1 INDICATOR SELF-TEST FAILED:\n- "
        +"\n- ".join(indicator_result["issues"])
    )

print("V11.4.1 RELEASE CHECK: OK")
print("indicator lookahead/recursive self-test: OK")
print("SQLite WAL/quick_check: OK")
print("neutral news failover contract: OK")
print(f"auto interval={config.AUTO_SCAN_INTERVAL_MIN}m; database={config.DATABASE_PATH}")
if not str(config.DATABASE_PATH).startswith("/data/"):
    print("WARNING: local/non-Railway database path is not persistent /data.")
