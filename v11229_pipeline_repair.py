"""V11.22.9 · Deep execution + freshness consistency repair.

Live faults addressed:
1) API-safe pacing made a healthy derivatives snapshot take ~14–15s while the
   inherited V11.18 freshness gate rejected 1H snapshots after 12s.
2) Deep concurrency 3 + futures-data 1.0 req/s caused candidates to compete for
   the same endpoint queue and some hit the 90s execution deadline.
3) PRIME remained practically unreachable even after an otherwise strong setup
   survived Production.

Safety preserved:
- incomplete derivatives snapshots remain rejected;
- snapshots beyond the new bounded age remain rejected;
- hard evidence conflicts, ADL, execution, liquidity, news and risk gates stay;
- AUTO/STRONG eligibility is not loosened by the PRIME rebalance.
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
from collections import Counter
from dataclasses import replace

import bot_v11191 as runtime
import app.scanner as app_scanner
import v11191_futures_engine as futures
import v11224_full_deep_repair as deep224
import v11226_stable_deep_engine as deep226
import v11227_stability_core as deep227
import v11150_strong as strong

base = runtime.base
VERSION = "11.22.9"

# ---------------------------------------------------------------------------
# 1) Scheduler/deep throughput consistency.
# ---------------------------------------------------------------------------
# At 1.0 req/s and concurrency=3, four /futures/data endpoints interleave across
# candidates and a single candidate can accumulate 14–15s acquisition duration.
# Two deep workers reduce queue contention; 1.25 req/s remains conservative.
deep227.FUTURES_DATA_RPS = 1.25
futures.DEEP_CONCURRENCY = 2
deep226.FUTURES_DATA_RPS = 1.25

# Keep enough total scan budget for 20 full-deep candidates without allowing an
# endpoint stall to be mislabeled as a strategy rejection.
futures.FULL_SCAN_BUDGET_SEC = max(
    600, int(getattr(futures, "FULL_SCAN_BUDGET_SEC", 600) or 600)
)

_snapshot = deep226.derivatives_snapshot_v11226

def _reset_current_deep():
    deep224._deep_stats["runs"] = 0
    deep224._deep_stats["ok"] = 0
    deep224._deep_stats["timeouts"] = 0
    deep224._deep_stats["errors"] = 0
    deep224._deep_stats["reasons"] = Counter()
    deep224._deep_stats["last_ms"] = []


async def deep_one_v11229(row, kind, market_context, news, adl_risks, min_score, sem):
    symbol, lower, base_frame, higher, soft_l, soft_s = row
    deep224._deep_stats["runs"] += 1

    # Queue time stays outside the candidate execution timer.
    async with sem:
        started = time.monotonic()
        try:
            adl = adl_risks.get(symbol) if isinstance(adl_risks, dict) else None

            # 150s is a deadlock guard, not a freshness rule. Freshness is
            # validated independently below by the strategy compatibility gate.
            d = await asyncio.wait_for(_snapshot(symbol, adl), timeout=150.0)

            if not isinstance(d, dict):
                raise RuntimeError("invalid derivatives snapshot")
            if not d.get("deep_data"):
                deep224._deep_stats["reasons"]["DERIVATIVES_INCOMPLETE"] += 1
                return None, "DERIVATIVES_INCOMPLETE", d

            oi_notional = futures._f(d.get("open_interest")) * futures._f(d.get("mark_price"))
            d.update(futures.liquidation_snapshot(symbol, oi_notional))

            timeframe = "1H" if kind == "main" else "15M"
            audit = {}
            result = futures.legacy.analyze(
                symbol, timeframe, base_frame, higher, float(min_score), lower,
                market_context.get("bias"), d,
                futures.legacy.for_symbol(news, symbol), market_context,
                audit=audit,
            )

            if result is None:
                inferred = "LONG" if float(soft_l) >= float(soft_s) else "SHORT"
                result = futures._momentum_fallback(
                    symbol, timeframe, base_frame, higher, lower, inferred, d,
                    market_context, futures.legacy.for_symbol(news, symbol), min_score,
                )
                if result is None:
                    d["_strategy_audit"] = audit
                    deep224._deep_stats["reasons"]["FINAL_STRATEGY_REJECT"] += 1
                    return None, "FINAL_STRATEGY_REJECT", d
                d["_strategy_audit"] = audit
                d["_v11210_fallback"] = True

            side = str(getattr(result, "side", "") or "").upper()
            hist = max(0.0, float(futures.calibration_penalty(symbol, side, timeframe) or 0))
            result.feature_snapshot.setdefault("v11212_cohort_isolation", {}).update({
                "historical_calibration_penalty": hist,
                "calibration_shadow_only": True,
            })
            if kind != "main":
                result.expected_window = "30 минут–4 часа"

            elapsed = (time.monotonic() - started) * 1000.0
            d["_v11229_deep_ms"] = round(elapsed, 1)
            deep224._deep_stats["ok"] += 1
            deep224._deep_stats["reasons"]["PASS"] += 1
            deep224._deep_stats["last_ms"].append((symbol, round(elapsed)))
            deep224._deep_stats["last_ms"] = deep224._deep_stats["last_ms"][-20:]
            return result, "", d

        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - started) * 1000.0
            deep224._deep_stats["timeouts"] += 1
            deep224._deep_stats["reasons"]["DEEP_EXECUTION_TIMEOUT"] += 1
            return None, "DEEP_EXECUTION_TIMEOUT", {
                "_error": "deep execution exceeded 150s after slot acquisition",
                "_v11229_deep_ms": round(elapsed, 1),
            }
        except Exception as exc:
            elapsed = (time.monotonic() - started) * 1000.0
            key = f"ERROR:{type(exc).__name__}"
            deep224._deep_stats["errors"] += 1
            deep224._deep_stats["reasons"][key] += 1
            return None, key, {
                "_error": str(exc),
                "_v11229_deep_ms": round(elapsed, 1),
            }


# ---------------------------------------------------------------------------
# 2) Correct the acquisition-duration/freshness contradiction.
# ---------------------------------------------------------------------------
# Current Production wrapper rejects:
#   15M > 8s, 1H > 12s
# But acquisition duration includes intentional API queue/pacing time and is not
# equivalent to market-data age. We retain a bounded guard while allowing a
# complete snapshot produced by the conservative scheduler.
_old_analyze = futures.legacy.analyze

NEW_MAX_ACQUIRE_MS = {
    "15M": 18_000.0,
    "1H": 25_000.0,
}
OLD_GATE_MS = {
    "15M": 8_000.0,
    "1H": 12_000.0,
}


def analyze_v11229(*args, **kwargs):
    # app.strategy positional signature:
    # symbol,timeframe,df,higher,min_score,lower,market_bias,derivatives,...
    timeframe = kwargs.get("timeframe")
    if timeframe is None and len(args) > 1:
        timeframe = args[1]
    tf = str(timeframe or "1H").upper()

    derivatives = kwargs.get("derivatives")
    positional = False
    d_index = 7
    if derivatives is None and len(args) > d_index:
        derivatives = args[d_index]
        positional = True

    if not isinstance(derivatives, dict):
        return _old_analyze(*args, **kwargs)

    original_ms = float(derivatives.get("v1142_acquire_duration_ms", 0) or 0)
    new_limit = NEW_MAX_ACQUIRE_MS.get(tf, 25_000.0)

    # Never rescue incomplete data. This adapter only corrects the clock
    # semantics for an otherwise complete snapshot.
    complete = bool(derivatives.get("deep_data"))
    quality = int(derivatives.get("data_quality", 0) or 0)
    adl_ok = bool(derivatives.get("adl_fresh", False))
    can_reclassify = (
        original_ms > OLD_GATE_MS.get(tf, 12_000.0)
        and original_ms <= new_limit
        and complete
        and quality >= 8
        and adl_ok
    )

    if not can_reclassify:
        return _old_analyze(*args, **kwargs)

    d = copy.deepcopy(derivatives)
    d["v11229_original_acquire_duration_ms"] = original_ms
    d["v11229_freshness_limit_ms"] = new_limit
    d["v11229_scheduler_adjusted_freshness"] = True

    # Feed the legacy wrapper a value just inside its old threshold; original
    # duration is preserved separately and attached to the resulting signal.
    d["v1142_acquire_duration_ms"] = OLD_GATE_MS.get(tf, 12_000.0) - 1.0

    a = list(args)
    k = dict(kwargs)
    if positional:
        a[d_index] = d
    else:
        k["derivatives"] = d

    result = _old_analyze(*a, **k)
    if result is not None:
        result.feature_snapshot.setdefault("data_freshness_v11229", {}).update({
            "original_derivatives_acquire_ms": original_ms,
            "effective_limit_ms": new_limit,
            "scheduler_adjusted": True,
            "deep_data": complete,
            "data_quality": quality,
            "adl_fresh": adl_ok,
        })
    return result


# ---------------------------------------------------------------------------
# 3) PRIME: rare but reachable. STRONG/AUTO gate remains authoritative.
# ---------------------------------------------------------------------------
_original_strong_assess = strong.assess


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def assess_strong_v11229(signal):
    a = _original_strong_assess(signal)
    if a.prime_eligible:
        return a
    if not a.auto_eligible or a.blockers:
        return a

    snap = dict(getattr(signal, "feature_snapshot", {}) or {})
    evidence = dict(snap.get("evidence_v117") or {})
    stability = dict(snap.get("selection_stability_v11100") or {})
    margin = dict(snap.get("decision_margin_v11140") or {})

    rank = _f(getattr(signal, "professional_rank", 0))
    support = int(evidence.get("support", getattr(signal, "evidence_support", 0)) or 0)
    conflicts = int(evidence.get("conflict", getattr(signal, "evidence_conflicts", 0)) or 0)
    hard = list(evidence.get("hard_conflicts") or [])
    selection = str(stability.get("label") or getattr(signal, "selection_stability_label", "") or "").upper()
    margin_label = str(margin.get("label") or getattr(signal, "decision_margin_label", "") or "").upper()

    pack = {"available": False, "unique_support": 0, "unique_conflicts": 0, "refinement_conflicts": 0}
    try:
        fn = getattr(strong, "indicator_edge_assessment", None)
        if fn is not None:
            pack = dict(fn(signal) or pack)
    except Exception:
        pass

    available = bool(pack.get("available"))
    ps = int(pack.get("unique_support", 0) or 0)
    pc = int(pack.get("unique_conflicts", 0) or 0)
    rc = int(pack.get("refinement_conflicts", 0) or 0)

    prime = (
        rank >= 86.0
        and support >= 5
        and conflicts <= 1
        and not hard
        and float(a.score) >= 65.0
        and selection != "FRAGILE"
        and margin_label != "NEAR_TIE"
        and (not available or (ps >= 2 and pc <= 1 and rc <= 1))
    )
    if not prime:
        return a
    return replace(
        a,
        label="PRIME_STRONG",
        prime_eligible=True,
        reasons=tuple(a.reasons) + ("V11.22.9 PRIME consensus",),
    )


def annotate_strong_v11229(signal):
    a = assess_strong_v11229(signal)
    signal.strong_signal_label = a.label
    signal.strong_signal_score = a.score
    signal.strong_auto_eligible = a.auto_eligible
    signal.strong_prime_eligible = a.prime_eligible
    signal.feature_snapshot.setdefault("strong_consensus_v11150", {}).update({
        "schema": "11.22.9-prime-rebalance-v1",
        "label": a.label,
        "score": a.score,
        "auto_eligible": a.auto_eligible,
        "prime_eligible": a.prime_eligible,
        "reasons": list(a.reasons),
        "blockers": list(a.blockers),
        "negative_only": True,
        "professional_rank_changed": False,
    })
    return signal


def annotate_many_v11229(rows):
    return [annotate_strong_v11229(x) for x in (rows or [])]


# ---------------------------------------------------------------------------
# 4) Diagnostics.
# ---------------------------------------------------------------------------
_old_hb = base.heartbeat_text


def heartbeat_v11229(diagnostics, **kwargs):
    text = _old_hb(diagnostics, **kwargs)
    try:
        d = dict(diagnostics or {})
        rej = dict(d.get("rejections") or {})
        text += (
            f"\n🛠 V11.22.9 pipeline: deep concurrency <b>{futures.DEEP_CONCURRENCY}</b>"
            f" · futures-data <b>{deep227.FUTURES_DATA_RPS:.2f} req/s</b>"
            f" · exec-timeout <b>150s</b>"
            f"\n⏱ Freshness caps: 15M <b>18s</b> · 1H <b>25s</b>"
            f" · current deep timeouts <b>{int(rej.get('DEEP_EXECUTION_TIMEOUT',0) or 0)}</b>"
        )
    except Exception:
        pass
    return text


def install():
    # Deep/scheduler.
    futures.DEEP_CONCURRENCY = 2
    deep227.FUTURES_DATA_RPS = 1.25
    deep226.FUTURES_DATA_RPS = 1.25
    futures._deep_one = deep_one_v11229

    # Freshness wrapper aliases used by the raw Futures engine.
    futures.legacy.analyze = analyze_v11229
    app_scanner.analyze = analyze_v11229
    base.core.analyze = analyze_v11229

    # PRIME aliases used later in Production.
    strong.assess = assess_strong_v11229
    strong.annotate = annotate_strong_v11229
    strong.annotate_many = annotate_many_v11229
    base.assess_strong_signal = assess_strong_v11229
    base.annotate_strong_signal = annotate_strong_v11229
    base.annotate_strong_signals = annotate_many_v11229

    base.heartbeat_text = heartbeat_v11229
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
