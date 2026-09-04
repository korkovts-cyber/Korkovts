"""V11.23.0 · Consolidated independent-family signal core.

Why:
The legacy strategy double-counts correlated indicators (EMA/Supertrend/Ichimoku,
MACD/RSI/Stoch, OBV/CMF/MFI/taker), then requires several of the same concepts
again as hard gates. This overlay adds a clean fallback for candidates rejected
by that over-constrained combination.

Principles:
- Existing legacy signal remains authoritative if it passes.
- Fallback never bypasses complete derivatives, ADL, spread, funding/crowding,
  severe opposing news, or basic geometry.
- Five independent families:
  TREND, MOMENTUM, FLOW, LOCATION, EXECUTION.
- TREND + EXECUTION mandatory; at least 4/5 families must align.
- No indicator receives multiple votes inside one family.
"""
from __future__ import annotations

import math

import bot_v11191 as runtime
import app.scanner as app_scanner
from app.indicators import enrich
from app.strategy import Signal, _strength_score
from app.config import ROUND_TRIP_COST_PCT
import v11191_futures_engine as futures

base = runtime.base
VERSION = "11.23.0"

_original_analyze = futures.legacy.analyze


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _family_fallback(symbol, timeframe, df, higher=None, min_score=75, lower=None,
                     market_bias=None, derivatives=None, news=None,
                     market_context=None, audit=None):
    # This fallback is a production Futures fallback, not a preliminary
    # technical screener. Full derivatives are mandatory.
    if not isinstance(derivatives, dict) or not derivatives.get("deep_data"):
        return None

    try:
        x = enrich(df)
        h = enrich(higher) if higher is not None else None
        l = enrich(lower) if lower is not None else None
        if len(x) < 220 or h is None or len(h) < 180:
            return None
        a, p = x.iloc[-1], x.iloc[-2]
        q = h.iloc[-1]
        z = l.iloc[-1] if l is not None and len(l) >= 50 else None
    except Exception:
        return None

    price = _f(a.close)
    atr = _f(a.atr)
    if price <= 0 or atr <= 0:
        return None

    # Market data / execution hard safety.
    adl_risk = str(derivatives.get("adl_risk", "unknown")).lower()
    adl_fresh = bool(derivatives.get("adl_fresh", False))
    spread = _f(derivatives.get("spread_bps"), 999)
    funding = _f(derivatives.get("funding"))
    crowd = _f(derivatives.get("global_ls"), 1.0)
    basis = _f(derivatives.get("basis_bps"))
    oi = _f(derivatives.get("open_interest"))
    oi_change = _f(derivatives.get("oi_change_pct"))
    taker = _f(derivatives.get("taker_ratio"), 1.0)
    top_change = _f(derivatives.get("top_position_change_pct"))
    quality = int(derivatives.get("data_quality", 0) or 0)

    if quality < 8 or not adl_fresh or adl_risk not in {"low", "medium"}:
        return None
    if spread > 5.0 or abs(basis) > 20.0:
        return None

    news_score = _f((news or {}).get("score"))
    news_sources = int((news or {}).get("sources", 0) or 0)
    if news_sources < 1:
        return None

    # ---------- independent family votes ----------
    # TREND: structure only. EMA and HTF structure are used once; Supertrend
    # and Ichimoku are intentionally not separate votes.
    trend_long = (
        a.ema20 > a.ema50
        and q.ema20 > q.ema50
        and q.close > q.ema200
    )
    trend_short = (
        a.ema20 < a.ema50
        and q.ema20 < q.ema50
        and q.close < q.ema200
    )

    # MOMENTUM: one composite vote. ADX controls quality; MACD/RSI express side.
    momentum_long = (
        _f(a.adx) >= 18
        and _f(a.macd_hist) > _f(p.macd_hist)
        and 45 <= _f(a.rsi) <= 72
    )
    momentum_short = (
        _f(a.adx) >= 18
        and _f(a.macd_hist) < _f(p.macd_hist)
        and 28 <= _f(a.rsi) <= 55
    )

    # FLOW: derivatives are the independent live confirmation.
    # Mild OI contraction is tolerated; directional taker flow is required.
    flow_long = taker >= 1.02 and oi_change >= -0.25 and top_change >= -6.0
    flow_short = taker <= 0.98 and oi_change >= -0.25 and top_change <= 6.0

    # LOCATION: avoid chasing. Use ATR distance + VWAP/lower-TF timing once.
    dist = (price - _f(a.ema20)) / atr
    lower_long = z is None or (_f(z.close) >= _f(z.ema20) or _f(z.macd_hist) >= 0)
    lower_short = z is None or (_f(z.close) <= _f(z.ema20) or _f(z.macd_hist) <= 0)
    location_long = -0.10 <= dist <= 1.55 and price >= _f(a.vwap20) and lower_long
    location_short = -1.55 <= dist <= 0.10 and price <= _f(a.vwap20) and lower_short

    # EXECUTION/RISK: one hard family. Avoid crowded/funding extremes.
    execution_long = (
        spread <= 5.0 and funding <= 0.0012 and crowd < 1.85
        and news_score > -0.80
    )
    execution_short = (
        spread <= 5.0 and funding >= -0.0012 and crowd > 0.55
        and news_score < 0.80
    )

    families_long = {
        "TREND": trend_long,
        "MOMENTUM": momentum_long,
        "FLOW": flow_long,
        "LOCATION": location_long,
        "EXECUTION": execution_long,
    }
    families_short = {
        "TREND": trend_short,
        "MOMENTUM": momentum_short,
        "FLOW": flow_short,
        "LOCATION": location_short,
        "EXECUTION": execution_short,
    }

    def side_score(fams, side):
        aligned = sum(bool(v) for v in fams.values())
        # Quality within families adjusts ranking but does not create extra votes.
        score = aligned * 18.0
        score += min(5.0, max(0.0, (_f(a.adx) - 18.0) * 0.35))
        score += min(4.0, max(0.0, abs(taker - 1.0) * 80.0))
        score += 3.0 if _f(a.efficiency20) >= 0.30 else 0.0
        if adl_risk == "medium":
            score -= 4.0
        if side == "LONG" and market_bias == "SHORT":
            score -= 8.0
        if side == "SHORT" and market_bias == "LONG":
            score -= 8.0
        return aligned, max(0.0, min(100.0, score))

    nL, sL = side_score(families_long, "LONG")
    nS, sS = side_score(families_short, "SHORT")

    # Direction must be meaningfully clearer; avoid coin-flip candidates.
    if sL >= sS:
        side, fams, aligned, fam_score, opposite = "LONG", families_long, nL, sL, sS
    else:
        side, fams, aligned, fam_score, opposite = "SHORT", families_short, nS, sS, sL

    # Mandatory independent concepts + 4/5 agreement.
    if aligned < 4 or not fams["TREND"] or not fams["EXECUTION"]:
        if isinstance(audit, dict):
            audit.setdefault("issues", []).append(
                f"family consensus {aligned}/5; trend={int(fams['TREND'])}; execution={int(fams['EXECUTION'])}"
            )
        return None

    if fam_score - opposite < 10.0:
        if isinstance(audit, dict):
            audit.setdefault("issues", []).append(
                f"family direction gap {fam_score-opposite:.1f} < 10"
            )
        return None

    # Respect regime only as a veto when it is directional and the candidate
    # has only 4/5 families. Exceptional 5/5 independent setups may diverge.
    if market_bias in {"LONG", "SHORT"} and side != market_bias and aligned < 5:
        return None

    # Geometry: controlled ATR risk, no ultra-tight artificial stops.
    if side == "LONG":
        entry_low = price - atr * 0.08
        entry_high = price + atr * 0.06
        entry = entry_high
        stop = min(entry - atr * 1.20, _f(a.ema20) - atr * 0.20)
        risk = entry - stop
        if risk <= 0:
            return None
        tp1, tp2, tp3 = entry + risk, entry + 2*risk, entry + 3*risk
    else:
        entry_low = price - atr * 0.06
        entry_high = price + atr * 0.08
        entry = entry_low
        stop = max(entry + atr * 1.20, _f(a.ema20) + atr * 0.20)
        risk = stop - entry
        if risk <= 0:
            return None
        tp1, tp2, tp3 = entry - risk, entry - 2*risk, entry - 3*risk

    if not atr * 0.85 <= risk <= atr * 2.10:
        return None
    cost_r = entry * (float(ROUND_TRIP_COST_PCT) / 100.0) / risk
    if cost_r > 0.25:
        return None

    reasons = [
        f"Independent families: {aligned}/5",
        *[f"{name}: confirmed" for name, ok in fams.items() if ok],
        f"ADX {_f(a.adx):.1f}",
        f"taker {taker:.3f} · OI {oi_change:+.2f}%",
        f"spread {spread:.2f} bps",
    ]

    snap = {
        "decision": {
            "side": side,
            "setup": "INDEPENDENT FAMILY CONSENSUS",
            "threshold": float(min_score),
            "family_score": fam_score,
            "opposite_family_score": opposite,
            "family_gap": fam_score - opposite,
        },
        "family_consensus_v11230": {
            "aligned": aligned,
            "total": 5,
            "families": {k: bool(v) for k, v in fams.items()},
            "no_double_count": True,
        },
        "technical": {
            "close": price,
            "atr": atr,
            "atr_pct": _f(a.atr_pct),
            "adx": _f(a.adx),
            "rsi": _f(a.rsi),
            "macd_hist": _f(a.macd_hist),
            "efficiency20": _f(a.efficiency20),
            "distance_ema20_atr": dist,
        },
        "derivatives": {
            "funding": funding,
            "open_interest": oi,
            "oi_change_pct": oi_change,
            "taker_ratio": taker,
            "global_long_short": crowd,
            "top_position_change_pct": top_change,
            "spread_bps": spread,
            "basis_bps": basis,
            "adl_risk": adl_risk,
            "adl_age_minutes": _f(derivatives.get("adl_age_minutes"), 9999),
            "data_quality": quality,
            "data_quality_total": int(derivatives.get("data_quality_total", 9) or 9),
        },
        "news": {
            "score": news_score,
            "sources": news_sources,
            "event_risk": _f((news or {}).get("event_risk")),
        },
        "market": dict(market_context or {"bias": market_bias}),
    }

    score_display = _strength_score(max(75.0, fam_score))
    lev = 1 if _f(a.atr_pct) >= 1.5 or adl_risk == "medium" else 2
    sig = Signal(
        symbol, timeframe, side, score_display,
        entry_low, entry_high, stop, tp1, tp2, tp3, 2.0, reasons,
        funding=funding, open_interest=oi, volatility_pct=_f(a.atr_pct),
        leverage=lev, expected_window=("30 минут–4 часа" if str(timeframe).upper()=="15M" else "6–48 часов"),
        setup_type="НЕЗАВИСИМЫЙ КОНСЕНСУС 4/5",
        review_window=("1 час" if str(timeframe).upper()=="15M" else "4 часа"),
        data_quality=quality,
        data_quality_total=int(derivatives.get("data_quality_total", 9) or 9),
        estimated_cost_r=cost_r,
        adl_risk=adl_risk,
        market_context=dict(market_context or {"bias": market_bias}),
        feature_snapshot=snap,
    )
    return sig


def analyze_v11230(*args, **kwargs):
    # Preserve every existing legacy signal.
    result = _original_analyze(*args, **kwargs)
    if result is not None:
        result.feature_snapshot.setdefault("family_consensus_v11230", {}).update({
            "legacy_pass": True,
            "fallback_used": False,
        })
        return result

    # Only after legacy rejects do we try the independent-family model.
    # Resolve positional app.strategy signature.
    vals = list(args)
    names = [
        "symbol","timeframe","df","higher","min_score","lower",
        "market_bias","derivatives","news","market_context","audit"
    ]
    params = {}
    for i, name in enumerate(names):
        if i < len(vals):
            params[name] = vals[i]
    params.update(kwargs)

    fb = _family_fallback(
        params.get("symbol"),
        params.get("timeframe"),
        params.get("df"),
        params.get("higher"),
        params.get("min_score", 75),
        params.get("lower"),
        params.get("market_bias"),
        params.get("derivatives"),
        params.get("news"),
        params.get("market_context"),
        params.get("audit"),
    )
    if fb is not None:
        fb.feature_snapshot.setdefault("family_consensus_v11230", {}).update({
            "legacy_pass": False,
            "fallback_used": True,
        })
    return fb


_old_hb = base.heartbeat_text
def heartbeat_v11230(diagnostics, **kwargs):
    text = _old_hb(diagnostics, **kwargs)
    text += (
        "\n🧭 Signal Core: <b>V11.23.0 independent families</b>"
        " · Trend/Momentum/Flow/Location/Execution"
        " · minimum <b>4/5</b>"
    )
    return text


def install():
    # Patch runtime aliases used by Futures discovery/deep analysis.
    futures.legacy.analyze = analyze_v11230
    app_scanner.analyze = analyze_v11230
    base.core.analyze = analyze_v11230

    base.heartbeat_text = heartbeat_v11230
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
