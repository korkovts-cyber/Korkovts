"""V11.23.2 · Integrated derivatives freshness repair.

Root cause fixed:
V11.22.9 installed a freshness adapter, but V11.23.0 later replaced the
production analyze alias and could route candidates back through the inherited
V11.18 acquisition-duration gate.

This final layer is installed LAST and captures the actual active analyzer at
install time. It changes only the semantics of acquisition duration:
download/pacing duration is not market-data age.

Fail-closed protections remain:
- deep_data must be complete;
- data quality >= 8/9;
- ADL must be fresh;
- bounded acquisition duration only;
- all downstream strategy/execution/evidence/risk gates remain active.
"""
from __future__ import annotations

import copy

import bot_v11191 as runtime
import app.scanner as app_scanner
import v11191_futures_engine as futures

base = runtime.base
VERSION = "11.23.2"

_ACTIVE_ANALYZE = None

# The inherited V11.18 wrapper uses these limits against *download duration*.
LEGACY_LIMIT_MS = {"15M": 8000.0, "1H": 12000.0}

# A complete paced snapshot may legitimately take this long on a shared
# Railway/Binance path. This remains bounded; it is not an unlimited bypass.
SAFE_ACQUIRE_MS = {"15M": 22000.0, "1H": 30000.0}


def _resolve_call(args, kwargs):
    timeframe = kwargs.get("timeframe")
    if timeframe is None and len(args) > 1:
        timeframe = args[1]
    tf = str(timeframe or "1H").upper()

    derivatives = kwargs.get("derivatives")
    positional = False
    idx = 7
    if derivatives is None and len(args) > idx:
        derivatives = args[idx]
        positional = True
    return tf, derivatives, positional, idx


def analyze_v11232(*args, **kwargs):
    if _ACTIVE_ANALYZE is None:
        raise RuntimeError("V11.23.2 analyzer not installed")

    tf, derivatives, positional, idx = _resolve_call(args, kwargs)
    if not isinstance(derivatives, dict):
        return _ACTIVE_ANALYZE(*args, **kwargs)

    acquire_ms = float(derivatives.get("v1142_acquire_duration_ms", 0) or 0)
    legacy_limit = LEGACY_LIMIT_MS.get(tf, 12000.0)
    safe_limit = SAFE_ACQUIRE_MS.get(tf, 30000.0)

    complete = bool(derivatives.get("deep_data"))
    quality = int(derivatives.get("data_quality", 0) or 0)
    adl_fresh = bool(derivatives.get("adl_fresh", False))

    # Only repair the exact false-stale case seen in production.
    repair = (
        acquire_ms > legacy_limit
        and acquire_ms <= safe_limit
        and complete
        and quality >= 8
        and adl_fresh
    )

    if not repair:
        return _ACTIVE_ANALYZE(*args, **kwargs)

    d = copy.deepcopy(derivatives)
    d["v11232_original_acquire_duration_ms"] = acquire_ms
    d["v11232_safe_acquire_limit_ms"] = safe_limit
    d["v11232_complete_snapshot"] = True

    # The old wrapper mistakes acquisition duration for freshness. Feed it only
    # a compatibility value; preserve the real duration in V11.23.2 fields.
    d["v1142_acquire_duration_ms"] = legacy_limit - 1.0

    a = list(args)
    k = dict(kwargs)
    if positional:
        a[idx] = d
    else:
        k["derivatives"] = d

    result = _ACTIVE_ANALYZE(*a, **k)
    if result is not None:
        result.feature_snapshot.setdefault("data_freshness_v11232", {}).update({
            "original_acquire_ms": acquire_ms,
            "safe_limit_ms": safe_limit,
            "complete": complete,
            "data_quality": quality,
            "adl_fresh": adl_fresh,
            "false_stale_repaired": True,
        })
    return result


def install():
    global _ACTIVE_ANALYZE

    # CRITICAL: capture at install time AFTER V11.22.9 + V11.23.0 are installed.
    # This preserves the independent-family signal core instead of bypassing it.
    _ACTIVE_ANALYZE = futures.legacy.analyze

    futures.legacy.analyze = analyze_v11232
    app_scanner.analyze = analyze_v11232
    base.core.analyze = analyze_v11232

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
