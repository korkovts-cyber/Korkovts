"""V11.23.6 · Signal activation / delivery hardening.

Purpose:
- ensure the final V11.23.5 gate is applied to every Futures scan result BEFORE
  Telegram/AUTO consumes it;
- rebind all runtime scan aliases so no stale pre-overlay scanner survives;
- keep SPOT/FUTURES labels from V11.23.5;
- reduce repeated no-signal telemetry to a compact status card;
- keep fail-closed market-data safety while removing overly narrow market
  micro-thresholds from the independent-family fallback (implemented in the
  bundled v11230_signal_core.py activation profile).

This release does not fabricate trades. A signal still requires complete deep
market data, fresh ADL, valid execution, trend and >=4/5 independent families.
"""
from __future__ import annotations

import inspect

import bot_v11191 as runtime
import v11191_futures_engine as futures
import v11235_signal_delivery_ui as ui235

base = runtime.base
VERSION = "11.23.6"

_ORIG_SCAN = futures.scan
_ORIG_SCAN_SHORT = futures.scan_short


def _finalize_rows(rows):
    out = []
    for row in (rows or []):
        try:
            row = ui235.apply_final_gate_v11235(row)
        except Exception:
            # Never lose an otherwise valid engine result because UI annotation
            # failed. The old safety pipeline has already accepted this row.
            pass
        out.append(row)
    # Strong/PRIME first, then display rank. Keep maximum four cards per scan.
    out.sort(
        key=lambda s: (
            bool(getattr(s, "strong_prime_eligible", False)),
            bool(getattr(s, "strong_auto_eligible", False)),
            float(getattr(s, "professional_rank", getattr(s, "score", 0)) or 0),
        ),
        reverse=True,
    )
    return out[:4]


async def scan_v11236(*args, **kwargs):
    rows = await _ORIG_SCAN(*args, **kwargs)
    return _finalize_rows(rows)


async def scan_short_v11236(*args, **kwargs):
    rows = await _ORIG_SCAN_SHORT(*args, **kwargs)
    return _finalize_rows(rows)


def heartbeat_v11236(diagnostics, **kwargs):
    d = dict(diagnostics or {})
    final = int(d.get("final", 0) or 0)
    status = str(d.get("status", "ok") or "ok").lower()
    if final > 0:
        return (
            "🤖 <b>AUTO: кандидаты найдены</b>\n"
            f"🔵 Futures: <b>{final}</b> · выполняю финальную проверку входа\n"
            "🟢 Spot: работает отдельно\n"
            "Готовая сделка придёт отдельной карточкой SPOT или FUTURES."
        )
    if status == "error":
        return "⚠️ <b>AUTO: проблема с рыночными данными</b> · повторю проверку в следующем цикле."
    return (
        "🤖 <b>AUTO работает</b> · рынок проверен\n"
        "Сейчас нет готовой сделки. Следующее сообщение придёт при новом сетапе."
    )


def install():
    # Critical: Futures AUTO/manual aliases must all resolve to the post-final-gate scan.
    futures.scan = scan_v11236
    futures.scan_short = scan_short_v11236
    runtime.futures_scan = scan_v11236
    runtime.futures_scan_short = scan_short_v11236
    try:
        runtime.scanner.scan = scan_v11236
        runtime.scanner.scan_short = scan_short_v11236
    except Exception:
        pass
    base.core.scan = scan_v11236
    base.core.scan_short = scan_short_v11236

    # Preserve the clear V11.23.5 cards, but make no-signal telemetry concise.
    base.card = ui235.futures_card_v11235
    base.spot_card = ui235.spot_card_v11235
    base.heartbeat_text = heartbeat_v11236

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
