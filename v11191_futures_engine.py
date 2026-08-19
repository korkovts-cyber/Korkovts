"""Korkovts V11.19.3 · CODE-QUALITY AUDITED FULL-UNIVERSE FUTURES ENGINE.

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
from app.liquidations import snapshot as liquidation_snapshot
from app.market import (
    get_adl_risks,
    get_klines,
    get_symbols,
    get_tickers,
)
from app.research import annotate_correlation_clusters
import app.scanner as legacy

FULL_SCAN_BUDGET_SEC = int(os.getenv("V11190_FULL_SCAN_BUDGET_SEC", "175"))
DEEP_CONCURRENCY = max(1, min(5, int(os.getenv("V11191_DEEP_CONCURRENCY", "4"))))
FRAME_CONCURRENCY = max(1, int(os.getenv("V11190_FRAME_CONCURRENCY", "6")))
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
    }


def scan_status():
    # Merge raw full-universe diagnostics with the later V11.18 Production
    # diagnostics written into app.scanner._last_scan by _prepare/_health_gate.
    merged=copy.deepcopy(_last)
    compat=copy.deepcopy(getattr(legacy,"_last_scan",{}) or {})
    for kind in ("main","short"):
        if kind in compat:
            merged.setdefault(kind,{}).update(compat[kind])
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
    # Compatibility with old UI.
    d["prefiltered"] = d.get("deep_checked", 0)
    d["deep_rejected"] = max(0, d.get("deep_checked", 0) - d.get("final", 0))
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


async def _frames(symbol, kind, sem):
    try:
        async with sem:
            if kind == "main":
                lower, base, higher = await asyncio.gather(
                    get_klines(symbol, "15m", 280),
                    get_klines(symbol, "1h", 360),
                    get_klines(symbol, "4h", 360),
                )
            else:
                lower, base, higher = await asyncio.gather(
                    get_klines(symbol, "5m", 300),
                    get_klines(symbol, "15m", 360),
                    get_klines(symbol, "1h", 360),
                )
        return symbol, lower, base, higher, None
    except Exception as exc:
        return symbol, None, None, None, exc


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
        result = legacy.analyze(
            symbol,timeframe,base,higher,float(min_score),lower,
            market_context.get("bias"),d,legacy.for_symbol(news,symbol),market_context
        )
        if result is None:
            return None, "FINAL_STRATEGY_REJECT", d

        side = str(getattr(result,"side","") or "").upper()
        penalty = max(0.0,float(calibration_penalty(symbol,side,timeframe) or 0))
        if penalty>0:
            threshold=min(95.0,float(min_score)+penalty)
            result = legacy.analyze(
                symbol,timeframe,base,higher,threshold,lower,
                market_context.get("bias"),d,legacy.for_symbol(news,symbol),market_context
            )
            if result is None:
                return None, "CALIBRATION_REJECT", d
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
    try:
        symbols, tickers, news = await asyncio.gather(
            get_symbols(), get_tickers(), legacy.get_news_sentiment()
        )
        try:
            adl_risks=await get_adl_risks()
        except Exception as exc:
            # Do not kill the whole market scan because the bulk ADL endpoint
            # failed. _deep_one will use the production symbol-level fallback.
            adl_risks={}
            d["adl_bulk_degraded"]=True
            d["adl_bulk_reason"]=f"{type(exc).__name__}: {exc}"
        d["universe"] = len(symbols)
        # Enhanced-news degradation is already handled by V11.18 as a stricter
        # threshold/telemetry state. Never resurrect the old whole-market kill
        # switch merely because auxiliary news sources temporarily report zero.
        d["news_sources"]=int((news or {}).get("sources",0) or 0)

        state = await legacy.market_state(tickers)
        analysis_state, neutral_mode = legacy.market_analysis_state(state)
        thresholds = legacy.scan_thresholds(state)
        min_score = thresholds["main"] if kind == "main" else thresholds["short_base"]
        d["regime"] = state.get("bias", "UNKNOWN")
        d["independent_mode"] = bool(analysis_state.get("independent_mode"))
        d["threshold"] = float(min_score)

        observed = [
            s for s in symbols
            if _f(tickers.get(s, {}).get("quote_volume")) >= MIN_OBSERVED_QUOTE_VOLUME
        ]
        liquid = [
            s for s in observed
            if _f(tickers.get(s, {}).get("quote_volume")) >= MIN_ACTIONABLE_QUOTE_VOLUME
        ]
        observed.sort(key=lambda s: _f(tickers.get(s, {}).get("quote_volume")), reverse=True)
        liquid_set = set(liquid)
        d["observed"] = len(observed)
        d["liquid"] = len(liquid)

        sem = asyncio.Semaphore(FRAME_CONCURRENCY)
        frame_rows = await asyncio.gather(*(_frames(s, kind, sem) for s in observed))
        btc_change = _f(tickers.get("BTCUSDT", {}).get("change"))
        ranked = []
        for symbol, lower, base, higher, err in frame_rows:
            if err is not None or base is None:
                d["frames_failed"] += 1
                if len(d["error_examples"]) < 5:
                    d["error_examples"].append(f"{symbol}: frame {err}")
                continue
            d["frames_ok"] += 1
            change = _f(tickers.get(symbol, {}).get("change"))
            sl = _soft_side_score(base, higher, lower, "LONG", change, btc_change)
            ss = _soft_side_score(base, higher, lower, "SHORT", change, btc_change)
            ranked.append((symbol, lower, base, higher, sl, ss))

        ranked.sort(key=lambda r: max(r[4], r[5]), reverse=True)
        d["top_long_watch"] = [
            {"symbol": r[0], "score": round(r[4], 1)}
            for r in sorted(ranked, key=lambda r: r[4], reverse=True)[:5]
        ]
        d["top_short_watch"] = [
            {"symbol": r[0], "score": round(r[5], 1)}
            for r in sorted(ranked, key=lambda r: r[5], reverse=True)[:5]
        ]
        # Legacy Fast Radar consumes near_candidates between full scans. Feed it
        # a balanced union of the new LONG/SHORT full-universe leaders so the
        # wider scanner also improves 60-second discovery instead of starving it.
        near={}
        for r in (
            sorted(ranked,key=lambda r:r[4],reverse=True)[:8]
            + sorted(ranked,key=lambda r:r[5],reverse=True)[:8]
        ):
            side="LONG" if float(r[4])>=float(r[5]) else "SHORT"
            raw=max(float(r[4]),float(r[5]))
            prev=near.get(r[0])
            if prev is None or raw>float(prev.get("raw",0)):
                near[r[0]]={"symbol":r[0],"side":side,"raw":round(raw,1),"issues":[]}
        d["near_candidates"]=sorted(near.values(),key=lambda x:float(x["raw"]),reverse=True)[:12]

        # FULL-UNIVERSE CONTRACT:
        # every liquid symbol already passed 3-frame LONG/SHORT discovery above.
        # Heavy Binance derivatives endpoints are applied to a WIDE balanced
        # shortlist, not to only 1-2 technical survivors and not to all ~170
        # names (which would create avoidable request-weight/rate-limit risk).
        liquid_ranked = [r for r in ranked if r[0] in liquid_set]
        long_ranked = sorted(liquid_ranked, key=lambda r:r[4], reverse=True)
        short_ranked = sorted(liquid_ranked, key=lambda r:r[5], reverse=True)
        deep_rows = _select_deep_rows(
            liquid_ranked,DEEP_SHORTLIST,MIN_OPPOSITE_SIDE_RESERVE
        )
        d["deep_checked"] = len(deep_rows)
        d["deep_shortlist_target"] = DEEP_SHORTLIST
        d["full_universe_ranked"] = len(liquid_ranked)
        d["deep_long"] = sum(float(r[4])>=float(r[5]) for r in deep_rows)
        d["deep_short"] = sum(float(r[4])<float(r[5]) for r in deep_rows)
        d["ticker_screened_all"] = len(symbols)
        d["non_actionable_low_liquidity"] = max(0,len(symbols)-len(liquid_ranked))
        deep_sem = asyncio.Semaphore(DEEP_CONCURRENCY)
        tasks = [
            _deep_one(r, kind, analysis_state, news, adl_risks, min_score, deep_sem)
            for r in deep_rows
        ]
        results = await asyncio.gather(*tasks)

        live = []
        frames_for_corr = {}
        for idx, (row, outcome) in enumerate(zip(deep_rows, results), 1):
            signal, reason, payload = outcome
            if reason == "DERIVATIVES_INCOMPLETE":
                d["derivatives_incomplete"] += 1
            elif reason.startswith("ERROR:"):
                d["deep_errors"] += 1
            elif not reason:
                d["deep_complete"] += 1
            d["rejections"][reason or "PASS"] = int(d["rejections"].get(reason or "PASS", 0)) + 1

            if signal is not None:
                signal = _decorate(signal, row[4], row[5], idx, len(deep_rows))
                live.append(signal)
                frames_for_corr[row[0]] = row[2]

            # The outer V11.18 watchdog is authoritative. Record target overruns rather
            # than cancelling already-started deep checks and biasing selection by latency.
            if time.monotonic() - started > FULL_SCAN_BUDGET_SEC:
                d["target_budget_exceeded"] = True

        # A broader candidate pool feeds V11.18 Strong/Indicator/Portfolio gates.
        # Do not throw away candidate #5/#10 before those stronger layers see it.
        live.sort(
            key=lambda s: (
                _f(getattr(s, "score", 0)),
                max(_f(getattr(s, "deep_soft_long", 0)), _f(getattr(s, "deep_soft_short", 0))),
            ),
            reverse=True,
        )
        try:
            annotate_correlation_clusters(live, frames_for_corr)
        except Exception:
            pass

        if neutral_mode:
            limit = max(NEUTRAL_REGIME_MAX_SIGNALS * 3, 9)
        else:
            limit = MAX_RETURN_CANDIDATES
        final = live[:limit]
        d["final"] = len(final)
        d["long_final"] = sum(1 for s in final if str(s.side).upper() == "LONG")
        d["short_final"] = sum(1 for s in final if str(s.side).upper() == "SHORT")
        _finish(d, "ok", d.get("reason", ""))
        return final
    except Exception as exc:
        _finish(d, "error", exc)
        raise


async def scan():
    return await _run("main")


async def scan_short():
    return await _run("short")
