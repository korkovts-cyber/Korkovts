"""V11.22.6 · Stable deep engine + API burst control.

This release fixes the live production defects seen after the broad pipeline
started completing 210/210 primary and 72/72 multi-TF.

ROOT CAUSES FIXED
1) V11.22.4 started a 30s timeout BEFORE a full-deep candidate acquired the
   deep semaphore. Candidates in the queue could timeout without ever starting
   their real derivatives/strategy work.
2) Full-deep verification did not classify DEEP_CANDIDATE_TIMEOUT as an API /
   technical failure, which produced the contradictory UI:
      "Full-deep verified 14/14" + "Full-deep 0/14 complete".
3) Each deep candidate requests several Binance /futures/data/* endpoints.
   Bursting many candidates together can hit endpoint-specific throttles even
   while the global request-weight counter looks safe.
4) Futures and Spot can request the same derivatives snapshot close together;
   duplicate in-flight work wastes API budget.

FIXES
- Queue-safe deep timeout: timeout starts only AFTER the candidate gets its slot.
- Deep concurrency reduced to 3 to avoid endpoint bursts; candidate work gets a
  65s execution budget after slot acquisition.
- Full scan budget 540s, leaving enough room for a safe paced deep stage.
- Separate 1.6 req/s limiter for Binance /futures/data/* endpoints while primary
  klines retain the 4 req/s research pace.
- 25s derivatives snapshot cache + in-flight de-duplication.
- DEEP_CANDIDATE_TIMEOUT is now a technical failure in verified coverage.
- Deep timeout counters reset per scan; UI shows current-cycle truth.
- Spot/Futures independence from V11.22.5 remains.
- All final strategy, execution, evidence, ADL and risk gates remain mandatory.

No trading score threshold is weakened.
"""
from __future__ import annotations

import asyncio
import copy
import time
from collections import Counter

import bot_v11191 as runtime
import app.market as market
import v11191_futures_engine as futures
import spot_scanner as spot_legacy
import v11217_reliability as rel217
import v11224_full_deep_repair as deep224

base = runtime.base
VERSION = "11.22.6"

# ---------------------------------------------------------------------------
# 1) Endpoint-specific limiter for Binance futures-data endpoints.
# ---------------------------------------------------------------------------
_underlying_get = market._get
_futures_data_lock = asyncio.Lock()
_futures_data_next = 0.0
FUTURES_DATA_RPS = 1.6

async def market_get_v11226(path, params=None):
    global _futures_data_next
    path_s = str(path or "")
    if path_s.startswith("/futures/data/"):
        async with _futures_data_lock:
            now = time.monotonic()
            wait = max(0.0, _futures_data_next - now)
            if wait:
                await asyncio.sleep(wait)
            _futures_data_next = max(time.monotonic(), _futures_data_next) + (1.0 / FUTURES_DATA_RPS)
    return await _underlying_get(path, params)

market._get = market_get_v11226

# ---------------------------------------------------------------------------
# 2) Snapshot cache + in-flight dedupe.
# ---------------------------------------------------------------------------
_original_snapshot = futures.legacy.get_derivatives_snapshot
_snapshot_cache = {}
_snapshot_inflight = {}
_snapshot_lock = asyncio.Lock()

async def derivatives_snapshot_v11226(symbol, adl=None):
    symbol = str(symbol or "").upper()
    now = time.monotonic()
    row = _snapshot_cache.get(symbol)
    if row and row[0] > now:
        return copy.deepcopy(row[1])

    creator = False
    async with _snapshot_lock:
        row = _snapshot_cache.get(symbol)
        if row and row[0] > time.monotonic():
            return copy.deepcopy(row[1])
        task = _snapshot_inflight.get(symbol)
        if task is None or task.done():
            task = asyncio.create_task(_original_snapshot(symbol, adl))
            _snapshot_inflight[symbol] = task
            creator = True

    try:
        result = await task
        if isinstance(result, dict):
            ttl = 25.0 if bool(result.get("deep_data")) else 8.0
            _snapshot_cache[symbol] = (time.monotonic() + ttl, copy.deepcopy(result))
        return copy.deepcopy(result)
    finally:
        if creator:
            async with _snapshot_lock:
                if _snapshot_inflight.get(symbol) is task:
                    _snapshot_inflight.pop(symbol, None)

# Patch all relevant runtime aliases.
futures.legacy.get_derivatives_snapshot = derivatives_snapshot_v11226
market.get_derivatives_snapshot = derivatives_snapshot_v11226
spot_legacy.get_derivatives_snapshot = derivatives_snapshot_v11226

# ---------------------------------------------------------------------------
# 3) Queue-safe full-deep.
# ---------------------------------------------------------------------------
futures.DEEP_CONCURRENCY = 3
futures.FULL_SCAN_BUDGET_SEC = max(
    540, int(getattr(futures, "FULL_SCAN_BUDGET_SEC", 420) or 420)
)

# Bypass V11.22.4's queue-counting timeout and call the proven pre-timeout chain.
_pre_timeout_deep = deep224._original_deep_one

def _reset_deep_stats():
    deep224._deep_stats["runs"] = 0
    deep224._deep_stats["ok"] = 0
    deep224._deep_stats["timeouts"] = 0
    deep224._deep_stats["errors"] = 0
    deep224._deep_stats["reasons"] = Counter()
    deep224._deep_stats["last_ms"] = []

class _NoopSem:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False

_noop_sem = _NoopSem()

async def deep_one_v11226(row, kind, market_context, news, adl_risks, min_score, sem):
    symbol = str(row[0]) if row else "?"
    deep224._deep_stats["runs"] += 1

    # IMPORTANT: queue time is outside the execution timeout.
    async with sem:
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                _pre_timeout_deep(
                    row, kind, market_context, news, adl_risks, min_score, _noop_sem
                ),
                timeout=65.0,
            )
            elapsed = (time.monotonic() - started) * 1000.0
            signal, reason, payload = result
            key = str(reason or "PASS")
            deep224._deep_stats["reasons"][key] += 1
            if not reason:
                deep224._deep_stats["ok"] += 1
            elif str(reason).startswith("ERROR:"):
                deep224._deep_stats["errors"] += 1
            deep224._deep_stats["last_ms"].append((symbol, round(elapsed)))
            deep224._deep_stats["last_ms"] = deep224._deep_stats["last_ms"][-20:]
            if isinstance(payload, dict):
                payload["_v11226_deep_ms"] = round(elapsed, 1)
                payload["_v11226_queue_safe_timeout"] = True
            return result
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - started) * 1000.0
            deep224._deep_stats["timeouts"] += 1
            deep224._deep_stats["reasons"]["DEEP_CANDIDATE_TIMEOUT"] += 1
            deep224._deep_stats["last_ms"].append((symbol, round(elapsed)))
            deep224._deep_stats["last_ms"] = deep224._deep_stats["last_ms"][-20:]
            return None, "DEEP_CANDIDATE_TIMEOUT", {
                "_error": "deep execution exceeded 65s after semaphore acquisition",
                "_v11226_deep_ms": round(elapsed, 1),
                "_v11226_queue_safe_timeout": True,
            }
        except Exception as exc:
            elapsed = (time.monotonic() - started) * 1000.0
            deep224._deep_stats["errors"] += 1
            key = f"ERROR:{type(exc).__name__}"
            deep224._deep_stats["reasons"][key] += 1
            return None, key, {
                "_error": str(exc),
                "_v11226_deep_ms": round(elapsed, 1),
            }

futures._deep_one = deep_one_v11226

# ---------------------------------------------------------------------------
# 4) Truthful deep verification.
# ---------------------------------------------------------------------------
def deep_verification_v11226(d):
    rejections = dict((d or {}).get("rejections") or {})
    technical = (
        "DERIVATIVES_INCOMPLETE",
        "SCAN_DEADLINE",
        "DEEP_CANDIDATE_TIMEOUT",
    )
    failed = sum(int(rejections.get(k, 0) or 0) for k in technical)
    failed += sum(
        int(v or 0) for k, v in rejections.items()
        if str(k).startswith("ERROR:") or str(k).startswith("RATE_LIMIT")
    )
    total = int((d or {}).get("deep_checked", 0) or 0)
    verified = max(0, total - failed)
    coverage = verified / max(1, total)
    return total, verified, coverage

rel217._deep_verification = deep_verification_v11226

# Reset current-cycle timeout counters immediately before a full raw scan.
_prev_raw_scan = base._raw_scan
_prev_raw_short = base._raw_short

async def raw_scan_v11226():
    _reset_deep_stats()
    return await _prev_raw_scan()

async def raw_short_v11226():
    _reset_deep_stats()
    return await _prev_raw_short()

base._raw_scan = raw_scan_v11226
base._raw_short = raw_short_v11226

# ---------------------------------------------------------------------------
# 5) Diagnostics.
# ---------------------------------------------------------------------------
_old_hb = base.heartbeat_text
def heartbeat_v11226(diagnostics, **kwargs):
    text = _old_hb(diagnostics, **kwargs)
    try:
        d = dict(diagnostics or {})
        total, verified, coverage = deep_verification_v11226(d)
        complete = int(d.get("deep_complete", 0) or 0)
        timeouts = int((d.get("rejections") or {}).get("DEEP_CANDIDATE_TIMEOUT", 0) or 0)
        # Explicit truth line disambiguates "verified" from strategy rejection.
        if total:
            text += (
                f"\n🧾 Deep truth: verified-data <b>{verified}/{total}</b>"
                f" · strategy-complete <b>{complete}/{total}</b>"
                f" · timeouts <b>{timeouts}</b>"
            )
        text += (
            f"\n🚦 Futures-data limiter: <b>{FUTURES_DATA_RPS:.1f} req/s</b>"
            f" · deep concurrency <b>{futures.DEEP_CONCURRENCY}</b>"
            f"\n♻️ Derivatives cache: <b>{len(_snapshot_cache)}</b>"
            f" · inflight <b>{len(_snapshot_inflight)}</b>"
        )
    except Exception:
        pass
    return text
base.heartbeat_text = heartbeat_v11226

_old_health = base.health_text
def health_v11226(h):
    text = _old_health(h)
    for old in (
        "V11.22.5","V11.22.4","V11.22.3","V11.22.2","V11.22.1",
        "V11.22.0","V11.21.9","V11.21.8","V11.21.7","V11.21.6"
    ):
        text = text.replace(old, VERSION)
    text += (
        f"\nStable deep engine: <b>ACTIVE</b>"
        f" · queue-safe timeout 65s"
        f" · futures-data {FUTURES_DATA_RPS:.1f} req/s"
        f" · deep concurrency {futures.DEEP_CONCURRENCY}"
    )
    return text
base.health_text = health_v11226

def install():
    market._get = market_get_v11226
    market.get_derivatives_snapshot = derivatives_snapshot_v11226
    futures.legacy.get_derivatives_snapshot = derivatives_snapshot_v11226
    spot_legacy.get_derivatives_snapshot = derivatives_snapshot_v11226
    futures._deep_one = deep_one_v11226
    rel217._deep_verification = deep_verification_v11226
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
