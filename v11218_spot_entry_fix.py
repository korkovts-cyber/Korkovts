"""V11.21.8 · Spot BUY-NOW persistence fix.

Fixes a concrete WATCH -> BUY NOW starvation bug:
fresh revalidation recalculates the entry corridor on every pass, while the
persistent watch table compared floating-point geometry with exact equality.
Small legitimate EMA/ATR drift therefore reset confirm_streak to 0 immediately
before record_ready(), making a stable candidate repeatedly become 1/2 and
almost never reach 2/2.

This overlay preserves the prior confirmation only when the rebuilt setup is
materially the same. A WATCH downgrade, invalidation, different setup, large
geometry move, portfolio conflict, bad L2/flow/news/crowding, or failed fresh
revalidation still resets/blocks as before.
"""
from __future__ import annotations

import asyncio
import math

import bot_v11191 as runtime
import spot_watch
import v11217_reliability as reliability

base = runtime.base
VERSION = "11.21.8"


def _f(value, default=0.0):
    try:
        x = float(value)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _same_spot_setup(previous, signal):
    """Material-geometry continuity, never exact float equality."""
    if not previous or signal is None:
        return False
    if str(previous.get("status") or "").upper() != "ACTIVE":
        return False
    if str(previous.get("setup_type") or "") != str(getattr(signal, "setup_type", "") or ""):
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

    # The strategy corridor is roughly 0.55 ATR wide. These limits allow normal
    # closed-candle EMA/ATR drift while rejecting a genuinely rebuilt trade.
    center_tol = max(old_width * 0.80, old_mid * 0.0035)   # <= ~0.35%
    stop_tol = max(old_width * 1.25, old_mid * 0.0050)    # <= ~0.50%
    width_ratio = new_width / max(old_width, 1e-12)

    return bool(
        abs(new_mid - old_mid) <= center_tol
        and abs(new_inv - old_inv) <= stop_tol
        and 0.50 <= width_ratio <= 1.80
    )


_original_upsert = base.upsert_spot_watch


def upsert_spot_watch_v11218(signal, ttl_hours=36):
    """Preserve READY 1/2 across only a materially identical BUY rebuild."""
    symbol = str(getattr(signal, "symbol", "") or "").upper()
    incoming = str(getattr(signal, "status", "") or "").upper()
    before = base.get_spot_watch(symbol) if symbol else None
    preserve = bool(
        incoming == "BUY"
        and before
        and int(before.get("confirm_streak") or 0) > 0
        and _same_spot_setup(before, signal)
    )

    watch_id = _original_upsert(signal, ttl_hours=ttl_hours)
    if not preserve or watch_id is None:
        return watch_id

    # The legacy SQL may have reset the streak because one rounded price differs
    # by one tick. Restore only the earlier proof; never increase it here.
    prior_streak = min(2, int(before.get("confirm_streak") or 0))
    with spot_watch._db() as c:
        c.execute(
            """UPDATE spot_watchlist
               SET candidate_state='READY_PENDING',
                   confirm_streak=?,
                   last_ready_at=?,
                   ready_score=COALESCE(?,ready_score),
                   last_reason='materially same BUY geometry; prior confirmation preserved'
               WHERE symbol=? AND status='ACTIVE' AND release_key=?""",
            (
                prior_streak,
                before.get("last_ready_at"),
                before.get("ready_score"),
                symbol,
                spot_watch.SPOT_RELEASE_KEY,
            ),
        )
    return watch_id


# Patch both the module and the by-value import used by bot_v11180.
spot_watch.upsert = upsert_spot_watch_v11218
base.upsert_spot_watch = upsert_spot_watch_v11218


def _near_original_zone(row, ask):
    """Cheap precheck only; fresh strategy still requires its exact current zone."""
    lo = _f(row.get("entry_low"))
    hi = _f(row.get("entry_high"))
    if ask <= 0 or lo <= 0 or hi < lo:
        return False
    width = max(hi - lo, ask * 0.0005)
    pad = max(width * 0.25, ask * 0.0015)
    return (lo - pad) <= ask <= (hi + pad)


async def _spot_watch_core_v11218(context):
    """Original production logic with continuity-safe 2/2 confirmation."""
    chats = list(base.core.subscribers())
    if not chats:
        return

    promoted = 0
    async with base._spot_candidate_lock:
        rows = base.active_spot_watches(10)
        if not rows:
            return

        active_clusters = set(base.spot_active_clusters())
        active_positions = base.spot_reserved_signals(10)
        portfolio_symbols = [
            str(r.get("symbol") or "").upper() for r in active_positions
        ]
        active_count = base.spot_reserved_count()
        if active_count >= 2:
            base.core.log.info("V11.21.8 Spot WATCH portfolio cap active=%s", active_count)
            return

        for row in rows:
            symbol = str(row.get("symbol") or "").upper()

            if base.spot_was_sent_recently(symbol, 72):
                base.close_spot_watch(
                    symbol, "COOLDOWN", "recent delivered/pending BUY already exists"
                )
                continue

            local_book = base.spot_local_book(symbol, 3.0, 50)
            local_health = base.spot_book_stability(symbol, 3.0)
            if local_book is None or not local_health.get("healthy"):
                base.record_spot_watch_check(
                    symbol, None,
                    f"local depth not ready: {local_health.get('reason','unsynchronised')}",
                )
                continue

            ask = float(local_book["asks"][0][0])
            if ask <= float(row.get("invalidation") or 0):
                base.close_spot_watch(
                    symbol, "CANCELLED", "price invalidated before BUY"
                )
                continue

            # V11.21.8: do not refuse a fresh full revalidation because the old
            # corridor moved by a tiny amount. This is only permission to recheck;
            # the rebuilt signal must itself be BUY in its exact fresh zone.
            if not _near_original_zone(row, ask):
                base.reset_spot_ready(
                    symbol, "waiting near original BUY zone", ask
                )
                continue

            cluster = str(row.get("portfolio_cluster") or symbol).upper()
            if cluster in active_clusters:
                base.record_spot_watch_check(
                    symbol, ask, f"portfolio cluster {cluster} already has OPEN Spot BUY"
                )
                continue

            corr_risk = await base.spot_active_correlation_risk(
                symbol, portfolio_symbols
            )
            if corr_risk.get("degraded"):
                base.record_spot_watch_check(
                    symbol, ask, "active-position correlation check unavailable"
                )
                continue
            if corr_risk.get("blocked"):
                base.record_spot_watch_check(
                    symbol, ask,
                    f"corr {float(corr_risk.get('corr',0)):.2f} with "
                    f"{corr_risk.get('with_symbol')} active/pending Spot position",
                )
                continue

            signal, error = await base.spot_recheck_watch(row)
            if signal is None:
                base.reset_spot_ready(
                    symbol, error or "fresh revalidation rejected", ask
                )
                continue
            if str(getattr(signal, "status", "")).upper() != "BUY":
                base.reset_spot_ready(
                    symbol, "fresh full revalidation still WATCH", ask
                )
                continue

            # This upsert now preserves 1/2 when the fresh BUY is materially the
            # same trade instead of resetting on one-tick geometry drift.
            base.upsert_spot_watch(signal)
            streak = base.record_spot_ready(
                symbol, float(signal.score), ask, 60
            )
            if streak < 2:
                base._decorate_spot_entry(signal, "READY_PENDING", streak)
                base.record_spot_watch_check(
                    symbol, ask,
                    f"fresh BUY confirmation {streak}/2; wait for persistence",
                )
                continue

            base._decorate_spot_entry(signal, "BUY_NOW", streak)
            signal_id = base.save_spot_signal(signal, delivered=False)
            base.record_v1180_decision("SPOT", signal_id, signal)
            payload = base.spot_card(signal, True)
            for chat_id in chats:
                base.enqueue_spot_delivery(signal_id, chat_id, payload)

            base.close_spot_watch(
                symbol, "PENDING_DELIVERY",
                "WATCH -> BUY 2/2 passed; Telegram live revalidation pending",
                signal_id,
            )
            active_clusters.add(base._spot_cluster_key(signal))
            portfolio_symbols.append(symbol)
            promoted += 1
            active_count += 1
            if active_count >= 2:
                break

    if promoted:
        delivered = await base._deliver_spot_pending(context.bot)
        base.core.log.info(
            "V11.21.8 Spot WATCH queued=%s delivered=%s", promoted, delivered
        )


async def spot_watch_job_v11218(context):
    """Research-serialized 1-minute Spot promotion."""
    if not list(base.core.subscribers()):
        return

    acquired = False
    try:
        await asyncio.wait_for(
            runtime._v11205_research_gate.acquire(), timeout=45.0
        )
        acquired = True

        health = await base.health_check(force=False)
        base._last_health = health
        if (
            bool(getattr(health, "hard_pause", False))
            or str(getattr(health, "status", "")).upper() == "PAUSE"
        ):
            return

        # The gate prevents a new heavy scan from starting. If an unwrapped
        # legacy scan already owns the underlying lock, defer to next minute.
        if base.core._scan_lock.locked():
            return

        await _spot_watch_core_v11218(context)
    except asyncio.TimeoutError:
        base.core.log.info("V11.21.8 Spot WATCH deferred: research slot busy")
    except Exception:
        base.core.log.exception("V11.21.8 Spot WATCH cycle failed")
    finally:
        if acquired and runtime._v11205_research_gate.locked():
            runtime._v11205_research_gate.release()


base.spot_watch_job = spot_watch_job_v11218


# Add the actual top WATCH state/reason to the 10-minute AUTO heartbeat. This
# makes future starvation diagnosable from Telegram instead of requiring logs.
_original_heartbeat = base.heartbeat_text


def heartbeat_text_v11218(diagnostics, **kwargs):
    text = _original_heartbeat(diagnostics, **kwargs)
    try:
        rows = list(base.active_spot_watches(10))
        if rows:
            top = rows[0]
            reason = str(top.get("last_reason") or "waiting for first WATCH check")
            reason = base.escape(reason[:150])
            text += (
                f"\n🔬 Spot top: <b>{base.escape(str(top.get('symbol','?')))}</b> "
                f"· confirm <b>{int(top.get('confirm_streak') or 0)}/2</b>"
                f"\n└ {reason}"
            )
        else:
            text += "\n🔬 Spot top: <b>нет активных WATCH</b>"
    except Exception:
        pass
    return text


base.heartbeat_text = heartbeat_text_v11218


# Final visible release sync. V11.21.7 owns the Health implementation, but the
# installed runtime is V11.21.8 after this overlay.
_original_health_text_v11218 = base.health_text

def health_text_v11218(h):
    text = _original_health_text_v11218(h)
    return text.replace("V11.21.7", "V11.21.8").replace("V11.21.6", "V11.21.8")

base.health_text = health_text_v11218


def install():
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
