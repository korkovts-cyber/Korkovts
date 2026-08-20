"""Korkovts V11.21.5 · CODE-QUALITY AUDITED FULL-UNIVERSE FUTURES ENGINE.

Goal:
- evaluate the whole liquid Binance USD-M perpetual universe before any hard technical gate;
- rank every liquid symbol LONG and SHORT before expensive derivatives requests;
- rank LONG and SHORT opportunity independently;
- spend up to ~3 minutes on a complete scan when the universe/API requires it;
- keep the V11.18 final execution/risk/ENTRY NOW layers authoritative.
"""
from __future__ import annotations

import asyncio
import copy
import math
import os
import time
from datetime import datetime, timezone

from app.config import (
    MIN_24H_QUOTE_VOLUME,
    NEUTRAL_REGIME_MAX_SIGNALS,
)
from app.db import calibration_penalty
from app.indicators import enrich
from app.strategy import Signal, _strength_score
from app.liquidations import snapshot as liquidation_snapshot
from app.market import (
    get_adl_risks,
    get_klines,
    get_symbols,
    get_tickers,
)
from app.research import annotate_correlation_clusters
import app.scanner as legacy
from v11197_sources import mandatory_sources, status as mandatory_source_status
from v11198_deep_screen import screen as quick_deep_screen, select_full_deep, FULL_DEEP_TARGET, MIN_SCREEN_COVERAGE

# V11.21.5: stale Railway env may make the budget longer, never shorter than the architecture needs.
FULL_SCAN_BUDGET_SEC = max(170, min(240, int(os.getenv("V11194_FULL_SCAN_BUDGET_SEC", "175"))))
SOURCE_STAGE_TIMEOUT_SEC = max(15, min(45, int(os.getenv("V11194_SOURCE_TIMEOUT_SEC", "30"))))
FRAME_STAGE_MAX_SEC = max(45, min(120, int(os.getenv("V11194_FRAME_STAGE_MAX_SEC", "90"))))
FRAME_REQUEST_TIMEOUT_SEC = max(8, min(30, int(os.getenv("V11194_FRAME_REQUEST_TIMEOUT_SEC", "22"))))
MIN_FRAME_COVERAGE = max(.80, min(1.0, float(os.getenv("V11194_MIN_FRAME_COVERAGE", ".95"))))
DEEP_CONCURRENCY = max(2, min(5, int(os.getenv("V11198_DEEP_CONCURRENCY", "4"))))
FRAME_CONCURRENCY = max(1, min(5, int(os.getenv("V11190_FRAME_CONCURRENCY", "4"))))
MULTIFRAME_TARGET = max(48, min(96, int(os.getenv("V11214_MULTIFRAME_TARGET", "72"))))
MAX_RETURN_CANDIDATES = max(8, min(24, int(os.getenv("V11191_MAX_RETURN_CANDIDATES", "20"))))
DEEP_SHORTLIST = max(24, min(48, int(os.getenv("V11191_FUTURES_DEEP_SHORTLIST", "36"))))
MIN_OPPOSITE_SIDE_RESERVE = max(4, min(10, int(os.getenv("V11193_MIN_OPPOSITE_SIDE_RESERVE", "8"))))
MIN_ACTIONABLE_QUOTE_VOLUME = float(
    os.getenv("V11190_MIN_ACTIONABLE_QUOTE_VOLUME", str(MIN_24H_QUOTE_VOLUME))
)
# Near-liquid names are still inspected in stage 1 and appear in diagnostics,
# but only the liquid universe may generate an actionable signal.
MIN_OBSERVED_QUOTE_VOLUME = float(os.getenv("V11190_MIN_OBSERVED_QUOTE_VOLUME", "1000000"))

_last = {
    "main": {"status": "idle"},
    "short": {"status": "idle"},
}


def _diag(kind):
    return {
        "kind": kind,
        "status": "running",
        "reason": "",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "universe": 0,
        "observed": 0,
        "liquid": 0,
        "frames_ok": 0,
        "frames_failed": 0,
        "deep_checked": 0,
        "deep_complete": 0,
        "derivatives_incomplete": 0,
        "deep_errors": 0,
        "final": 0,
        "long_final": 0,
        "short_final": 0,
        "elapsed_sec": 0.0,
        "budget_sec": FULL_SCAN_BUDGET_SEC,
        "regime": "UNKNOWN",
        "independent_mode": False,
        "threshold": None,
        "top_long_watch": [],
        "top_short_watch": [],
        "rejections": {},
        "error_examples": [],
        "momentum_fallback": 0,
    }


def scan_status():
    """Return V11.21 discovery truth plus isolated Production diagnostics.

    Legacy V11.18 writes into app.scanner._last_scan during _prepare/_health_gate.
    Those fields must not overwrite the new source/frame/deep funnel, otherwise a
    previous Production reason/status can masquerade as the current scan state.
    """
    merged=copy.deepcopy(_last)
    compat=copy.deepcopy(getattr(legacy,"_last_scan",{}) or {})
    production_keys=(
        "production_pool","pre_v1142_final","v1142_filtered",
        "alpha_rejected","execution_rejected","evidence_rejected",
        "indicator_rejected","adaptive_rejected","strong_rejected",
        "protection_rejected","portfolio_rejected","v1142_duration_sec",
        "news_degraded","news_reason","health","clock_offset_ms","clock_rtt_ms",
    )
    for kind in ("main","short"):
        raw=dict(compat.get(kind) or {})
        dst=merged.setdefault(kind,{})
        prod={key:copy.deepcopy(raw[key]) for key in production_keys if key in raw}
        if prod:
            dst["production"]=prod
            for key,value in prod.items():
                # Preserve compatibility for UI fields that are genuinely owned
                # by Production, without allowing status/reason/funnel overwrite.
                dst[key]=value
        # Production final is meaningful only after this same full-universe run
        # actually produced a pool. It may refine final, but never resurrect a
        # stale final from a prior cycle.
        if "production_pool" in raw and int(dst.get("deep_complete",0) or 0)>0:
            dst["final"]=int(raw.get("final",dst.get("final",0)) or 0)
    return merged


def _finish(d, status, reason=""):
    d["status"] = status
    d["reason"] = str(reason or "")
    d["finished_at"] = datetime.now(timezone.utc).isoformat()
    try:
        start = datetime.fromisoformat(d["started_at"].replace("Z", "+00:00")).timestamp()
        d["elapsed_sec"] = max(0.0, time.time() - start)
    except Exception:
        pass
    # Compatibility with old UI, but do not collapse the two-stage deep funnel.
    # prefiltered = cheap derivatives screen candidate count (normally 36);
    # deep_checked = expensive production snapshot count (normally 14).
    d["prefiltered"] = int(d.get("deep_screen_candidates", d.get("deep_checked", 0)) or 0)
    d["deep_rejected"] = max(0, int(d.get("deep_checked",0) or 0) - int(d.get("final",0) or 0))
    rejects=dict(d.get("rejections") or {})
    d["top_rejections"]=[
        {"reason":str(k),"count":int(v)}
        for k,v in sorted(
            ((k,v) for k,v in rejects.items() if str(k)!="PASS"),
            key=lambda item:(-int(item[1]),str(item[0]))
        )[:3]
    ]
    d["technical_rejected"] = max(0, d.get("observed", 0) - d.get("frames_ok", 0))
    d["technical_errors"] = d.get("frames_failed", 0)
    _last[d["kind"]] = copy.deepcopy(d)
    try:
        legacy._last_scan[d["kind"]]=copy.deepcopy(d)
    except Exception:
        pass


def _f(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else float(default)
    except Exception:
        return float(default)


def _soft_side_score(base_df, higher_df, lower_df, side, ticker_change=0.0, btc_change=0.0):
    """Broad opportunity score. It deliberately does not require a final setup."""
    try:
        b = enrich(base_df)
        h = enrich(higher_df)
        l = enrich(lower_df)
        if len(b) < 80 or len(h) < 80 or len(l) < 50:
            return 0.0
        x, p = b.iloc[-1], b.iloc[-2]
        q = h.iloc[-1]
        z = l.iloc[-1]
    except Exception:
        return 0.0

    long = str(side).upper() == "LONG"
    score = 0.0

    if long:
        if x.ema20 > x.ema50: score += 12
        if x.ema50 > x.ema200: score += 10
        if q.ema20 > q.ema50: score += 10
        if q.close > q.ema200: score += 8
        if x.plus_di > x.minus_di: score += 7
        if x.macd_hist > p.macd_hist: score += 6
        if x.close > x.vwap20: score += 5
        if x.obv > x.obv_ema20: score += 4
        if z.close > z.ema20: score += 5
        if z.macd_hist > 0: score += 4
        if x.taker_imbalance10 >= 0.02: score += 5
        if x.cvd > x.cvd_ema20: score += 4
        if 42 <= x.rsi <= 72: score += 4
        if ticker_change > btc_change + 1.0: score += 6
        if x.close >= x.high20 * 0.995: score += 5
    else:
        if x.ema20 < x.ema50: score += 12
        if x.ema50 < x.ema200: score += 10
        if q.ema20 < q.ema50: score += 10
        if q.close < q.ema200: score += 8
        if x.minus_di > x.plus_di: score += 7
        if x.macd_hist < p.macd_hist: score += 6
        if x.close < x.vwap20: score += 5
        if x.obv < x.obv_ema20: score += 4
        if z.close < z.ema20: score += 5
        if z.macd_hist < 0: score += 4
        if x.taker_imbalance10 <= -0.02: score += 5
        if x.cvd < x.cvd_ema20: score += 4
        if 28 <= x.rsi <= 58: score += 4
        if ticker_change < btc_change - 1.0: score += 6
        if x.close <= x.low20 * 1.005: score += 5

    adx = _f(getattr(x, "adx", 0))
    score += min(10.0, max(0.0, adx - 14.0) * 0.6)
    eff = _f(getattr(x, "efficiency20", 0))
    if eff >= .35: score += 6
    elif eff < .15: score -= 8
    vol_z = _f(getattr(x, "vol_z", 0))
    if vol_z >= .5: score += min(6, vol_z * 2)
    atrp = _f(getattr(x, "atr_pct", 0))
    if atrp > 4.0: score -= 8
    return max(0.0, min(100.0, score))


def _risk_aware_leverage(signal):
    """Conservative leverage ceiling; position risk remains independent of leverage."""
    side = str(getattr(signal, "side", "LONG")).upper()
    entry = _f(getattr(signal, "entry_high" if side == "LONG" else "entry_low", 0))
    stop = _f(getattr(signal, "stop", 0))
    if entry <= 0 or stop <= 0:
        return 1, "invalid geometry"
    stop_pct = abs(entry - stop) / entry * 100.0
    if stop_pct <= 0:
        return 1, "invalid stop distance"

    score = _f(getattr(signal, "score", 0))
    vol = _f(getattr(signal, "volatility_pct", 0))
    adl = str(getattr(signal, "adl_risk", "unknown")).lower()

    # Keep margin loss at the stop roughly <=10% before fees/slippage.
    geometry_cap = max(1, min(5, int(10.0 / max(stop_pct, 0.40))))
    if score >= 94: quality_cap = 5
    elif score >= 90: quality_cap = 4
    elif score >= 86: quality_cap = 3
    else: quality_cap = 2

    if vol >= 2.5: vol_cap = 2
    elif vol >= 1.6: vol_cap = 3
    elif vol >= 1.0: vol_cap = 4
    else: vol_cap = 5

    adl_cap = 2 if adl == "medium" else (1 if adl not in ("low", "medium") else 5)
    lev = max(1, min(geometry_cap, quality_cap, vol_cap, adl_cap))
    reason = f"stop {stop_pct:.2f}% · vol {vol:.2f}% · quality {score:.0f} · ADL {adl.upper()}"
    return lev, reason


def _decorate(signal, soft_long, soft_short, deep_order, total):
    lev, lev_reason = _risk_aware_leverage(signal)
    signal.leverage = lev
    signal.deep_scan_rank = int(deep_order)
    signal.deep_scan_universe = int(total)
    signal.deep_soft_long = float(soft_long)
    signal.deep_soft_short = float(soft_short)
    signal.leverage_reason = lev_reason
    signal.position_risk_pct = 0.35
    signal.deep_analysis = True
    signal.reasons = list(getattr(signal, "reasons", []) or [])
    signal.reasons.append(
        f"V11.19 deep scan: derivatives checked {deep_order}/{total}; "
        f"broad LONG/SHORT rank {soft_long:.0f}/{soft_short:.0f}"
    )
    fs = dict(getattr(signal, "feature_snapshot", {}) or {})
    fs["deep_market_v11190"] = {
        "universe_rank": int(deep_order),
        "deep_universe": int(total),
        "soft_long": round(float(soft_long), 3),
        "soft_short": round(float(soft_short), 3),
        "recommended_max_leverage": int(lev),
        "leverage_reason": lev_reason,
        "risk_budget_pct": 0.35,
    }
    signal.feature_snapshot = fs
    return signal


def _primary_side_score(frame, side, ticker_change=0.0, btc_change=0.0):
    """Cheap one-timeframe whole-market rank before full 3-TF enrichment."""
    try:
        x=enrich(frame)
        if len(x)<80:
            return 0.0
        a,p=x.iloc[-1],x.iloc[-2]
    except Exception:
        return 0.0
    long=str(side).upper()=="LONG"
    score=0.0
    if long:
        if a.ema20>a.ema50: score+=18
        if a.ema50>a.ema200: score+=14
        if a.close>a.ema20: score+=7
        if a.plus_di>a.minus_di: score+=8
        if a.macd_hist>p.macd_hist: score+=7
        if a.close>a.vwap20: score+=6
        if a.obv>a.obv_ema20: score+=5
        if a.taker_imbalance10>=0: score+=5
        if 40<=a.rsi<=74: score+=4
        if ticker_change>btc_change+.75: score+=8
    else:
        if a.ema20<a.ema50: score+=18
        if a.ema50<a.ema200: score+=14
        if a.close<a.ema20: score+=7
        if a.minus_di>a.plus_di: score+=8
        if a.macd_hist<p.macd_hist: score+=7
        if a.close<a.vwap20: score+=6
        if a.obv<a.obv_ema20: score+=5
        if a.taker_imbalance10<=0: score+=5
        if 26<=a.rsi<=60: score+=4
        if ticker_change<btc_change-.75: score+=8
    adx=_f(getattr(a,"adx",0))
    score+=min(10.0,max(0.0,adx-14.0)*.6)
    eff=_f(getattr(a,"efficiency20",0))
    if eff>=.35: score+=6
    elif eff<.15: score-=6
    volz=_f(getattr(a,"vol_z",0))
    if volz>=.5: score+=min(5.0,volz*1.5)
    return max(0.0,min(100.0,score))


async def _primary_frame(symbol,kind,sem):
    """One request per liquid symbol. This is the true whole-market pass."""
    try:
        interval="1h" if kind=="main" else "15m"
        limit=280 if kind=="main" else 300
        async with sem:
            frame=await asyncio.wait_for(
                get_klines(symbol,interval,limit),
                timeout=FRAME_REQUEST_TIMEOUT_SEC,
            )
        return symbol,frame,None
    except Exception as exc:
        return symbol,None,exc


async def _extra_frames(symbol,kind,sem):
    """Only ranked finalists receive the other two timeframes."""
    try:
        async with sem:
            if kind=="main":
                request=asyncio.gather(
                    get_klines(symbol,"15m",280),
                    get_klines(symbol,"4h",360),
                )
            else:
                request=asyncio.gather(
                    get_klines(symbol,"5m",300),
                    get_klines(symbol,"1h",360),
                )
            lower,higher=await asyncio.wait_for(
                request,timeout=FRAME_REQUEST_TIMEOUT_SEC
            )
        return symbol,lower,higher,None
    except Exception as exc:
        return symbol,None,None,exc


async def _frames(symbol, kind, sem):
    try:
        async with sem:
            if kind == "main":
                request = asyncio.gather(
                    get_klines(symbol, "15m", 280),
                    get_klines(symbol, "1h", 360),
                    get_klines(symbol, "4h", 360),
                )
            else:
                request = asyncio.gather(
                    get_klines(symbol, "5m", 300),
                    get_klines(symbol, "15m", 360),
                    get_klines(symbol, "1h", 360),
                )
            lower, base, higher = await asyncio.wait_for(
                request, timeout=FRAME_REQUEST_TIMEOUT_SEC
            )
        return symbol, lower, base, higher, None
    except asyncio.TimeoutError:
        return symbol, None, None, None, TimeoutError(
            f"frame batch exceeded {FRAME_REQUEST_TIMEOUT_SEC}s"
        )
    except Exception as exc:
        return symbol, None, None, None, exc


def _momentum_fallback(symbol,timeframe,base,higher,lower,side,d,market_context,news,min_score):
    try:
        b=enrich(base); h=enrich(higher); l=enrich(lower)
        if len(b)<80 or len(h)<80 or len(l)<50: return None
        a,p=b.iloc[-1],b.iloc[-2]; q=h.iloc[-1]; z=l.iloc[-1]
        price=_f(a.close); atr=_f(a.atr)
        if price<=0 or atr<=0: return None
        side=str(side).upper(); long=side=="LONG"
        dist=(price-_f(a.ema20))/atr
        adx=_f(a.adx); eff=_f(a.efficiency20); rsi=_f(a.rsi,50)
        spread=_f(d.get("spread_bps"),999); taker=_f(d.get("taker_ratio"),1)
        oi_change=_f(d.get("oi_change_pct")); funding=_f(d.get("funding"))
        crowd=_f(d.get("global_ls"),1); basis=_f(d.get("basis_bps"))
        adl=str(d.get("adl_risk","unknown")).lower(); adl_fresh=bool(d.get("adl_fresh",False))
        bias=str((market_context or {}).get("bias") or "NEUTRAL").upper()
        independent=bool((market_context or {}).get("independent_mode"))
        if long:
            structure=(a.ema20>a.ema50>a.ema200 and q.ema20>q.ema50 and q.close>q.ema200
                       and z.close>z.ema20 and z.macd_hist>=0 and a.plus_di>a.minus_di and a.macd_hist>0)
            flow_ok=taker>=0.99; regime_ok=bias in ("LONG","NEUTRAL") or independent
            crowd_ok=crowd<1.90 and basis<22 and funding<=.0012
            extension_ok=.45<=dist<=2.60 and rsi<78
        else:
            structure=(a.ema20<a.ema50<a.ema200 and q.ema20<q.ema50 and q.close<q.ema200
                       and z.close<z.ema20 and z.macd_hist<=0 and a.minus_di>a.plus_di and a.macd_hist<0)
            flow_ok=taker<=1.01; regime_ok=bias in ("SHORT","NEUTRAL") or independent
            crowd_ok=crowd>.55 and basis>-22 and funding>=-.0012
            extension_ok=-2.60<=dist<=-.45 and rsi>22
        hard_ok=(bool(d.get("deep_data")) and structure and flow_ok and regime_ok and crowd_ok
                 and extension_ok and spread<=5.0 and adl in ("low","medium") and adl_fresh
                 and oi_change>=-1.0 and adx>=22 and eff>=.22)
        if not hard_ok: return None

        if long:
            low=price-.28*atr; high=price+.06*atr; entry=high; stop=entry-1.35*atr
            risk=entry-stop; tps=(entry+risk,entry+2*risk,entry+3*risk)
        else:
            low=price-.06*atr; high=price+.28*atr; entry=low; stop=entry+1.35*atr
            risk=stop-entry; tps=(entry-risk,entry-2*risk,entry-3*risk)

        soft=82.0+min(8.0,max(0.0,adx-22)*.5)+min(5.0,max(0.0,eff-.22)*12)
        soft+=3.0 if (taker>=1.03 if long else taker<=.97) else 0.0
        raw=max(float(min_score),soft)
        reasons=[
            "V11.21.5 momentum-continuation lane",
            f"HTF trend aligned · ADX {adx:.0f} · efficiency {eff:.2f}",
            f"distance EMA20 {dist:+.2f} ATR",
            f"taker {taker:.2f} · OI {oi_change:+.1f}% · spread {spread:.1f}bps",
            "entry corridor allows controlled retest; do not chase outside zone",
        ]
        fs={
            "decision":{"side":side,"setup":"MOMENTUM CONTINUATION RETEST","threshold":float(min_score),
                        "raw_long":raw if long else 0,"raw_short":raw if not long else 0,"score_gap":20.0},
            "technical":{"close":price,"atr":atr,"atr_pct":_f(a.atr_pct),"adx":adx,
                         "plus_di":_f(a.plus_di),"minus_di":_f(a.minus_di),"rsi":rsi,
                         "macd_hist":_f(a.macd_hist),"efficiency20":eff,
                         "distance_ema20_atr":dist,"taker_imbalance10":_f(a.taker_imbalance10)},
            "derivatives":{"funding":funding,"open_interest":_f(d.get("open_interest")),
                           "oi_change_pct":oi_change,"price_change_pct":_f(d.get("price_change_pct")),
                           "taker_ratio":taker,"global_long_short":crowd,
                           "top_position_long_short":_f(d.get("top_position_ls"),1),
                           "top_position_change_pct":_f(d.get("top_position_change_pct")),
                           "book_imbalance":_f(d.get("book_imbalance")),"spread_bps":spread,
                           "basis_bps":basis,"adl_risk":adl,
                           "adl_age_minutes":_f(d.get("adl_age_minutes"),9999),
                           "data_quality":int(d.get("data_quality",0) or 0),
                           "data_quality_total":int(d.get("data_quality_total",9) or 9)},
            "news":dict(news or {}),"market":dict(market_context or {}),
            "v11210":{"fallback":True,"reason":"exact geometry missed strong trend"},
        }
        return Signal(
            symbol,timeframe,side,_strength_score(raw),low,high,stop,tps[0],tps[1],tps[2],2.0,reasons,
            funding=funding,open_interest=_f(d.get("open_interest")),volatility_pct=_f(a.atr_pct),
            leverage=1,setup_type="MOMENTUM CONTINUATION RETEST",
            review_window="1 час" if timeframe=="15M" else "4 часа",
            data_quality=int(d.get("data_quality",0) or 0),
            data_quality_total=int(d.get("data_quality_total",9) or 9),
            estimated_cost_r=price*.0012/max(risk,1e-12),adl_risk=adl,
            market_context=dict(market_context or {}),feature_snapshot=fs,
        )
    except Exception:
        return None


async def _deep_one(row, kind, market_context, news, adl_risks, min_score, sem):
    symbol, lower, base, higher, soft_l, soft_s = row
    try:
        async with sem:
            # If the bulk ADL map failed, pass None so the production market
            # layer may perform the symbol-level ADL fallback instead of
            # marking every deep candidate incomplete.
            adl = adl_risks.get(symbol) if isinstance(adl_risks,dict) else None
            d = await asyncio.wait_for(legacy.get_derivatives_snapshot(symbol, adl), timeout=22)
        if not d.get("deep_data"):
            return None, "DERIVATIVES_INCOMPLETE", d
        oi_notional = _f(d.get("open_interest")) * _f(d.get("mark_price"))
        d.update(liquidation_snapshot(symbol, oi_notional))

        # Final gate remains the proven base strategy. The V11.19 change is that
        # the wide LONG/SHORT shortlist reaches this expensive gate rather than only
        # 1-2 technical prefilter survivors.
        timeframe = "1H" if kind == "main" else "15M"
        # First pass discovers the actual side through the exact production
        # analyze wrapper (freshness/coherence included). Calibration is then
        # applied to THAT side and the candidate is re-evaluated if necessary.
        strategy_audit={}
        result = legacy.analyze(
            symbol,timeframe,base,higher,float(min_score),lower,
            market_context.get("bias"),d,legacy.for_symbol(news,symbol),market_context,
            audit=strategy_audit
        )
        fallback_used=False
        if result is None:
            inferred="LONG" if float(soft_l)>=float(soft_s) else "SHORT"
            result=_momentum_fallback(
                symbol,timeframe,base,higher,lower,inferred,d,market_context,
                legacy.for_symbol(news,symbol),min_score
            )
            if result is None:
                d["_strategy_audit"]=strategy_audit
                return None,"FINAL_STRATEGY_REJECT",d
            fallback_used=True
            d["_strategy_audit"]=strategy_audit
            d["_v11210_fallback"]=True

        side = str(getattr(result,"side","") or "").upper()
        historical_penalty = max(0.0,float(calibration_penalty(symbol,side,timeframe) or 0))
        result.feature_snapshot.setdefault("v11212_cohort_isolation",{}).update({
            "historical_calibration_penalty":historical_penalty,
            "calibration_shadow_only":True,
        })
        penalty = 0.0
        if penalty>0:
            threshold=min(95.0,float(min_score)+penalty)
            if fallback_used:
                raw=float((result.feature_snapshot.get("decision") or {}).get(
                    "raw_long" if side=="LONG" else "raw_short",0) or 0)
                if raw<threshold:
                    return None,"CALIBRATION_REJECT",d
            else:
                strategy_audit={}
                result = legacy.analyze(
                    symbol,timeframe,base,higher,threshold,lower,
                    market_context.get("bias"),d,legacy.for_symbol(news,symbol),market_context,
                    audit=strategy_audit
                )
                if result is None:
                    d["_strategy_audit"]=strategy_audit
                    return None,"CALIBRATION_REJECT",d
        if kind != "main":
            result.expected_window = "30 минут–4 часа"
        return result, "", d
    except Exception as exc:
        return None, f"ERROR:{type(exc).__name__}", {"_error": str(exc)}


def _select_deep_rows(liquid_ranked, limit=DEEP_SHORTLIST, min_each=MIN_OPPOSITE_SIDE_RESERVE):
    """Select strongest setups while preserving a small opposite-side radar.

    A hard 50/50 split wastes deep capacity in a directional market. Start from
    the strongest cross-side ranking, then guarantee only ``min_each`` rows for
    each inferred side by replacing the weakest overrepresented rows.
    """
    rows=list(liquid_ranked or [])
    if not rows:
        return []
    limit=max(1,min(int(limit),len(rows)))
    min_each=max(0,min(int(min_each),limit//2))

    def side(row):
        return "LONG" if float(row[4])>=float(row[5]) else "SHORT"
    ordered=sorted(rows,key=lambda r:max(float(r[4]),float(r[5])),reverse=True)
    selected=ordered[:limit]

    for wanted in ("LONG","SHORT"):
        have=sum(side(r)==wanted for r in selected)
        need=max(0,min_each-have)
        if not need:
            continue
        candidates=[r for r in ordered[limit:] if side(r)==wanted]
        while need and candidates:
            # Replace the weakest member from the currently overrepresented side.
            replaceable=[
                (idx,r) for idx,r in enumerate(selected)
                if side(r)!=wanted and sum(side(x)==side(r) for x in selected)>min_each
            ]
            if not replaceable:
                break
            idx,_=min(replaceable,key=lambda pair:max(float(pair[1][4]),float(pair[1][5])))
            selected[idx]=candidates.pop(0)
            need-=1

    selected.sort(key=lambda r:max(float(r[4]),float(r[5])),reverse=True)
    return selected


async def _run(kind):
    d = _diag(kind)
    started = time.monotonic()
    deadline = started + FULL_SCAN_BUDGET_SEC
    _last[kind] = copy.deepcopy(d)

    def remaining(reserve=0.0):
        return max(0.0, deadline - time.monotonic() - float(reserve))

    try:
        # Mandatory discovery sources are independent and source-aware.
        # exchangeInfo can use a long verified cache; ticker/24hr only a short
        # verified cache. A failure names the actual endpoint instead of
        # collapsing into a generic TimeoutError with a fake 0 -> 0 funnel.
        try:
            symbols,tickers,source_meta=await asyncio.wait_for(
                mandatory_sources(),
                timeout=min(
                    max(20.0,SOURCE_STAGE_TIMEOUT_SEC),
                    max(1.0,remaining())
                ),
            )
        except Exception as exc:
            d["source_stage"]="ERROR"
            d["source_error"]=f"{type(exc).__name__}: {exc}"
            d["mandatory_sources"]=mandatory_source_status()
            _last[kind]=copy.deepcopy(d)
            raise RuntimeError(
                f"mandatory Futures source stage failed: {exc}"
            ) from exc

        d["source_stage"]="OK"
        d["source_meta"]=source_meta
        d["mandatory_sources"]=mandatory_source_status()
        d["universe"]=len(symbols)
        _last[kind]=copy.deepcopy(d)

        # Auxiliary news/ADL must not hold the global scan-lock indefinitely.
        try:
            news = await asyncio.wait_for(
                legacy.get_news_sentiment(),
                timeout=min(18.0, max(1.0, remaining()))
            )
        except Exception as exc:
            news = {
                "sources":0, "items":[], "assets":{}, "global":0.0,
                "breaking_events":[], "high_impact_count":0,
                "v114_news_degraded":True,
            }
            d["news_degraded"]=True
            d["news_reason"]=f"{type(exc).__name__}: {exc}"

        try:
            adl_risks = await asyncio.wait_for(
                get_adl_risks(),
                timeout=min(18.0, max(1.0, remaining()))
            )
        except Exception as exc:
            adl_risks={}
            d["adl_bulk_degraded"]=True
            d["adl_bulk_reason"]=f"{type(exc).__name__}: {exc}"

        d["news_sources"]=int((news or {}).get("sources",0) or 0)

        state = await asyncio.wait_for(
            legacy.market_state(tickers),
            timeout=min(15.0, max(1.0, remaining()))
        )
        analysis_state, neutral_mode = legacy.market_analysis_state(state)
        thresholds = legacy.scan_thresholds(state)
        min_score = thresholds["main"] if kind == "main" else thresholds["short_base"]
        d["regime"] = state.get("bias", "UNKNOWN")
        d["independent_mode"] = bool(analysis_state.get("independent_mode"))
        d["threshold"] = float(min_score)

        observed = [
            symbol for symbol in symbols
            if _f(tickers.get(symbol, {}).get("quote_volume")) >= MIN_OBSERVED_QUOTE_VOLUME
        ]
        liquid = [
            symbol for symbol in observed
            if _f(tickers.get(symbol, {}).get("quote_volume")) >= MIN_ACTIONABLE_QUOTE_VOLUME
        ]
        observed.sort(
            key=lambda symbol: _f(tickers.get(symbol, {}).get("quote_volume")),
            reverse=True,
        )
        liquid_set=set(liquid)
        d["observed"]=len(observed)
        d["liquid"]=len(liquid)
        _last[kind]=copy.deepcopy(d)

        # Stage 1A: one primary timeframe for EVERY actionable/liquid symbol.
        # The previous implementation fetched three timeframes for every
        # observed >=$1m symbol. With 491 observed names that meant 1473 kline
        # requests before ranking, impossible inside a 90s stage at 4.5 req/s.
        #
        # Whole-market coverage now means every actionable liquid symbol gets a
        # genuine candle analysis + all observed names remain represented by
        # ticker/liquidity telemetry. Only the strongest balanced subset spends
        # REST budget on the other two timeframes.
        sem=asyncio.Semaphore(FRAME_CONCURRENCY)
        primary_tasks=[
            asyncio.create_task(_primary_frame(symbol,kind,sem))
            for symbol in liquid
        ]
        primary_budget=min(65.0,max(1.0,remaining(reserve=70.0)))
        done,pending=await asyncio.wait(primary_tasks,timeout=primary_budget)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending,return_exceptions=True)

        btc_change=_f(tickers.get("BTCUSDT",{}).get("change"))
        primary_rows=[]
        for task in done:
            try:
                symbol,frame,err=task.result()
            except Exception as exc:
                d["frames_failed"]+=1
                continue
            if err is not None or frame is None:
                d["frames_failed"]+=1
                if len(d["error_examples"])<5:
                    d["error_examples"].append(f"{symbol}: primary frame {err}")
                continue
            change=_f(tickers.get(symbol,{}).get("change"))
            sl=_primary_side_score(frame,"LONG",change,btc_change)
            ss=_primary_side_score(frame,"SHORT",change,btc_change)
            primary_rows.append((symbol,frame,sl,ss))

        d["primary_frames_ok"]=len(primary_rows)
        d["primary_frames_target"]=len(liquid)
        d["primary_pending_cancelled"]=len(pending)
        primary_coverage=len(primary_rows)/max(1,len(liquid))
        d["primary_frame_coverage"]=round(primary_coverage,4)
        _last[kind]=copy.deepcopy(d)

        # A full-market scan must see nearly all ACTIONABLE names. We do not
        # require 95% coverage of hundreds of non-actionable $1m names.
        if primary_coverage < .90:
            raise RuntimeError(
                f"actionable primary-frame coverage incomplete: "
                f"{len(primary_rows)}/{len(liquid)} ({primary_coverage:.0%})"
            )

        # Balanced 72-name multiframe shortlist. It is adaptive in directional
        # markets but preserves meaningful opposite-side discovery.
        ordered=sorted(
            primary_rows,
            key=lambda row:max(float(row[2]),float(row[3])),
            reverse=True,
        )
        target=min(MULTIFRAME_TARGET,len(ordered))
        reserve=min(18,target//3)
        selected=ordered[:target]

        def _pside(row):
            return "LONG" if float(row[2])>=float(row[3]) else "SHORT"

        for wanted in ("LONG","SHORT"):
            have=sum(_pside(r)==wanted for r in selected)
            need=max(0,reserve-have)
            candidates=[r for r in ordered[target:] if _pside(r)==wanted]
            while need and candidates:
                replaceable=[
                    (i,r) for i,r in enumerate(selected)
                    if _pside(r)!=wanted and sum(_pside(x)==_pside(r) for x in selected)>reserve
                ]
                if not replaceable:
                    break
                i,_=min(replaceable,key=lambda pair:max(pair[1][2],pair[1][3]))
                selected[i]=candidates.pop(0)
                need-=1

        primary_map={row[0]:row[1] for row in selected}
        extra_tasks=[
            asyncio.create_task(_extra_frames(row[0],kind,sem))
            for row in selected
        ]
        extra_budget=min(55.0,max(1.0,remaining(reserve=45.0)))
        done2,pending2=await asyncio.wait(extra_tasks,timeout=extra_budget)
        for task in pending2:
            task.cancel()
        if pending2:
            await asyncio.gather(*pending2,return_exceptions=True)

        frame_rows=[]
        for task in done2:
            try:
                symbol,lower,higher,err=task.result()
            except Exception as exc:
                d["frames_failed"]+=1
                continue
            base=primary_map.get(symbol)
            if err is not None or base is None or lower is None or higher is None:
                d["frames_failed"]+=1
                if len(d["error_examples"])<5:
                    d["error_examples"].append(f"{symbol}: extra frames {err}")
            else:
                frame_rows.append((symbol,lower,base,higher,None))

        d["multiframe_target"]=len(selected)
        d["multiframe_ok"]=len(frame_rows)
        d["multiframe_pending_cancelled"]=len(pending2)
        multi_coverage=len(frame_rows)/max(1,len(selected))
        d["multiframe_coverage"]=round(multi_coverage,4)
        d["frames_ok"]=len(frame_rows)
        d["frame_pending_cancelled"]=len(pending)+len(pending2)
        _last[kind]=copy.deepcopy(d)

        if multi_coverage < .85:
            raise RuntimeError(
                f"ranked multiframe coverage incomplete: "
                f"{len(frame_rows)}/{len(selected)} ({multi_coverage:.0%})"
            )

        ranked=[]
        for symbol,lower,base,higher,err in frame_rows:
            change=_f(tickers.get(symbol,{}).get("change"))
            sl=_soft_side_score(base,higher,lower,"LONG",change,btc_change)
            ss=_soft_side_score(base,higher,lower,"SHORT",change,btc_change)
            ranked.append((symbol,lower,base,higher,sl,ss))

        # The meaningful coverage metric is now the liquid-universe primary pass.
        d["frame_coverage"]=round(primary_coverage,4)
        _last[kind]=copy.deepcopy(d)

        ranked.sort(key=lambda row:max(row[4],row[5]),reverse=True)
        d["top_long_watch"]=[
            {"symbol":row[0],"score":round(row[4],1)}
            for row in sorted(ranked,key=lambda row:row[4],reverse=True)[:5]
        ]
        d["top_short_watch"]=[
            {"symbol":row[0],"score":round(row[5],1)}
            for row in sorted(ranked,key=lambda row:row[5],reverse=True)[:5]
        ]

        near={}
        for row in (
            sorted(ranked,key=lambda row:row[4],reverse=True)[:8]
            + sorted(ranked,key=lambda row:row[5],reverse=True)[:8]
        ):
            side="LONG" if float(row[4])>=float(row[5]) else "SHORT"
            raw=max(float(row[4]),float(row[5]))
            prev=near.get(row[0])
            if prev is None or raw>float(prev.get("raw",0)):
                near[row[0]]={
                    "symbol":row[0],"side":side,"raw":round(raw,1),"issues":[]
                }
        d["near_candidates"]=sorted(
            near.values(),key=lambda item:float(item["raw"]),reverse=True
        )[:12]

        liquid_ranked=list(ranked)
        # Keep these diagnostics for release compatibility and Fast Radar.
        long_ranked=sorted(liquid_ranked,key=lambda row:row[4],reverse=True)
        short_ranked=sorted(liquid_ranked,key=lambda row:row[5],reverse=True)
        deep_rows=_select_deep_rows(
            liquid_ranked,DEEP_SHORTLIST,MIN_OPPOSITE_SIDE_RESERVE
        )
        d["deep_checked"]=len(deep_rows)
        d["deep_shortlist_target"]=DEEP_SHORTLIST
        d["full_universe_ranked"]=len(primary_rows)
        d["multiframe_ranked"]=len(liquid_ranked)
        d["deep_long"]=sum(float(row[4])>=float(row[5]) for row in deep_rows)
        d["deep_short"]=sum(float(row[4])<float(row[5]) for row in deep_rows)
        d["ticker_screened_all"]=len(symbols)
        d["non_actionable_low_liquidity"]=max(0,len(symbols)-len(liquid_ranked))
        _last[kind]=copy.deepcopy(d)

        # Stage 2A: ALL 36 names receive a low-cost derivatives screen.
        # This is ranking only; no trade can bypass the full production snapshot.
        screened,screen_diag=await quick_deep_screen(deep_rows,tickers)
        d["deep_screen"]=screen_diag
        d["deep_screen_complete"]=int(screen_diag.get("complete",0) or 0)
        d["deep_screen_coverage"]=float(screen_diag.get("coverage",0) or 0)
        _last[kind]=copy.deepcopy(d)
        if d["deep_screen_coverage"] < MIN_SCREEN_COVERAGE:
            raise RuntimeError(
                f"fast derivatives screen coverage incomplete: "
                f"{d['deep_screen_complete']}/{len(deep_rows)}"
            )

        # Stage 2B: only the strongest screened names receive the expensive
        # 9-component production snapshot. Direction allocation is adaptive.
        full_selected=select_full_deep(screened,FULL_DEEP_TARGET,3)
        full_rows=[row for meta,row in full_selected]
        # UI funnel uses deep_checked as the expensive/full-deep count. The
        # 36-name fast screen is reported separately to avoid claiming all 36
        # received the 9-component production snapshot.
        d["deep_screen_candidates"]=len(deep_rows)
        d["prefiltered"]=len(deep_rows)
        d["deep_checked"]=len(full_rows)
        d["deep_full_target"]=len(full_rows)
        d["deep_full_symbols"]=[row[0] for row in full_rows]
        d["deep_full_long"]=sum(float(row[4])>=float(row[5]) for row in full_rows)
        d["deep_full_short"]=sum(float(row[4])<float(row[5]) for row in full_rows)
        _last[kind]=copy.deepcopy(d)

        deep_sem=asyncio.Semaphore(DEEP_CONCURRENCY)
        task_map={
            asyncio.create_task(
                _deep_one(row,kind,analysis_state,news,adl_risks,min_score,deep_sem)
            ): idx
            for idx,row in enumerate(full_rows)
        }
        # Preserve a small tail for final ranking/correlation/UI.
        deep_timeout=max(1.0,remaining(8.0))
        done,pending=await asyncio.wait(
            task_map.keys(),timeout=deep_timeout
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending,return_exceptions=True)
        d["deep_deadline_cancelled"]=len(pending)

        results=[None]*len(full_rows)
        for task in done:
            idx=task_map[task]
            try:
                results[idx]=task.result()
            except Exception as exc:
                results[idx]=(
                    None,f"ERROR:{type(exc).__name__}",{"_error":str(exc)}
                )
        for task in pending:
            results[task_map[task]]=(
                None,"SCAN_DEADLINE",
                {"_error":"deep check cancelled at full-scan deadline"},
            )

        live=[]
        frames_for_corr={}
        for idx,(row,outcome) in enumerate(zip(full_rows,results),1):
            signal,reason,payload=outcome
            if reason=="DERIVATIVES_INCOMPLETE":
                d["derivatives_incomplete"]+=1
            elif reason=="SCAN_DEADLINE":
                pass
            elif reason.startswith("ERROR:"):
                d["deep_errors"]+=1
            elif not reason:
                d["deep_complete"]+=1
            d["rejections"][reason or "PASS"]=int(
                d["rejections"].get(reason or "PASS",0)
            )+1
            if signal is None and isinstance(payload,dict):
                sa=dict(payload.get("_strategy_audit") or {})
                if sa and len(d.get("deep_rejections",[]))<8:
                    d.setdefault("deep_rejections",[]).append({
                        "symbol":str(sa.get("symbol") or row[0]),
                        "side":str(sa.get("side") or "?"),
                        "raw":float(sa.get("raw",0) or 0),
                        "issues":list(sa.get("issues") or []),
                        "geometry_recovered":bool(sa.get("geometry_recovered")),
                    })
            if signal is not None:
                if isinstance(payload,dict) and payload.get("_v11210_fallback"):
                    d["momentum_fallback"]=int(d.get("momentum_fallback",0))+1
                signal=_decorate(signal,row[4],row[5],idx,len(full_rows))
                live.append(signal)
                frames_for_corr[row[0]]=row[2]
            if idx % 6 == 0:
                _last[kind]=copy.deepcopy(d)

        # If deadline prevented a material part of deep shortlist from being
        # checked, report an incomplete scan rather than "no signals".
        completed_deep=len(full_rows)-len(pending)
        d["deep_completed_or_rejected"]=completed_deep
        deep_coverage=completed_deep/max(1,len(full_rows))
        d["deep_coverage"]=round(deep_coverage,4)
        if pending and deep_coverage < .70:
            raise RuntimeError(
                f"deep shortlist deadline coverage incomplete: "
                f"{completed_deep}/{len(full_rows)}"
            )

        live.sort(
            key=lambda signal:(
                _f(getattr(signal,"score",0)),
                max(
                    _f(getattr(signal,"deep_soft_long",0)),
                    _f(getattr(signal,"deep_soft_short",0)),
                ),
            ),
            reverse=True,
        )
        try:
            annotate_correlation_clusters(live,frames_for_corr)
        except Exception:
            pass

        if neutral_mode:
            limit=max(NEUTRAL_REGIME_MAX_SIGNALS*3,9)
        else:
            limit=MAX_RETURN_CANDIDATES
        final=live[:limit]
        d["final"]=len(final)
        d["long_final"]=sum(
            1 for signal in final if str(signal.side).upper()=="LONG"
        )
        d["short_final"]=sum(
            1 for signal in final if str(signal.side).upper()=="SHORT"
        )
        d["elapsed_sec"]=round(time.monotonic()-started,2)
        _finish(d,"ok",d.get("reason",""))
        return final
    except Exception as exc:
        _finish(d,"error",exc)
        raise


async def scan():
    return await _run("main")


async def scan_short():
    return await _run("short")
