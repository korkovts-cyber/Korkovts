"""V11.23.9 · Full-deep reachability repair.

The cheap 36-name derivatives screen is RANKING ONLY. A temporary OI/premium
screen failure must never turn 36 valid technical candidates into 0 full-deep
checks. This layer degrades gracefully: missing screen rows are ranked from the
already-computed technical soft LONG/SHORT scores and still go through the
complete expensive derivatives snapshot + Production/Alpha/execution gates.

No signal can be emitted from this fallback itself.
"""
from __future__ import annotations

import bot_v11191 as runtime
import v11191_futures_engine as futures

base = runtime.base
core = base.core
VERSION = "11.23.9"

_ORIG_SCREEN = futures.quick_deep_screen


def _soft_meta(row):
    symbol, _lower, _base, _higher, soft_l, soft_s = row
    long_side = float(soft_l) >= float(soft_s)
    soft = max(float(soft_l), float(soft_s))
    return {
        "symbol": str(symbol),
        "side": "LONG" if long_side else "SHORT",
        "score": float(soft),
        "soft": float(soft),
        "funding_pct": 0.0,
        "open_interest": 0.0,
        "oi_notional": 0.0,
        "ranking_fallback": True,
    }


async def screen_v11239(rows, tickers):
    rows = list(rows or [])
    if not rows:
        return [], {"status":"EMPTY","requested":0,"complete":0,"coverage":0.0}

    try:
        ranked, diag = await _ORIG_SCREEN(rows, tickers)
    except Exception as exc:
        ranked, diag = [], {
            "status":"DEGRADED_RANKING_FALLBACK",
            "requested":len(rows), "complete":0, "coverage":0.0,
            "screen_error":f"{type(exc).__name__}: {exc}",
        }

    ranked = list(ranked or [])
    diag = dict(diag or {})
    present = {str(row[1][0]) for row in ranked if row and len(row) >= 2}

    # The low-cost screen is not an eligibility gate. Fill every missing row
    # from the broad technical rank so the expensive production snapshot can
    # make the real decision.
    fallback_count = 0
    for row in rows:
        symbol = str(row[0])
        if symbol in present:
            continue
        ranked.append((_soft_meta(row), row))
        present.add(symbol)
        fallback_count += 1

    ranked.sort(key=lambda x: float((x[0] or {}).get("score", 0) or 0), reverse=True)
    diag.update({
        "status": "OK" if fallback_count == 0 else "DEGRADED_RANKING_FALLBACK",
        "requested": len(rows),
        "complete": len(ranked),
        # Effective ranking coverage is complete after safe technical fallback.
        "coverage": 1.0,
        "raw_screen_complete": max(0, len(ranked) - fallback_count),
        "ranking_fallback_count": fallback_count,
        "ranking_fallback_safe": True,
        "fallback_note": "cheap screen ranks only; full-deep remains mandatory",
    })
    return ranked, diag


def install():
    # _run() resolves this module global at execution time.
    futures.quick_deep_screen = screen_v11239
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    core.APP_VERSION = VERSION
    return True
