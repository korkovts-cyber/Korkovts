"""Broad Binance Spot scanner for 3–10 day long-only opportunities."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
from datetime import datetime,timezone

import numpy as np

from app.news import get_news_sentiment
from app.market import get_symbols as futures_symbols, get_derivatives_snapshot

from spot_indicators import enrich,last_features
from spot_market import universe,tickers_24h,klines,depth,agg_trades
from spot_microstructure import analyze_book
from spot_orderbook import snapshot as local_book_snapshot, stability as local_book_stability
from spot_news import assess as assess_news
from spot_strategy import preliminary_score,analyze,normalize,derivatives_crowding
from v1170_evidence import spot as spot_evidence_audit
from v11100_data import validate_spot_frame

log=logging.getLogger(__name__)

MIN_SPOT_QUOTE_VOLUME=float(os.getenv("SPOT_MIN_24H_QUOTE_VOLUME","5000000"))
DAILY_PREFILTER_LIMIT=max(20,min(80,int(os.getenv("SPOT_PREFILTER_LIMIT","36"))))
DEEP_LIMIT=max(6,min(20,int(os.getenv("SPOT_DEEP_LIMIT","12"))))
FINAL_LIMIT=max(1,min(4,int(os.getenv("SPOT_FINAL_LIMIT","4"))))

_last={"status":"idle","reason":"","started_at":None,"finished_at":None,
       "liquid":0,"daily_ok":0,"prefiltered":0,"deep_checked":0,
       "buy":0,"watch":0,"regime":"UNKNOWN","breadth":0.0,"dispersion_7d":0.0,
       "errors":0,"daily_errors":0,"deep_errors":0,"futures_overlay_ok":False,"near":[]}
_lock=asyncio.Lock()


def status():
    return dict(_last)


def _percentiles(values):
    """Tie-aware cross-sectional percentile; equal momentum gets equal rank."""
    if not values:
        return {}
    ordered=sorted(values.items(),key=lambda x:(float(x[1]),x[0]))
    n=len(ordered)
    if n==1:
        return {ordered[0][0]:50.0}
    result={}; i=0
    while i<n:
        j=i+1; value=float(ordered[i][1])
        while j<n and float(ordered[j][1])==value:
            j+=1
        avg_rank=(i+j-1)/2.0
        pct=100.0*avg_rank/(n-1)
        for k in range(i,j):
            result[ordered[k][0]]=pct
        i=j
    return result


def _futures_counterpart(spot_symbol,fut_set):
    """Resolve exact or multiplier-prefixed USD-M counterpart (e.g. 1000PEPEUSDT)."""
    spot=str(spot_symbol or "").upper()
    futures={str(s or "").upper() for s in (fut_set or ()) if s}
    if spot in futures:
        return spot
    candidates=[]
    for symbol in futures:
        if symbol.endswith(spot):
            prefix=symbol[:-len(spot)]
            if prefix.isdigit():
                candidates.append((len(prefix),int(prefix),symbol))
    return min(candidates)[2] if candidates else None




async def fresh_derivatives_risk(symbol):
    """Fresh USD-M crowding overlay for a Spot symbol.

    A missing Futures counterpart is normal. Failure to load the Futures symbol
    universe or a known counterpart snapshot is degraded and blocks a new BUY.
    """
    symbol=str(symbol or "").upper()
    try:
        fut_set=set(await futures_symbols())
    except Exception as exc:
        return derivatives_crowding({
            "available":False,"counterpart":True,"degraded":True,
            "reason":f"futures symbol universe unavailable: {type(exc).__name__}",
        })
    if not fut_set:
        return derivatives_crowding({
            "available":False,"counterpart":True,"degraded":True,
            "reason":"futures symbol universe empty",
        })
    counterpart=_futures_counterpart(symbol,fut_set)
    if not counterpart:
        return derivatives_crowding(
            {"available":False,"counterpart":False,"degraded":False}
        )
    try:
        d=await asyncio.wait_for(get_derivatives_snapshot(counterpart),timeout=14)
    except Exception as exc:
        return derivatives_crowding({
            "available":False,"counterpart":True,"degraded":True,
            "counterpart_symbol":counterpart,
            "reason":f"{type(exc).__name__}: {exc}",
        })
    if not d or int(d.get("data_quality",0) or 0)<6:
        return derivatives_crowding({
            "available":False,"counterpart":True,"degraded":True,
            "counterpart_symbol":counterpart,
            "reason":"derivatives data quality below 6",
        })
    return derivatives_crowding({
        "available":True,"counterpart":True,"degraded":False,
        "counterpart_symbol":counterpart,**d,
    })


async def _daily(symbol):
    try:
        frame=await klines(symbol,"1d",240)
        # A genuinely young listing is a normal eligibility skip, not an API error.
        if len(frame)<220:
            return None,None
        coherence=validate_spot_frame(frame,"1d",f"{symbol} daily")
        if coherence.observable and not coherence.ok:
            return None,f"{symbol}: market-data coherence failed: {coherence.reason}"
        feat=last_features(frame)
        if not feat:
            return None,None
        return (symbol,frame,feat),None
    except Exception as exc:
        log.warning("spot daily failed %s: %s",symbol,exc)
        return None,f"{symbol}: {type(exc).__name__}: {exc}"


def _market_state(rows,btc_frame):
    b=enrich(btc_frame).iloc[-1]
    close=float(b.close)
    bull=close>float(b.ema200) and float(b.ema20)>float(b.ema50)
    bear=close<float(b.ema200) and float(b.ema20)<float(b.ema50)
    eligible=list(rows.values())
    breadth=(sum(1 for r in eligible if float(r["feat"].get("ret7") or 0)>0
                 and float(r["feat"].get("close") or 0)>float(r["feat"].get("ema50") or 0))
             /max(1,len(eligible)))
    r7=[float(r["feat"].get("ret7") or 0) for r in eligible]
    dispersion=float(np.std(r7)) if r7 else 0.0
    if bear or breadth<.25:
        regime="BEAR"
    elif bull and breadth>=.45:
        regime="BULL"
    else:
        regime="NEUTRAL"
    btc_ret7=float(b.ret7); btc_ret14=float(b.ret14); btc_ret30=float(b.ret30)
    risk_off=(btc_ret14<=-5.0 or btc_ret7<=-4.0 or breadth<.35)
    return {
        "regime":regime,"breadth":breadth,"dispersion_7d":dispersion,
        # Extreme dispersion and a weak BTC/breadth tape are separate risk flags.
        "dispersion_risk":dispersion>=18.0,
        "risk_off":bool(risk_off),
        "btc_ret7":btc_ret7,"btc_ret14":btc_ret14,"btc_ret30":btc_ret30,
    }



def _corr30(frame_a,frame_b):
    """30D close-to-close correlation on aligned Spot daily bars."""
    try:
        a=frame_a[["close_time","close"]].copy().rename(columns={"close":"a"})
        b=frame_b[["close_time","close"]].copy().rename(columns={"close":"b"})
        m=a.merge(b,on="close_time",how="inner").tail(45)
        if len(m)<22:
            return 0.0
        ra=m["a"].pct_change(); rb=m["b"].pct_change()
        valid=np.isfinite(ra.to_numpy()) & np.isfinite(rb.to_numpy())
        if int(valid.sum())<20:
            return 0.0
        corr=float(np.corrcoef(ra.to_numpy()[valid],rb.to_numpy()[valid])[0,1])
        return corr if np.isfinite(corr) else 0.0
    except Exception:
        return 0.0


def _portfolio_diversify(signals,rows,threshold=.90):
    """Keep only the strongest simultaneous BUY inside a highly correlated cluster."""
    leaders=[]
    ordered=sorted(signals,key=lambda s:(1 if s.status=="BUY" else 0,float(s.score)),reverse=True)
    for signal in ordered:
        if signal.status!="BUY":
            signal.feature_snapshot.setdefault("portfolio",{}).setdefault("cluster_key",signal.symbol)
            continue
        blocked=None
        for leader in leaders:
            corr=_corr30(rows[signal.symbol]["daily"],rows[leader.symbol]["daily"])
            if corr>=float(threshold):
                blocked=(leader,corr); break
        if blocked:
            leader,corr=blocked
            signal.status="WATCH"
            signal.risks.append(
                f"portfolio: 30D correlation {corr:.2f} with stronger {leader.symbol}; duplicate BUY withheld"
            )
            signal.feature_snapshot.setdefault("portfolio",{}).update({
                "cluster_key":leader.symbol,"correlated_with":leader.symbol,
                "corr30":corr,"buy_withheld":True,
            })
        else:
            leaders.append(signal)
            signal.feature_snapshot.setdefault("portfolio",{}).update({
                "cluster_key":signal.symbol,"buy_withheld":False,
            })
    return ordered




async def active_correlation_risk(symbol,active_symbols,threshold=.90):
    """Check a new Spot candidate against already delivered OPEN Spot positions."""
    symbol=str(symbol or "").upper()
    others=[str(x or "").upper() for x in (active_symbols or ()) if x and str(x).upper()!=symbol]
    if not others:
        return {"blocked":False,"degraded":False,"corr":0.0,"with_symbol":None}
    try:
        base_frame=await klines(symbol,"1d",70)
        if len(base_frame)<35:
            return {"blocked":False,"degraded":True,"corr":0.0,"with_symbol":None,
                    "reason":"candidate correlation history incomplete"}
        worst=(0.0,None)
        for other in dict.fromkeys(others):
            frame=await klines(other,"1d",70)
            if len(frame)<35:
                return {"blocked":False,"degraded":True,"corr":0.0,"with_symbol":other,
                        "reason":f"{other} correlation history incomplete"}
            corr=_corr30(base_frame,frame)
            if corr>worst[0]:
                worst=(corr,other)
        return {
            "blocked":bool(worst[0]>=float(threshold)),
            "degraded":False,
            "corr":float(worst[0]),
            "with_symbol":worst[1],
        }
    except Exception as exc:
        return {
            "blocked":False,"degraded":True,"corr":0.0,"with_symbol":None,
            "reason":f"{type(exc).__name__}: {exc}",
        }


async def recheck_watch(row,max_context_age_minutes=45):
    """Full symbol revalidation for a persistent WATCH candidate.

    Cross-sectional RS/market context must still come from a recent broad scan;
    all symbol-specific trend, price, L2, flow, news and crowding inputs are fresh.
    """
    try:
        updated=datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z","+00:00"))
        if updated.tzinfo is None:
            updated=updated.replace(tzinfo=timezone.utc)
        age=(datetime.now(timezone.utc)-updated).total_seconds()/60.0
        if age>float(max_context_age_minutes):
            return None,"broad relative-strength context stale; wait for next full Spot scan"
    except Exception:
        return None,"WATCH context timestamp invalid"

    symbol=str(row.get("symbol") or "").upper()
    base=str(row.get("base_asset") or symbol.removesuffix("USDT")).upper()
    try:
        market=json.loads(row.get("market_json") or "{}")
    except Exception:
        market={}
    if not market or str(market.get("regime") or "UNKNOWN")=="UNKNOWN":
        return None,"WATCH market context unavailable"

    try:
        metas,daily,f4,f1,fm,trades,news_snapshot=await asyncio.gather(
            universe(),klines(symbol,"1d",240),klines(symbol,"4h",300),
            klines(symbol,"1h",300),klines(symbol,"1m",90),
            agg_trades(symbol,1000),get_news_sentiment(),
        )
        if len(daily)<220 or len(f4)<120 or len(f1)<80 or len(fm)<30:
            return None,"fresh WATCH history incomplete"
        coherence=[
            validate_spot_frame(daily,"1d","watch daily"),
            validate_spot_frame(f4,"4h","watch 4h"),
            validate_spot_frame(f1,"1h","watch 1h"),
            validate_spot_frame(fm,"1m","watch 1m"),
        ]
        bad=[x.reason for x in coherence if x.observable and not x.ok]
        if bad:
            return None,"market-data coherence failed: "+"; ".join(bad)
        meta=next((m for m in metas if m.symbol==symbol),None)
        if meta is None:
            return None,"symbol no longer eligible in Binance Spot universe"
        book=local_book_snapshot(symbol,3.0,100)
        ob_health=local_book_stability(symbol,3.0)
        if book is None or not ob_health.get("healthy"):
            return None,f"local order book not ready: {ob_health.get('reason','unsynchronised')}"
        micro=analyze_book(book,trades,minute_frame=fm)
        news=assess_news(news_snapshot,base)
        try:
            fut_set=set(await futures_symbols())
            overlay_ok=bool(fut_set)
        except Exception:
            fut_set=set(); overlay_ok=False
        counterpart=_futures_counterpart(symbol,fut_set) if overlay_ok else None
        if not overlay_ok:
            derivatives={"available":False,"counterpart":True,"degraded":True,
                         "reason":"futures symbol universe unavailable"}
        elif counterpart:
            derivatives={"available":False,"counterpart":True,"degraded":True,
                         "counterpart_symbol":counterpart}
            try:
                d=await asyncio.wait_for(get_derivatives_snapshot(counterpart),timeout=14)
                if d and d.get("data_quality",0)>=6:
                    derivatives={"available":True,"counterpart":True,"degraded":False,
                                 "counterpart_symbol":counterpart,**d}
            except Exception as exc:
                log.debug("watch derivatives-risk unavailable %s: %s",symbol,exc)
        else:
            derivatives={"available":False,"counterpart":False,"degraded":False}
        signal=analyze(
            symbol,base,daily,f4,f1,float(row.get("relative_percentile") or 50),
            float(row.get("excess_btc_14d") or 0),market,news,micro,derivatives
        )
        if signal is None:
            return None,"fresh full WATCH revalidation rejected candidate"
        normalized=normalize(signal,meta)
        if normalized is None:
            return None,"fresh WATCH exchange geometry invalid"
        evidence=spot_evidence_audit(normalized)
        evidence_penalty=max(0.0,(6-int(evidence.support))*1.5+int(evidence.conflict)*2.0)
        normalized.feature_snapshot.setdefault("evidence_v117",{})["score_penalty"]=evidence_penalty
        if evidence_penalty>0:
            normalized.score=max(0.0,float(normalized.score)-evidence_penalty)
        required=float((normalized.feature_snapshot or {}).get("required_score",82) or 82)
        if normalized.status=="BUY" and (not evidence.eligible or normalized.score<required):
            normalized.status="WATCH"
            normalized.risks.append(f"independent evidence gate: {evidence.summary}")
        # Preserve cluster identity from the broad scan so a duplicate cluster
        # cannot silently promote through the watchtower.
        try:
            old_feat=json.loads(row.get("feature_json") or "{}")
            old_portfolio=dict(old_feat.get("portfolio") or {})
        except Exception:
            old_portfolio={}
        if old_portfolio:
            normalized.feature_snapshot.setdefault("portfolio",{}).update(old_portfolio)
        normalized.feature_snapshot["data_coherence_v11100"]={
            "status":"GOOD" if any(x.observable for x in coherence) else "UNOBSERVABLE",
            "frames":[{
                "role":x.role,"interval":x.interval,"observable":x.observable,
                "age_sec":x.age_sec,"max_gap_sec":x.max_gap_sec,"reason":x.reason,
            } for x in coherence],
        }
        normalized.feature_snapshot["local_orderbook_v118"]={
            "healthy":bool(ob_health.get("healthy")),
            "stability_score":float(ob_health.get("stability_score",0) or 0),
            "samples":int(ob_health.get("samples",0) or 0),
            "coverage_sec":float(ob_health.get("coverage_sec",0) or 0),
            "bid_replenishment_ratio":float(ob_health.get("bid_replenishment_ratio",0) or 0),
            "median_imbalance_20bps":float(ob_health.get("median_imbalance_20bps",0) or 0),
            "event_age_sec":ob_health.get("event_age_sec"),
            "gaps":int(ob_health.get("gaps",0) or 0),
            "resyncs":int(ob_health.get("resyncs",0) or 0),
        }
        # The second BUY confirmation is allowed to wait for a stable local
        # depth stream. Broad discovery itself still uses REST.
        if normalized.status=="BUY" and float(ob_health.get("stability_score",0) or 0)<65:
            normalized.status="WATCH"
            normalized.risks.append(
                f"local order book stability only {float(ob_health.get('stability_score',0) or 0):.0f}/100"
            )
        return normalized,None
    except Exception as exc:
        log.warning("Spot WATCH recheck failed %s: %s",symbol,exc)
        return None,f"{type(exc).__name__}: {exc}"


async def _deep(symbol,row,rel_pct,excess,market,news_snapshot,fut_set,futures_overlay_ok):
    try:
        f4,f1,fm=await asyncio.gather(
            klines(symbol,"4h",300),klines(symbol,"1h",300),klines(symbol,"1m",90)
        )
        if len(f4)<120 or len(f1)<80 or len(fm)<30:
            return None,f"{symbol}: insufficient closed 4H/1H/1m history"
        coherence=[
            validate_spot_frame(row["daily"],"1d",f"{symbol} daily"),
            validate_spot_frame(f4,"4h",f"{symbol} 4h"),
            validate_spot_frame(f1,"1h",f"{symbol} 1h"),
            validate_spot_frame(fm,"1m",f"{symbol} 1m"),
        ]
        bad=[x.reason for x in coherence if x.observable and not x.ok]
        if bad:
            return None,f"{symbol}: market-data coherence failed: "+"; ".join(bad)
        x4=enrich(f4).iloc[-1]
        # Second-stage timing gate before expensive depth/derivatives requests.
        if not (float(x4.close)>float(x4.ema50) and float(x4.ema20)>=float(x4.ema50)*.995):
            return None,None
        book,trades=await asyncio.gather(depth(symbol,100),agg_trades(symbol,1000))
        micro=analyze_book(book,trades,minute_frame=fm)
        base=row["meta"].base_asset
        news=assess_news(news_snapshot,base)
        counterpart=_futures_counterpart(symbol,fut_set) if futures_overlay_ok else None
        if not futures_overlay_ok:
            # We cannot know whether this Spot pair has a USD-M counterpart, so
            # strict mode downgrades BUY to WATCH instead of pretending N/A.
            derivatives={"available":False,"counterpart":True,"degraded":True,
                         "reason":"futures symbol universe unavailable"}
        elif counterpart:
            derivatives={"available":False,"counterpart":True,"degraded":True,
                         "counterpart_symbol":counterpart}
            try:
                d=await asyncio.wait_for(get_derivatives_snapshot(counterpart),timeout=14)
                if d and d.get("data_quality",0)>=6:
                    derivatives={"available":True,"counterpart":True,"degraded":False,
                                 "counterpart_symbol":counterpart,**d}
            except Exception as exc:
                log.debug("spot derivatives-risk unavailable %s via %s: %s",symbol,counterpart,exc)
        else:
            derivatives={"available":False,"counterpart":False,"degraded":False}
        signal=analyze(
            symbol,base,row["daily"],f4,f1,rel_pct,excess,market,news,micro,derivatives
        )
        if signal is None:
            return None,None
        normalized=normalize(signal,row["meta"])
        if normalized is None:
            return None,None
        evidence=spot_evidence_audit(normalized)
        evidence_penalty=max(0.0,(6-int(evidence.support))*1.5+int(evidence.conflict)*2.0)
        normalized.feature_snapshot.setdefault("evidence_v117",{})["score_penalty"]=evidence_penalty
        if evidence_penalty>0:
            normalized.score=max(0.0,float(normalized.score)-evidence_penalty)
        required=float((normalized.feature_snapshot or {}).get("required_score",82) or 82)
        if normalized.status=="BUY" and (not evidence.eligible or normalized.score<required):
            normalized.status="WATCH"
            normalized.risks.append(f"independent evidence gate: {evidence.summary}")
        normalized.feature_snapshot["data_coherence_v11100"]={
            "status":"GOOD" if any(x.observable for x in coherence) else "UNOBSERVABLE",
            "frames":[{
                "role":x.role,"interval":x.interval,"observable":x.observable,
                "age_sec":x.age_sec,"max_gap_sec":x.max_gap_sec,"reason":x.reason,
            } for x in coherence],
        }
        return normalized,None
    except Exception as exc:
        log.warning("spot deep failed %s: %s",symbol,exc)
        return None,f"{symbol}: {type(exc).__name__}: {exc}"


async def scan(force=False):
    if _lock.locked():
        raise RuntimeError("Spot scan already running")
    async with _lock:
        _last.update(status="running",reason="",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,liquid=0,daily_ok=0,prefiltered=0,deep_checked=0,buy=0,watch=0,
                     errors=0,daily_errors=0,deep_errors=0,futures_overlay_ok=False,near=[])
        try:
            metas,tickers,news_snapshot=await asyncio.gather(universe(),tickers_24h(force=force),get_news_sentiment())
            liquid=[m for m in metas if tickers.get(m.symbol,{}).get("quote_volume",0)>=MIN_SPOT_QUOTE_VOLUME]
            liquid.sort(key=lambda m:tickers[m.symbol]["quote_volume"],reverse=True)
            _last["liquid"]=len(liquid)
            if not liquid:
                raise RuntimeError("no liquid Binance Spot USDT symbols")

            daily_results=await asyncio.gather(*(_daily(m.symbol) for m in liquid))
            by_meta={m.symbol:m for m in liquid}
            rows={}; daily_errors=[]
            for result,error in daily_results:
                if error:
                    daily_errors.append(error)
                if not result:
                    continue
                symbol,frame,feat=result
                rows[symbol]={"meta":by_meta[symbol],"daily":frame,"feat":feat}
            _last["daily_ok"]=len(rows)
            _last["daily_errors"]=len(daily_errors)
            # Broad data loss changes the cross-sectional ranking itself. Do not
            # call a partial universe "NO EDGE" or rank it as if complete.
            if liquid and len(daily_errors)>max(5,int(len(liquid)*.20)):
                raise RuntimeError(
                    f"Spot daily data degraded for {len(daily_errors)}/{len(liquid)} symbols: "
                    +"; ".join(daily_errors[:3])
                )
            if "BTCUSDT" not in rows:
                raise RuntimeError("BTCUSDT Spot daily state unavailable")

            market=_market_state(rows,rows["BTCUSDT"]["daily"])
            _last.update(regime=market["regime"],breadth=market["breadth"],dispersion_7d=market["dispersion_7d"])
            btc14=float(rows["BTCUSDT"]["feat"].get("ret14") or 0)
            momentum={
                s:float(r["feat"].get("ret7") or 0)+.65*float(r["feat"].get("ret14") or 0)+.25*float(r["feat"].get("ret30") or 0)
                for s,r in rows.items()
            }
            rel=_percentiles(momentum)

            prelim=[]
            for symbol,row in rows.items():
                if symbol=="BTCUSDT":
                    # BTC can still be recommended, but it competes fairly rather than
                    # being excluded from the relative-strength universe.
                    pass
                feat=row["feat"]
                excess=float(feat.get("ret14") or 0)-btc14
                p=preliminary_score(feat,rel.get(symbol,50),excess)
                # Reject obvious deteriorating/pump structures before deep data.
                if float(feat.get("ret30") or 0)<-8:
                    continue
                if float(feat.get("max_day14") or 0)>22:
                    continue
                if float(feat.get("close") or 0)<=float(feat.get("ema100") or 0):
                    continue
                prelim.append((p,symbol,excess))
            prelim.sort(reverse=True)
            prelim=prelim[:DAILY_PREFILTER_LIMIT]
            _last["prefiltered"]=len(prelim)
            _last["near"]=[{"symbol":s,"pre":round(p,1),"rel":round(rel.get(s,50),1),"excess":round(ex,1)} for p,s,ex in prelim[:5]]

            try:
                fut_set=set(await futures_symbols())
                futures_overlay_ok=bool(fut_set)
            except Exception as exc:
                log.warning("Spot futures-universe overlay unavailable: %s",exc)
                fut_set=set(); futures_overlay_ok=False
            _last["futures_overlay_ok"]=bool(futures_overlay_ok)
            deep_candidates=prelim[:DEEP_LIMIT]
            _last["deep_checked"]=len(deep_candidates)
            found=await asyncio.gather(*(
                _deep(s,rows[s],rel.get(s,50),ex,market,news_snapshot,fut_set,futures_overlay_ok)
                for _,s,ex in deep_candidates
            ))
            deep_errors=[err for _,err in found if err]
            _last["deep_errors"]=len(deep_errors)
            _last["errors"]=int(_last.get("daily_errors",0))+len(deep_errors)
            if deep_candidates and len(deep_errors)>=max(2,(len(deep_candidates)+1)//2):
                sample="; ".join(deep_errors[:3])
                raise RuntimeError(
                    f"Spot deep data degraded for {len(deep_errors)}/{len(deep_candidates)} candidates: {sample}"
                )
            final=[signal for signal,err in found if signal is not None]
            final=_portfolio_diversify(final,rows,.90)
            final.sort(key=lambda s:(1 if s.status=="BUY" else 0,s.score),reverse=True)
            _last["buy"]=sum(1 for x in final if x.status=="BUY")
            _last["watch"]=sum(1 for x in final if x.status=="WATCH")
            _last.update(status="ok",finished_at=datetime.now(timezone.utc).isoformat())
            return final[:FINAL_LIMIT]
        except Exception as exc:
            _last.update(status="error",reason=f"{type(exc).__name__}: {exc}",finished_at=datetime.now(timezone.utc).isoformat())
            raise
