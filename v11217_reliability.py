"""V11.21.7 · Production reliability + Spot AUTO delivery overlay.

Goals:
- preserve the complete V11.18/V11.21 Production pipeline;
- prevent full-deep Futures candidates from dying in a shared pacing/cooldown queue;
- stop a partial deep API pass from being reported as a truthful "no signal" result;
- make Spot AUTO a first-class path: 15-minute discovery + 1-minute WATCH promotion.

The final Production/Strong/Indicator/Adaptive/Final-Risk/ENTRY gates are not weakened.
"""
from __future__ import annotations

import asyncio
import time

import bot_v11191 as runtime
import v11191_futures_engine as futures
import v11196_api_resilience as api_resilience
import v11200_data_architecture as data_arch
import v1141_governor as governor

base = runtime.base
VERSION = "11.21.7"

# ---------------------------------------------------------------------------
# 1) Binance request budget.
# ---------------------------------------------------------------------------
# Keep the whole-market pass fast enough to finish, while avoiding the previous
# 4.5 rps burst profile on a shared Railway IP.
RESEARCH_RPS = min(float(getattr(data_arch, "REQUESTS_PER_SEC", 4.5) or 4.5), 4.0)
data_arch.REQUESTS_PER_SEC = max(3.5, RESEARCH_RPS)
data_arch.MIN_START_GAP = 1.0 / data_arch.REQUESTS_PER_SEC

# Preserve headroom for Health / ENTRY NOW.  The deep-stage helper below waits
# *before* starting a snapshot if the current minute is already busy, so the
# soft guard does not sleep inside the snapshot freshness timer.
api_resilience.SOFT_WEIGHT_CEILING = min(
    int(getattr(api_resilience, "SOFT_WEIGHT_CEILING", 1150) or 1150), 1050
)
api_resilience.ANALYSIS_CONCURRENCY = min(
    int(getattr(api_resilience, "ANALYSIS_CONCURRENCY", 4) or 4), 3
)
api_resilience._analysis_sem = asyncio.Semaphore(max(2, api_resilience.ANALYSIS_CONCURRENCY))

# Conservative pacing plus an occasional minute-boundary wait needs more than
# the old 175s target.  AUTO is still scheduled every 10 minutes.
futures.FULL_SCAN_BUDGET_SEC = max(
    240, min(280, int(getattr(futures, "FULL_SCAN_BUDGET_SEC", 240) or 240))
)
# One full snapshot at a time prevents 4 candidates from competing for the same
# paced request queue. Each snapshot's own components still run concurrently.
futures.DEEP_CONCURRENCY = 1

DEEP_START_WEIGHT_CEILING = 820


async def _wait_for_deep_window():
    """Wait outside snapshot acquisition until Binance budget is safe.

    This is intentionally outside the timed derivatives fetch, so a 429 cooldown
    or minute-boundary budget wait is not misclassified as stale market evidence.
    The scanner's overall deadline still remains authoritative and can cancel us.
    """
    while True:
        gs = governor.status()
        cooldown = max(0.0, float(gs.get("cooldown_seconds", 0) or 0))
        if cooldown > 0:
            await asyncio.sleep(cooldown + 0.25)
            continue

        now = time.time()
        minute = int(now // 60)
        budget_minute = int(api_resilience._budget.get("minute") or -1)
        used = max(
            int(api_resilience._budget.get("last_used", 0) or 0),
            int(gs.get("last_used_weight_1m", 0) or 0),
        )
        if budget_minute == minute and used >= DEEP_START_WEIGHT_CEILING:
            await asyncio.sleep(max(0.10, 60.20 - (now % 60)))
            continue
        return


async def _snapshot_with_retry(symbol, adl):
    """One controlled retry, without retrying inside a real cooldown."""
    last = None
    for attempt in range(2):
        await _wait_for_deep_window()
        try:
            # A healthy single-candidate snapshot should finish well below the
            # existing 8s/12s Production freshness limits.  18s is only an outer
            # deadlock/network guard; a slow completed snapshot is still rejected
            # by the unchanged Production freshness gate.
            return await asyncio.wait_for(
                futures.legacy.get_derivatives_snapshot(symbol, adl), timeout=18.0
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last = exc
            if attempt >= 1:
                break
            # If the failure created a 429/418 cooldown, the next loop waits the
            # full server-directed interval *before* starting a fresh snapshot.
            if max(0.0, float(governor.status().get("cooldown_seconds", 0) or 0)) <= 0:
                await asyncio.sleep(1.0)
    raise last or TimeoutError("deep derivatives snapshot unavailable")


async def _deep_one_v11217(row, kind, market_context, news, adl_risks, min_score, sem):
    """V11.21.6 decision path with resilient data acquisition only."""
    symbol, lower, base_frame, higher, soft_l, soft_s = row
    try:
        async with sem:
            adl = adl_risks.get(symbol) if isinstance(adl_risks, dict) else None
            d = await _snapshot_with_retry(symbol, adl)

            # One incomplete snapshot may be rebuilt once. It remains fail-closed:
            # no candidate reaches analyze() unless original deep_data is true.
            if not d.get("deep_data"):
                await asyncio.sleep(0.6)
                try:
                    d2 = await _snapshot_with_retry(symbol, adl)
                    if d2 and int(d2.get("data_quality", 0) or 0) > int(d.get("data_quality", 0) or 0):
                        d = d2
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

        if not d.get("deep_data"):
            return None, "DERIVATIVES_INCOMPLETE", d

        oi_notional = futures._f(d.get("open_interest")) * futures._f(d.get("mark_price"))
        d.update(futures.liquidation_snapshot(symbol, oi_notional))

        timeframe = "1H" if kind == "main" else "15M"
        strategy_audit = {}
        result = futures.legacy.analyze(
            symbol, timeframe, base_frame, higher, float(min_score), lower,
            market_context.get("bias"), d,
            futures.legacy.for_symbol(news, symbol), market_context,
            audit=strategy_audit,
        )
        fallback_used = False
        if result is None:
            inferred = "LONG" if float(soft_l) >= float(soft_s) else "SHORT"
            result = futures._momentum_fallback(
                symbol, timeframe, base_frame, higher, lower, inferred, d,
                market_context, futures.legacy.for_symbol(news, symbol), min_score,
            )
            if result is None:
                d["_strategy_audit"] = strategy_audit
                return None, "FINAL_STRATEGY_REJECT", d
            fallback_used = True
            d["_strategy_audit"] = strategy_audit
            d["_v11210_fallback"] = True

        side = str(getattr(result, "side", "") or "").upper()
        historical_penalty = max(
            0.0, float(futures.calibration_penalty(symbol, side, timeframe) or 0)
        )
        result.feature_snapshot.setdefault("v11212_cohort_isolation", {}).update({
            "historical_calibration_penalty": historical_penalty,
            "calibration_shadow_only": True,
        })

        # V11.21.6 cohort policy stays unchanged: calibration is shadow-only.
        penalty = 0.0
        if penalty > 0:
            threshold = min(95.0, float(min_score) + penalty)
            if fallback_used:
                raw = float((result.feature_snapshot.get("decision") or {}).get(
                    "raw_long" if side == "LONG" else "raw_short", 0
                ) or 0)
                if raw < threshold:
                    return None, "CALIBRATION_REJECT", d
            else:
                strategy_audit = {}
                result = futures.legacy.analyze(
                    symbol, timeframe, base_frame, higher, threshold, lower,
                    market_context.get("bias"), d,
                    futures.legacy.for_symbol(news, symbol), market_context,
                    audit=strategy_audit,
                )
                if result is None:
                    d["_strategy_audit"] = strategy_audit
                    return None, "CALIBRATION_REJECT", d
        if kind != "main":
            result.expected_window = "30 минут–4 часа"
        return result, "", d
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return None, f"ERROR:{type(exc).__name__}", {"_error": str(exc)}


# _run() resolves this global at execution time.  Only the raw deep acquisition
# step is replaced; the V11.18 Production pipeline remains above it.
futures._deep_one = _deep_one_v11217


# ---------------------------------------------------------------------------
# 2) Truthful full-deep completion.
# ---------------------------------------------------------------------------
def _deep_verification(d):
    rejections = dict((d or {}).get("rejections") or {})
    failed = int(rejections.get("DERIVATIVES_INCOMPLETE", 0) or 0)
    failed += int(rejections.get("SCAN_DEADLINE", 0) or 0)
    failed += sum(
        int(v or 0) for k, v in rejections.items()
        if str(k).startswith("ERROR:") or str(k).startswith("RATE_LIMIT")
    )
    total = int((d or {}).get("deep_checked", 0) or 0)
    verified = max(0, total - failed)
    coverage = verified / max(1, total)
    return total, verified, coverage


def _mark_deep_truth(kind, results):
    d = futures._last.get(kind, {})
    total, verified, coverage = _deep_verification(d)
    d["deep_verified"] = verified
    d["deep_verification_coverage"] = round(coverage, 4)
    d["deep_api_failed"] = max(0, total - verified)

    # API failure is not a market rejection. If no signal survived and too much
    # of the deep shortlist was never verifiably checked, report incomplete scan.
    if total and not results and coverage < 0.80:
        reason = f"deep verification coverage incomplete: {verified}/{total} ({coverage:.0%})"
        futures._finish(d, "error", reason)
        raise RuntimeError(reason)

    # If verified signals exist, keep them, but retain degraded coverage truth.
    if total and coverage < 1.0 and results:
        d["status"] = "degraded"
        d["reason"] = f"verified signals found with partial deep coverage {verified}/{total}"
    futures._last[kind] = futures.copy.deepcopy(d)
    try:
        futures.legacy._last_scan[kind] = futures.copy.deepcopy(d)
    except Exception:
        pass
    return results


# IMPORTANT: bot_v11180 captured the raw scanner in base._raw_scan/_raw_short and
# wraps it with _health_gate + _prepare (Production/Alpha/execution/evidence/etc.).
# Patch those raw hooks, NEVER base.core.scan, otherwise Production would be bypassed.
_original_raw_scan = base._raw_scan
_original_raw_short = base._raw_short

async def raw_scan_v11217():
    return _mark_deep_truth("main", await _original_raw_scan())

async def raw_short_v11217():
    return _mark_deep_truth("short", await _original_raw_short())

base._raw_scan = raw_scan_v11217
base._raw_short = raw_short_v11217
runtime.futures_scan = raw_scan_v11217
runtime.futures_scan_short = raw_short_v11217
runtime.scanner.scan = raw_scan_v11217
runtime.scanner.scan_short = raw_short_v11217


# Add verified/full-deep truth + Spot AUTO truth to the existing 10-minute card.
_original_heartbeat = base.heartbeat_text

def heartbeat_text_v11217(diagnostics, **kwargs):
    text = _original_heartbeat(diagnostics, **kwargs)
    total, verified, coverage = _deep_verification(diagnostics or {})
    if total:
        icon = "✅" if coverage >= .95 else ("⚠️" if coverage >= .80 else "🛑")
        text += f"\n{icon} Full-deep verified: <b>{verified}/{total}</b> ({coverage*100:.0f}%)"
    try:
        rows = list(base.active_spot_watches(20))
        ready = sum(int(row.get("confirm_streak") or 0) >= 1 for row in rows)
        text += (
            f"\n🟢 Spot AUTO: <b>ON</b> · discovery <b>15m</b> · WATCH <b>1m</b> "
            f"· active <b>{len(rows)}</b> · READY <b>{ready}</b>"
        )
    except Exception:
        text += "\n🟢 Spot AUTO: <b>ON</b> · discovery <b>15m</b> · WATCH <b>1m</b>"
    return text

base.heartbeat_text = heartbeat_text_v11217


# ---------------------------------------------------------------------------
# 3) Spot AUTO.
# ---------------------------------------------------------------------------
base.SPOT_AUTO_INTERVAL_MIN = 15
base.SPOT_WATCH_INTERVAL_MIN = 1

_original_spot_auto_job = base.spot_auto_job
# bot_v11191 stores the pre-gate V11.18 WATCH function here. Use it because this
# overlay owns the research gate itself and must not double-lock it.
_original_spot_watch_job = getattr(
    runtime, "_original_spot_watch_job_v11213", base.spot_watch_job
)

async def spot_auto_job_v11217(context):
    """15-minute Spot discovery; inherited spot_scan keeps heavy-scan serialization."""
    try:
        if not list(base.core.subscribers()):
            return
        health = await base.health_check(force=False)
        base._last_health = health
        if bool(getattr(health, "hard_pause", False)) or str(getattr(health, "status", "")).upper() == "PAUSE":
            base.core.log.warning("V11.21.7 Spot AUTO skipped: Production Health PAUSE")
            return
        return await _original_spot_auto_job(context)
    except Exception:
        base.core.log.exception("V11.21.7 Spot AUTO cycle failed")


async def spot_watch_job_v11217(context):
    """1-minute WATCH promotion with bounded patience for the heavy-research slot."""
    if not list(base.core.subscribers()):
        return
    acquired = False
    try:
        # V11.21.6 skipped immediately when Futures owned the slot. That could
        # starve Spot promotion. Wait up to 45s, then defer safely to next tick.
        await asyncio.wait_for(runtime._v11205_research_gate.acquire(), timeout=45.0)
        acquired = True
        health = await base.health_check(force=False)
        base._last_health = health
        if bool(getattr(health, "hard_pause", False)) or str(getattr(health, "status", "")).upper() == "PAUSE":
            return
        if base.core._scan_lock.locked():
            return
        return await _original_spot_watch_job(context)
    except asyncio.TimeoutError:
        base.core.log.info("V11.21.7 Spot WATCH deferred: research slot busy")
        return
    except Exception:
        base.core.log.exception("V11.21.7 Spot WATCH cycle failed")
        return
    finally:
        if acquired and runtime._v11205_research_gate.locked():
            runtime._v11205_research_gate.release()


base.spot_auto_job = spot_auto_job_v11217
base.spot_watch_job = spot_watch_job_v11217


# AUTO subscription drives BOTH Futures and Spot; make that explicit in Telegram.
_original_alerts_on = base.core.alerts_on

async def alerts_on_v11217(update, context):
    await _original_alerts_on(update, context)
    try:
        await update.effective_message.reply_text(
            "🟢 <b>SPOT AUTO ТОЖЕ ВКЛЮЧЁН</b>\n"
            "Spot discovery: <b>каждые 15 минут</b> · WATCH: <b>каждую 1 минуту</b>.\n"
            "Spot приходит только как отдельный <b>BUY NOW</b> после устойчивого 2/2 подтверждения; WATCH/READY не являются входом.",
            parse_mode=base.ParseMode.HTML,
            reply_markup=base.main_menu(),
        )
    except Exception:
        base.core.log.exception("V11.21.7 Spot AUTO confirmation message failed")

base.core.alerts_on = alerts_on_v11217


# post_init resolves bot_v11180 module globals at execution time; the patched
# intervals/jobs above are therefore used by inherited repeating jobs. Add an
# early bootstrap so a fresh deploy does not wait 3 minutes for first Spot scan.
_original_post_init = base.core.post_init

async def post_init_v11217(application):
    await _original_post_init(application)
    application.job_queue.run_once(
        spot_auto_job_v11217, when=75, name="v11217-spot-auto-bootstrap"
    )

base.core.post_init = post_init_v11217


# Health card version sync (health logic itself is unchanged).
_original_health_text = base.health_text

def health_text_v11217(h):
    return _original_health_text(h).replace("V11.21.6", "V11.21.7")

base.health_text = health_text_v11217


def install():
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
