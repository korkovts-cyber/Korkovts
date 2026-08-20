"""V11.22.0 · Deep audit hotfix.

Two additional reachability bugs found after reviewing the complete Spot state
machine, not just the strategy function:

A) The V11.21 BUY-READY lane itself accepts score >=78 and RS >=70 after all of
its no-hard-conflict checks. V11.21.9 still used 80/75 in NEUTRAL downstream,
so part of the same BUY-READY lane was still self-cancelling. V11.22.0 makes
the downstream threshold exactly match the lane that already produced BUY.
Independent evidence penalties still apply afterwards.

B) A broad 15-minute Spot scan is a lower-fidelity discovery pass. If it returned
WATCH for a symbol that the 1-minute local-L2 watchtower had already confirmed
READY 1/2, legacy upsert reset confirm_streak to zero. This could erase a valid
first confirmation minutes before the second one. V11.22.0 preserves only the
existing 1/2 proof when a broad WATCH refresh is materially the same geometry.
A fresh watchtower revalidation that itself returns WATCH still calls
reset_spot_ready and clears the streak immediately.

No signal is fabricated and no hard trading safety gate is removed.
"""
from __future__ import annotations

import math

import bot_v11191 as runtime
import v11191_spot_engine as spot_engine
import spot_strategy
import spot_scanner
import spot_watch

base = runtime.base
VERSION = "11.22.0"
_stats = {
    "broad_watch_ready_preserved": 0,
    "threshold_alignments": 0,
}


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


# ---------------------------------------------------------------------------
# 1) Finish the BUY-READY threshold alignment.
# ---------------------------------------------------------------------------
_prev_analyze = spot_engine.analyze


def analyze_v11220(*args, **kwargs):
    signal = _prev_analyze(*args, **kwargs)
    if signal is None:
        return None

    snap = dict(getattr(signal, "feature_snapshot", {}) or {})
    relief = dict(snap.get("spot_v11210") or {})
    if str(getattr(signal, "status", "")).upper() != "BUY" or not relief.get("buy_ready_relief"):
        return signal

    # The lane that created BUY already required score>=78, RP>=70, exact zone,
    # healthy execution, non-opposing flow and no hard market/news/crowding veto.
    # Downstream evidence may still penalize/reject it; do not re-impose the old
    # 82/86 and 75/85 floors here.
    before_score = _f(snap.get("required_score"), 999.0)
    before_rp = _f(snap.get("required_relative_percentile"), 999.0)
    snap["required_score"] = min(before_score, 78.0)
    snap["required_relative_percentile"] = min(before_rp, 70.0)
    snap.setdefault("spot_v11220", {}).update({
        "buy_ready_lane_fully_aligned": True,
        "previous_required_score": before_score,
        "effective_required_score": snap["required_score"],
        "previous_required_relative_percentile": before_rp,
        "effective_required_relative_percentile": snap["required_relative_percentile"],
        "evidence_still_mandatory": True,
    })
    signal.feature_snapshot = snap
    _stats["threshold_alignments"] += 1
    return signal


# Rebind every global reference used by broad scan and persistent WATCH recheck.
spot_engine.analyze = analyze_v11220
spot_engine.legacy.analyze = analyze_v11220
spot_strategy.analyze = analyze_v11220
spot_scanner.analyze = analyze_v11220


# ---------------------------------------------------------------------------
# 2) Broad WATCH must not erase a valid local-L2 READY 1/2 proof.
# ---------------------------------------------------------------------------
_prev_upsert = base.upsert_spot_watch


def _materially_same_geometry(previous, signal):
    if not previous or signal is None:
        return False
    old_lo = _f(previous.get("entry_low"))
    old_hi = _f(previous.get("entry_high"))
    old_inv = _f(previous.get("invalidation"))
    new_lo = _f(getattr(signal, "entry_low", 0))
    new_hi = _f(getattr(signal, "entry_high", 0))
    new_inv = _f(getattr(signal, "invalidation", 0))
    if min(old_lo, old_hi, old_inv, new_lo, new_hi, new_inv) <= 0:
        return False
    if not (old_inv < old_lo <= old_hi and new_inv < new_lo <= new_hi):
        return False

    old_mid = (old_lo + old_hi) / 2.0
    new_mid = (new_lo + new_hi) / 2.0
    old_width = max(old_hi - old_lo, old_mid * 0.0005)
    new_width = max(new_hi - new_lo, new_mid * 0.0005)
    overlap = max(0.0, min(old_hi, new_hi) - max(old_lo, new_lo))
    overlap_ratio = overlap / max(min(old_width, new_width), 1e-12)

    same_setup = (
        str(previous.get("setup_type") or "")
        == str(getattr(signal, "setup_type", "") or "")
    )
    center_close = abs(new_mid - old_mid) <= max(old_width * 0.65, old_mid * 0.0025)
    stop_close = abs(new_inv - old_inv) <= max(old_width * 1.0, old_mid * 0.0040)
    width_ok = 0.55 <= (new_width / max(old_width, 1e-12)) <= 1.65

    # A small setup-label flip (e.g. controlled -> compression continuation)
    # may be tolerated only if the actual price corridor strongly overlaps.
    shape_ok = same_setup or overlap_ratio >= 0.60
    return bool(shape_ok and center_close and stop_close and width_ok)


def upsert_v11220(signal, ttl_hours=36):
    symbol = str(getattr(signal, "symbol", "") or "").upper()
    incoming = str(getattr(signal, "status", "") or "").upper()
    before = base.get_spot_watch(symbol) if symbol else None

    preserve_broad_ready = bool(
        incoming == "WATCH"
        and before
        and str(before.get("status") or "").upper() == "ACTIVE"
        and int(before.get("confirm_streak") or 0) == 1
        and str(before.get("candidate_state") or "").upper() == "READY_PENDING"
        and _materially_same_geometry(before, signal)
    )

    watch_id = _prev_upsert(signal, ttl_hours=ttl_hours)
    if not preserve_broad_ready or watch_id is None:
        return watch_id

    # Restore exactly the prior 1/2 proof. Never create or preserve 2/2 here.
    # A true watchtower WATCH result does not use this path: it calls
    # reset_spot_ready() and therefore still clears the proof.
    with spot_watch._db() as c:
        c.execute(
            """UPDATE spot_watchlist
               SET candidate_state='READY_PENDING',
                   confirm_streak=1,
                   last_ready_at=?,
                   ready_score=?,
                   last_reason='broad WATCH refresh; local-L2 READY 1/2 preserved'
               WHERE symbol=? AND status='ACTIVE' AND release_key=?""",
            (
                before.get("last_ready_at"),
                before.get("ready_score"),
                symbol,
                spot_watch.SPOT_RELEASE_KEY,
            ),
        )
    _stats["broad_watch_ready_preserved"] += 1
    return watch_id


spot_watch.upsert = upsert_v11220
base.upsert_spot_watch = upsert_v11220


# ---------------------------------------------------------------------------
# Diagnostics.
# ---------------------------------------------------------------------------
_prev_heartbeat = base.heartbeat_text


def heartbeat_text_v11220(diagnostics, **kwargs):
    text = _prev_heartbeat(diagnostics, **kwargs)
    try:
        text += (
            f"\n🧠 Spot state audit: preserved READY "
            f"<b>{int(_stats['broad_watch_ready_preserved'])}</b> · "
            f"threshold aligned <b>{int(_stats['threshold_alignments'])}</b>"
        )
    except Exception:
        pass
    return text


base.heartbeat_text = heartbeat_text_v11220

_prev_health = base.health_text


def health_text_v11220(h):
    return (
        _prev_health(h)
        .replace("V11.21.9", "V11.22.0")
        .replace("V11.21.8", "V11.22.0")
        .replace("V11.21.7", "V11.22.0")
        .replace("V11.21.6", "V11.22.0")
    )


base.health_text = health_text_v11220


def install():
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
