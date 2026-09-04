"""V11.23.3 · Coherent entry/stop geometry.

Fixes the final bottleneck observed live:
high-quality candidates reached final analysis but were rejected because the
legacy strategy selected the farther of ATR and structural stops, then rejected
the same trade for excessive ATR risk.

This layer runs LAST and repairs only otherwise valid signals:
- never widens risk;
- never invents a stop on the wrong side of entry;
- keeps a minimum noise buffer;
- caps risk in ATR terms;
- re-derives TP1/2/3 from the corrected risk;
- candidates that cannot be repaired remain rejected.
"""
from __future__ import annotations

import copy
import math

import bot_v11191 as runtime
import app.scanner as app_scanner
from app.indicators import enrich
import v11191_futures_engine as futures

base = runtime.base
VERSION = "11.23.3"
_ACTIVE_ANALYZE = None

MIN_RISK_ATR = 0.90
TARGET_RISK_ATR = 1.35
MAX_RISK_ATR = 1.85


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _geometry(signal, df):
    """Return repaired signal or None.

    We use the nearer valid protective level, but never tighter than 0.90 ATR.
    This removes the legacy farther-stop contradiction without creating
    unrealistic tiny stops.
    """
    x = enrich(df)
    if len(x) < 50:
        return None
    a = x.iloc[-1]
    atr = _f(a.atr)
    ema20 = _f(a.ema20)
    if atr <= 0 or ema20 <= 0:
        return None

    side = str(signal.side).upper()
    entry = _f(signal.entry_high if side == "LONG" else signal.entry_low)
    old_stop = _f(signal.stop)
    if entry <= 0 or old_stop <= 0:
        return None

    min_risk = atr * MIN_RISK_ATR
    target = atr * TARGET_RISK_ATR
    max_risk = atr * MAX_RISK_ATR

    if side == "LONG":
        # Structure below EMA20, but if it is very far away use an ATR stop.
        structural = ema20 - atr * 0.20
        atr_stop = entry - target

        # nearest valid stop below entry = higher price; never above the
        # minimum-noise stop.
        candidate = max(structural, atr_stop)
        stop = min(candidate, entry - min_risk)

        risk = entry - stop
        if not (min_risk <= risk <= max_risk):
            return None
        if stop <= 0 or stop >= entry:
            return None

        signal.stop = stop
        signal.tp1 = entry + risk
        signal.tp2 = entry + 2.0 * risk
        signal.tp3 = entry + 3.0 * risk
    elif side == "SHORT":
        structural = ema20 + atr * 0.20
        atr_stop = entry + target

        # nearest valid stop above entry = lower price.
        candidate = min(structural, atr_stop)
        stop = max(candidate, entry + min_risk)

        risk = stop - entry
        if not (min_risk <= risk <= max_risk):
            return None
        if stop <= entry:
            return None

        signal.stop = stop
        signal.tp1 = entry - risk
        signal.tp2 = entry - 2.0 * risk
        signal.tp3 = entry - 3.0 * risk
    else:
        return None

    if min(signal.tp1, signal.tp2, signal.tp3) <= 0:
        return None

    signal.rr = 2.0
    snap = signal.feature_snapshot.setdefault("geometry_v11233", {})
    snap.update({
        "old_stop": old_stop,
        "new_stop": float(signal.stop),
        "risk_atr": float(risk / atr),
        "min_risk_atr": MIN_RISK_ATR,
        "target_risk_atr": TARGET_RISK_ATR,
        "max_risk_atr": MAX_RISK_ATR,
        "method": "nearest-valid-structure-with-atr-noise-floor",
        "risk_widened": False,
    })
    return signal


def analyze_v11233(*args, **kwargs):
    if _ACTIVE_ANALYZE is None:
        raise RuntimeError("V11.23.3 analyzer not installed")

    # Let the entire V11.23.2 / family core run normally first.
    result = _ACTIVE_ANALYZE(*args, **kwargs)
    if result is not None:
        # Existing pass: normalize geometry only when the stop is outside the
        # coherent risk band. Good existing geometry remains untouched.
        df = kwargs.get("df")
        if df is None and len(args) > 2:
            df = args[2]
        if df is None:
            return result
        try:
            x = enrich(df)
            atr = _f(x.iloc[-1].atr)
            entry = _f(result.entry_high if str(result.side).upper()=="LONG" else result.entry_low)
            risk = abs(entry - _f(result.stop))
            if atr > 0 and MIN_RISK_ATR <= risk/atr <= MAX_RISK_ATR:
                return result
            repaired = _geometry(copy.deepcopy(result), df)
            return repaired or result
        except Exception:
            return result

    # If the active analyzer returned None, attempt the independent-family
    # fallback directly. This is important because legacy geometry may reject
    # before the family core can produce a final Signal in some alias paths.
    try:
        import v11230_signal_core as family
        vals = list(args)
        names = [
            "symbol","timeframe","df","higher","min_score","lower",
            "market_bias","derivatives","news","market_context","audit"
        ]
        p = {}
        for i, name in enumerate(names):
            if i < len(vals):
                p[name] = vals[i]
        p.update(kwargs)

        # Use the family model's raw independent assessment.
        candidate = family._family_fallback(
            p.get("symbol"), p.get("timeframe"), p.get("df"), p.get("higher"),
            p.get("min_score",75), p.get("lower"), p.get("market_bias"),
            p.get("derivatives"), p.get("news"), p.get("market_context"),
            p.get("audit"),
        )
        if candidate is None:
            return None

        repaired = _geometry(candidate, p.get("df"))
        if repaired is None:
            if isinstance(p.get("audit"), dict):
                p["audit"].setdefault("issues", []).append(
                    "точка входа слишком далеко от безопасного структурного стопа"
                )
            return None

        repaired.feature_snapshot.setdefault("family_consensus_v11230", {}).update({
            "geometry_repaired_v11233": True,
        })
        return repaired
    except Exception:
        return None


def install():
    global _ACTIVE_ANALYZE
    _ACTIVE_ANALYZE = futures.legacy.analyze

    futures.legacy.analyze = analyze_v11233
    app_scanner.analyze = analyze_v11233
    base.core.analyze = analyze_v11233

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
