"""V11.23.4 · Unified final 5-factor gate.

Goal:
No Futures candidate can be presented as STRONG/PRIME solely because the
legacy engine scored it highly. Legacy remains discovery only.

Every final candidate is re-evaluated by independent families:
TREND / MOMENTUM / FLOW / LOCATION / EXECUTION.

Rules:
- PRIME: 5/5 and safe location.
- STRONG: 4/5 minimum, TREND + EXECUTION + LOCATION mandatory.
- If trend/flow are good but LOCATION is not, mark WAIT ENTRY, never STRONG ENTRY.
- Legacy score remains diagnostic only.
"""
from __future__ import annotations

import math

import bot_v11191 as runtime
import v11230_signal_core as family
import v11150_strong as strong

base = runtime.base
VERSION = "11.23.4"


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _families_from_signal(signal):
    snap = dict(getattr(signal, "feature_snapshot", {}) or {})
    fam = dict(snap.get("family_consensus_v11230") or {})
    families = dict(fam.get("families") or {})
    if families:
        return families, fam

    # Legacy-pass path: reconstruct the five independent families from the
    # normalized feature snapshot where possible.
    tech = dict(snap.get("technical") or {})
    der = dict(snap.get("derivatives") or {})
    news = dict(snap.get("news") or {})
    market = dict(snap.get("market") or getattr(signal, "market_context", {}) or {})

    side = str(getattr(signal, "side", "") or "").upper()

    # Trend: if higher-timeframe alignment was explicitly recorded by legacy,
    # treat as trend pass. This is a single family vote, not multiple indicator votes.
    reasons = " | ".join(str(x) for x in (getattr(signal, "reasons", []) or []))
    if side == "LONG":
        trend = (
            "HTF trend aligned" in reasons
            or "старший" in reasons.lower()
            or "EMA и Ichimoku подтверждают тренд" in reasons
        )
    else:
        trend = (
            "HTF trend aligned" in reasons
            or "старший" in reasons.lower()
            or "EMA и Ichimoku подтверждают тренд" in reasons
        )

    adx = _f(tech.get("adx"))
    rsi = _f(tech.get("rsi"), 50)
    macd = _f(tech.get("macd_hist"))
    if side == "LONG":
        momentum = adx >= 18 and rsi <= 72 and macd >= 0
    else:
        momentum = adx >= 18 and rsi >= 28 and macd <= 0

    taker = _f(der.get("taker_ratio"), 1.0)
    oi_change = _f(der.get("oi_change_pct"))
    top_change = _f(der.get("top_position_change_pct"))
    if side == "LONG":
        flow = taker >= 1.02 and oi_change >= -0.25 and top_change >= -6.0
    else:
        flow = taker <= 0.98 and oi_change >= -0.25 and top_change <= 6.0

    distance = _f(tech.get("distance_ema20_atr"), 999)
    # Location must be strict. +2.14 ATR TAO becomes WAIT.
    if side == "LONG":
        location = -0.10 <= distance <= 1.55
    else:
        location = -1.55 <= distance <= 0.10

    spread = _f(der.get("spread_bps"), 999)
    funding = _f(der.get("funding"))
    crowd = _f(der.get("global_long_short"), 1.0)
    adl = str(der.get("adl_risk", getattr(signal, "adl_risk", "unknown")) or "unknown").lower()
    news_score = _f(news.get("score"))
    if side == "LONG":
        execution = spread <= 5.0 and funding <= 0.0012 and crowd < 1.85 and adl in {"low","medium"} and news_score > -0.80
    else:
        execution = spread <= 5.0 and funding >= -0.0012 and crowd > 0.55 and adl in {"low","medium"} and news_score < 0.80

    families = {
        "TREND": bool(trend),
        "MOMENTUM": bool(momentum),
        "FLOW": bool(flow),
        "LOCATION": bool(location),
        "EXECUTION": bool(execution),
    }
    return families, {
        "families": families,
        "aligned": sum(bool(v) for v in families.values()),
        "total": 5,
        "legacy_reconstructed": True,
    }


def apply_final_gate(signal):
    families, fam = _families_from_signal(signal)
    aligned = sum(bool(v) for v in families.values())

    trend = bool(families.get("TREND"))
    location = bool(families.get("LOCATION"))
    execution = bool(families.get("EXECUTION"))

    prime = aligned == 5 and trend and location and execution
    strong_entry = aligned >= 4 and trend and location and execution
    wait_entry = aligned >= 3 and trend and execution and not location

    # Never let old flags survive if the new final gate disagrees.
    signal.strong_prime_eligible = bool(prime)
    signal.strong_auto_eligible = bool(strong_entry)

    if prime:
        signal.strong_signal_label = "PRIME_STRONG"
        signal.entry_now_state = getattr(signal, "entry_now_state", "SETUP") or "SETUP"
    elif strong_entry:
        signal.strong_signal_label = "STRONG"
        signal.entry_now_state = getattr(signal, "entry_now_state", "SETUP") or "SETUP"
    elif wait_entry:
        signal.strong_signal_label = "WAIT_ENTRY"
        signal.entry_now_state = "READY_PENDING"
    else:
        signal.strong_signal_label = "NO_TRADE"
        signal.entry_now_state = "SETUP"

    snap = signal.feature_snapshot.setdefault("final_gate_v11234", {})
    snap.update({
        "families": {k: bool(v) for k,v in families.items()},
        "aligned": aligned,
        "prime": prime,
        "strong_entry": strong_entry,
        "wait_entry": wait_entry,
        "legacy_score_diagnostic_only": True,
    })

    # Make the user-facing score consistent with the five independent families.
    # 5/5 -> 95, 4/5 -> 86, 3/5 -> 72, lower -> 60/50...
    map_score = {5:95.0, 4:86.0, 3:72.0, 2:60.0, 1:50.0, 0:40.0}
    signal.professional_rank = map_score.get(aligned, 40.0)
    return signal


def annotate_many_v11234(rows):
    return [apply_final_gate(x) for x in (rows or [])]


# Patch strong annotators so later layers cannot restore legacy STRONG/PRIME.
_old_annotate = strong.annotate
_old_many = strong.annotate_many

def annotate_v11234(signal):
    try:
        signal = _old_annotate(signal)
    except Exception:
        pass
    return apply_final_gate(signal)


def annotate_many_strong_v11234(rows):
    out = []
    for s in (rows or []):
        try:
            s = _old_annotate(s)
        except Exception:
            pass
        out.append(apply_final_gate(s))
    return out


def install():
    strong.annotate = annotate_v11234
    strong.annotate_many = annotate_many_strong_v11234
    base.annotate_strong_signal = annotate_v11234
    base.annotate_strong_signals = annotate_many_strong_v11234

    # Hook post-processing aliases when present.
    if hasattr(base, "annotate_decision_edges"):
        # no-op: decision edge remains diagnostic; final gate runs after strong layer
        pass

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
