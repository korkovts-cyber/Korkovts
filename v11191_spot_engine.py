"""V11.21 · CODE-QUALITY AUDITED FULL-UNIVERSE Spot signal engine.

Principles:
- every eligible Binance Spot/USDT symbol receives daily discovery;
- the whole liquid universe receives cross-sectional relative-strength ranking;
- a wide top pool receives 4H/1H/1m + Spot L2/aggTrades + news + crowding;
- BEAR is no longer a blanket ban on an independently strong recovery leader;
- unavailable auxiliary Futures/news telemetry is a penalty/uncertainty state,
  while actual negative news, extreme crowding and bad execution remain vetoes.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime, timezone

import numpy as np

import spot_scanner as legacy
import spot_strategy as strategy
import v1170_evidence as evidence_mod

log=logging.getLogger(__name__)

SPOT_DEEP_SHORTLIST=36
SPOT_FINAL_LIMIT=6
_last={
    "status":"idle","reason":"","started_at":None,"finished_at":None,
    "universe":0,"liquid":0,"daily_ok":0,"full_universe_ranked":0,
    "prefiltered":0,"deep_checked":0,"buy":0,"watch":0,"regime":"UNKNOWN",
    "breadth":0.0,"dispersion_7d":0.0,"errors":0,"daily_errors":0,
    "deep_errors":0,"futures_overlay_ok":False,"near":[],
}
_lock=asyncio.Lock()


def status():
    return copy.deepcopy(_last)


def _finite(x,default=0.0):
    try:
        v=float(x)
        return v if np.isfinite(v) else float(default)
    except Exception:
        return float(default)


def market_state(rows,btc_frame):
    """Faster transition-aware regime; slow 1D trend is risk context, not a kill switch."""
    b=legacy.enrich(btc_frame).iloc[-1]
    close=_finite(b.close)
    bull=close>_finite(b.ema200) and _finite(b.ema20)>_finite(b.ema50)
    bear=close<_finite(b.ema200) and _finite(b.ema20)<_finite(b.ema50)

    eligible=list(rows.values())
    breadth=sum(
        1 for r in eligible
        if _finite(r["feat"].get("ret7"))>0
        and _finite(r["feat"].get("close"))>_finite(r["feat"].get("ema50"))
    )/max(1,len(eligible))
    r7=[_finite(r["feat"].get("ret7")) for r in eligible]
    dispersion=float(np.std(r7)) if r7 else 0.0

    btc_ret7=_finite(b.ret7)
    btc_ret14=_finite(b.ret14)
    btc_ret30=_finite(b.ret30)

    recovery=(btc_ret7>=1.0 and breadth>=.30) or (btc_ret7>=2.5 and breadth>=.24)
    broad_bull=bull and breadth>=.42
    broad_bear=bear and breadth<.22 and btc_ret7<0

    if broad_bull:
        regime="BULL"
    elif broad_bear and not recovery:
        regime="BEAR"
    else:
        regime="NEUTRAL"

    # risk_off now requires current deterioration. A stale 14D drawdown alone
    # cannot veto a market that is already recovering on 7D breadth/price.
    risk_off=bool(
        (btc_ret7<=-4.0 and breadth<.35)
        or (btc_ret14<=-7.0 and btc_ret7<0 and breadth<.30)
    )
    return {
        "regime":regime,"breadth":breadth,"dispersion_7d":dispersion,
        "dispersion_risk":dispersion>=20.0,
        "risk_off":risk_off,
        "recovery":bool(recovery),
        "btc_ret7":btc_ret7,"btc_ret14":btc_ret14,"btc_ret30":btc_ret30,
    }


def _independent_strength(daily,frame4h,frame1h,rp,excess):
    try:
        d=legacy.enrich(daily).iloc[-1]
        h4=legacy.enrich(frame4h).iloc[-1]
        h1=legacy.enrich(frame1h).iloc[-1]
        confirmations=0
        confirmations+=_finite(d.close)>_finite(d.ema100)
        confirmations+=_finite(d.ret14)>0
        confirmations+=_finite(d.ret30)>0
        confirmations+=_finite(d.path_eff14)>=.25
        confirmations+=_finite(h4.close)>_finite(h4.ema50)
        confirmations+=_finite(h4.ema20)>=_finite(h4.ema50)*.99
        confirmations+=_finite(h1.close)>_finite(h1.ema20)
        confirmations+=_finite(h1.macd_hist)>=0
        return bool(float(rp)>=88 and float(excess)>=-1.0 and confirmations>=6)
    except Exception:
        return False


def analyze(symbol,base_asset,daily,frame4h,frame1h,relative_percentile,excess_btc_14d,
            market,news,micro,derivatives=None):
    """Compatibility wrapper around proven Spot strategy with duplicate-veto removal."""
    m=dict(market or {})
    n=dict(news or {})
    d=dict(derivatives or {})

    independent=_independent_strength(
        daily,frame4h,frame1h,relative_percentile,excess_btc_14d
    )
    if independent and m.get("regime")=="BEAR":
        m["original_regime"]="BEAR"
        m["regime"]="NEUTRAL"
        m["independent_recovery"]=True
        # Local leadership may overcome stale global trend, but not a genuinely
        # current risk-off tape.
        if bool(m.get("recovery")):
            m["risk_off"]=False

    # Auxiliary-data degradation is not directional evidence. Preserve actual
    # negative events/crowding, but do not turn a timeout into a BUY veto.
    if n.get("degraded") and not (
        n.get("block") or n.get("recent_negative") or n.get("global_breaking")
    ):
        n["degraded_original"]=True
        n["degraded"]=False
        n["adjustment"]=int(n.get("adjustment",0) or 0)-2

    if d.get("degraded") and not d.get("available"):
        d={
            "available":False,"counterpart":False,"degraded":False,
            "auxiliary_degraded":True,
            "reason":str(d.get("reason") or "futures crowding unavailable"),
        }

    result=strategy._v11191_original_analyze(
        symbol,base_asset,daily,frame4h,frame1h,relative_percentile,excess_btc_14d,
        m,n,micro,d
    )
    if result is None:
        return None

    result.feature_snapshot.setdefault("spot_v11191",{}).update({
        "independent_strength":bool(independent),
        "original_market_regime":str((market or {}).get("regime","UNKNOWN")),
        "effective_market_regime":str(m.get("regime","UNKNOWN")),
        "news_auxiliary_degraded":bool((news or {}).get("degraded") and not n.get("degraded")),
        "crowding_auxiliary_degraded":bool((derivatives or {}).get("degraded") and not d.get("degraded")),
    })
    if independent:
        result.reasons.append("V11.21 independent recovery leader")

    if str(getattr(result,"status","")).upper()=="WATCH":
        snap=dict(getattr(result,"feature_snapshot",{}) or {})
        market_snap=dict(snap.get("market") or {})
        micro_snap=dict(snap.get("micro") or {})
        news_snap=dict(snap.get("news") or {})
        crowd_snap=dict(snap.get("derivatives") or {})
        daily_snap=dict(snap.get("daily") or {})
        h4_snap=dict(snap.get("4h") or {})
        live=_finite(snap.get("live_price"))
        in_zone=float(result.entry_low)<=live<=float(result.entry_high)
        regime=str(market_snap.get("regime") or result.market_regime).upper()
        trend4=_finite(h4_snap.get("ema20"))>_finite(h4_snap.get("ema50"))
        no_hard=(
            regime!="BEAR"
            and not bool(market_snap.get("risk_off"))
            and not bool(market_snap.get("dispersion_risk"))
            and not bool(news_snap.get("block"))
            and not bool(news_snap.get("recent_negative"))
            and not bool(news_snap.get("global_breaking"))
            and not bool(crowd_snap.get("extreme"))
            and bool(micro_snap.get("healthy"))
            and _finite(micro_snap.get("spread_bps"),999)<=6.0
            and _finite(micro_snap.get("impact_5k_bps"),999)<=15.0
            and _finite(micro_snap.get("book_imbalance_20bps"))>=-.30
            and _finite(micro_snap.get("buy_share"),.5)>=.50
            and _finite(micro_snap.get("closed_buy_share_15m"),.5)>=.49
        )
        quality=(
            float(result.score)>=78.0
            and float(result.relative_percentile)>=70.0
            and _finite(daily_snap.get("ret14"))>0
            and _finite(daily_snap.get("ret30"))>0
            and _finite(daily_snap.get("path_eff14"))>=.25
            and trend4 and in_zone
        )
        if no_hard and quality:
            result.status="BUY"
            result.reasons.append("V11.21 BUY READY: trend + zone + execution + non-opposing flow")
            result.feature_snapshot.setdefault("spot_v11210",{}).update({
                "buy_ready_relief":True,"rule":"strong WATCH in exact zone; no hard conflict",
            })
    return result


def spot_evidence(signal):
    """Neutralize only duplicated *degraded/regime* vetoes; real conflicts stay hard."""
    audit=evidence_mod._v11191_original_spot(signal)
    snap=dict(getattr(signal,"feature_snapshot",{}) or {})
    ext=dict(snap.get("spot_v11191") or {})
    independent=bool(ext.get("independent_strength"))
    new=[]
    for fam in audit.families:
        state=fam.state
        hard=fam.hard
        detail=fam.detail
        if fam.name=="market regime" and independent and state=="CONFLICT":
            state="NEUTRAL"; hard=False
            detail+=" · independent recovery leader"
        if fam.name=="news/event" and ext.get("news_auxiliary_degraded") and state=="CONFLICT":
            state="NEUTRAL"; hard=False
            detail="auxiliary news telemetry degraded; no negative event detected"
        if fam.name=="crowding" and ext.get("crowding_auxiliary_degraded") and state=="CONFLICT":
            state="NEUTRAL"; hard=False
            detail="auxiliary futures crowding unavailable; Spot data remains primary"
        new.append(evidence_mod.Family(fam.name,state,detail,hard))

    support=sum(x.state=="SUPPORT" for x in new)
    neutral=sum(x.state=="NEUTRAL" for x in new)
    conflict=sum(x.state=="CONFLICT" for x in new)
    hard_conflicts=tuple(
        f"{x.name}: {x.detail}" for x in new if x.state=="CONFLICT" and x.hard
    )
    min_support=3 if independent else 4
    eligible=(not hard_conflicts and support>=min_support and conflict<=1)
    summary=f"{support} independent families support · {conflict} conflict"
    out=evidence_mod.Audit(
        eligible,support,neutral,conflict,hard_conflicts,tuple(new),summary
    )
    signal.feature_snapshot.setdefault("evidence_v117",{}).update({
        "eligible":out.eligible,"support":out.support,"neutral":out.neutral,
        "conflict":out.conflict,"hard_conflicts":list(out.hard_conflicts),
        "families":[x.__dict__ for x in out.families],"summary":out.summary,
        "v11191_rebalanced":True,
    })
    return out


async def _deep(symbol,row,rel_pct,excess,market,news_snapshot,fut_set,futures_overlay_ok):
    """No second-stage 4H kill switch before L2/flow/derivatives."""
    try:
        f4,f1,fm=await asyncio.gather(
            legacy.klines(symbol,"4h",300),
            legacy.klines(symbol,"1h",300),
            legacy.klines(symbol,"1m",90),
        )
        if len(f4)<120 or len(f1)<80 or len(fm)<30:
            return None,f"{symbol}: insufficient closed 4H/1H/1m history"

        coherence=[
            legacy.validate_spot_frame(row["daily"],"1d",f"{symbol} daily"),
            legacy.validate_spot_frame(f4,"4h",f"{symbol} 4h"),
            legacy.validate_spot_frame(f1,"1h",f"{symbol} 1h"),
            legacy.validate_spot_frame(fm,"1m",f"{symbol} 1m"),
        ]
        bad=[x.reason for x in coherence if x.observable and not x.ok]
        if bad:
            return None,f"{symbol}: market-data coherence failed: "+"; ".join(bad)

        book,trades=await asyncio.gather(
            legacy.depth(symbol,100),legacy.agg_trades(symbol,1000)
        )
        micro=legacy.analyze_book(book,trades,minute_frame=fm)
        base=row["meta"].base_asset
        news=legacy.assess_news(news_snapshot,base)

        counterpart=legacy._futures_counterpart(symbol,fut_set) if futures_overlay_ok else None
        if not futures_overlay_ok:
            derivatives={
                "available":False,"counterpart":True,"degraded":True,
                "reason":"futures symbol universe unavailable",
            }
        elif counterpart:
            derivatives={
                "available":False,"counterpart":True,"degraded":True,
                "counterpart_symbol":counterpart,
            }
            try:
                raw=await asyncio.wait_for(
                    legacy.get_derivatives_snapshot(counterpart),timeout=14
                )
                if raw and int(raw.get("data_quality",0) or 0)>=6:
                    derivatives={
                        "available":True,"counterpart":True,"degraded":False,
                        "counterpart_symbol":counterpart,**raw,
                    }
            except Exception as exc:
                derivatives["reason"]=f"{type(exc).__name__}: {exc}"
        else:
            derivatives={"available":False,"counterpart":False,"degraded":False}

        signal=analyze(
            symbol,base,row["daily"],f4,f1,rel_pct,excess,
            market,news,micro,derivatives
        )
        if signal is None:
            return None,None

        normalized=legacy.normalize(signal,row["meta"])
        if normalized is None:
            return None,None

        audit=spot_evidence(normalized)
        penalty=max(0.0,(5-int(audit.support))*1.0+int(audit.conflict)*2.0)
        normalized.feature_snapshot.setdefault("evidence_v117",{})["score_penalty"]=penalty
        if penalty:
            normalized.score=max(0.0,float(normalized.score)-penalty)

        required=float(
            (normalized.feature_snapshot or {}).get("required_score",82) or 82
        )
        if normalized.status=="BUY" and (not audit.eligible or normalized.score<required):
            normalized.status="WATCH"
            normalized.risks.append(f"independent evidence gate: {audit.summary}")

        normalized.feature_snapshot["data_coherence_v11100"]={
            "status":"GOOD" if any(x.observable for x in coherence) else "UNOBSERVABLE",
            "frames":[{
                "role":x.role,"interval":x.interval,"observable":x.observable,
                "age_sec":x.age_sec,"max_gap_sec":x.max_gap_sec,"reason":x.reason,
            } for x in coherence],
        }
        return normalized,None
    except Exception as exc:
        log.warning("V11.21 Spot deep failed %s: %s",symbol,exc)
        return None,f"{symbol}: {type(exc).__name__}: {exc}"


async def scan(force=False):
    if _lock.locked():
        raise RuntimeError("Spot scan already running")
    async with _lock:
        _last.update(
            status="running",reason="",
            started_at=datetime.now(timezone.utc).isoformat(),finished_at=None,
            universe=0,liquid=0,daily_ok=0,full_universe_ranked=0,
            prefiltered=0,deep_checked=0,buy=0,watch=0,errors=0,daily_errors=0,
            deep_errors=0,futures_overlay_ok=False,near=[],
        )
        try:
            metas,tickers=await asyncio.gather(
                legacy.universe(),legacy.tickers_24h(force=force)
            )
            try:
                news_snapshot=await asyncio.wait_for(legacy.get_news_sentiment(),timeout=18)
                _last["news_degraded"]=False
            except Exception as exc:
                news_snapshot={"global":0.0,"assets":{},"items":[],"headlines":[],
                    "breaking_events":[],"sources":0,"source_total":0,"failed_sources":1,
                    "event_risk":0.0,"high_impact_count":0,"v114_news_degraded":True}
                _last["news_degraded"]=True
                _last["news_reason"]=f"{type(exc).__name__}: {exc}"
            _last["universe"]=len(metas)

            liquid=[
                m for m in metas
                if _finite(tickers.get(m.symbol,{}).get("quote_volume"))
                >=legacy.MIN_SPOT_QUOTE_VOLUME
            ]
            liquid.sort(
                key=lambda m:_finite(tickers.get(m.symbol,{}).get("quote_volume")),
                reverse=True
            )
            _last["liquid"]=len(liquid)
            if not liquid:
                raise RuntimeError("no liquid Binance Spot USDT symbols")

            daily_results=await asyncio.gather(*(legacy._daily(m.symbol) for m in liquid))
            by_meta={m.symbol:m for m in liquid}
            rows={}
            daily_errors=[]
            for result,error in daily_results:
                if error: daily_errors.append(error)
                if not result: continue
                symbol,frame,feat=result
                rows[symbol]={"meta":by_meta[symbol],"daily":frame,"feat":feat}

            _last["daily_ok"]=len(rows)
            _last["daily_errors"]=len(daily_errors)
            if liquid and len(daily_errors)>max(5,int(len(liquid)*.25)):
                raise RuntimeError(
                    f"Spot daily data degraded {len(daily_errors)}/{len(liquid)}"
                )
            if "BTCUSDT" not in rows:
                raise RuntimeError("BTCUSDT Spot daily state unavailable")

            market=market_state(rows,rows["BTCUSDT"]["daily"])
            _last.update(
                regime=market["regime"],breadth=market["breadth"],
                dispersion_7d=market["dispersion_7d"]
            )
            btc14=_finite(rows["BTCUSDT"]["feat"].get("ret14"))
            momentum={
                s:_finite(r["feat"].get("ret7"))
                  +.65*_finite(r["feat"].get("ret14"))
                  +.25*_finite(r["feat"].get("ret30"))
                for s,r in rows.items()
            }
            rel=legacy._percentiles(momentum)

            # FULL-UNIVERSE discovery: no close>EMA100 / max-day hard exclusion here.
            ranked=[]
            for symbol,row in rows.items():
                feat=row["feat"]
                excess=_finite(feat.get("ret14"))-btc14
                pre=legacy.preliminary_score(feat,rel.get(symbol,50),excess)

                # Recovery sensitivity: reward current 7D leadership even when
                # slow 30D structure is still catching up.
                if _finite(feat.get("ret7"))>0:
                    pre+=min(8.0,_finite(feat.get("ret7"))*.35)
                if rel.get(symbol,50)>=90:
                    pre+=4.0
                ranked.append((pre,symbol,excess))

            ranked.sort(reverse=True)
            _last["full_universe_ranked"]=len(ranked)
            _last["prefiltered"]=len(ranked)
            _last["near"]=[
                {"symbol":s,"pre":round(p,1),"rel":round(rel.get(s,50),1),
                 "excess":round(ex,1)}
                for p,s,ex in ranked[:8]
            ]

            try:
                fut_set=set(await legacy.futures_symbols())
                futures_overlay_ok=bool(fut_set)
            except Exception as exc:
                log.warning("V11.21 Spot futures overlay unavailable: %s",exc)
                fut_set=set()
                futures_overlay_ok=False
            _last["futures_overlay_ok"]=futures_overlay_ok

            deep_candidates=ranked[:min(SPOT_DEEP_SHORTLIST,len(ranked))]
            _last["deep_checked"]=len(deep_candidates)

            # Bounded concurrency prevents a broad deep scan from becoming an API burst.
            sem=asyncio.Semaphore(4)
            async def one(item):
                p,s,ex=item
                async with sem:
                    return await _deep(
                        s,rows[s],rel.get(s,50),ex,market,news_snapshot,
                        fut_set,futures_overlay_ok
                    )

            found=await asyncio.gather(*(one(x) for x in deep_candidates))
            deep_errors=[err for _,err in found if err]
            _last["deep_errors"]=len(deep_errors)
            _last["errors"]=len(daily_errors)+len(deep_errors)
            if deep_candidates and len(deep_errors)>=max(4,int(len(deep_candidates)*.55)):
                raise RuntimeError(
                    f"Spot deep data degraded {len(deep_errors)}/{len(deep_candidates)}"
                )

            final=[sig for sig,err in found if sig is not None]
            final=legacy._portfolio_diversify(final,rows,.92)
            final.sort(
                key=lambda s:(1 if s.status=="BUY" else 0,float(s.score)),
                reverse=True
            )
            _last["buy"]=sum(x.status=="BUY" for x in final)
            _last["watch"]=sum(x.status=="WATCH" for x in final)
            _last.update(
                status="ok",finished_at=datetime.now(timezone.utc).isoformat()
            )
            return final[:SPOT_FINAL_LIMIT]
        except Exception as exc:
            _last.update(
                status="error",reason=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.now(timezone.utc).isoformat()
            )
            raise


def install():
    # Preserve original callables once.
    if not hasattr(strategy,"_v11191_original_analyze"):
        strategy._v11191_original_analyze=strategy.analyze
    if not hasattr(evidence_mod,"_v11191_original_spot"):
        evidence_mod._v11191_original_spot=evidence_mod.spot

    strategy.analyze=analyze
    legacy.analyze=analyze
    legacy.spot_evidence_audit=spot_evidence
    legacy._market_state=market_state
    legacy._deep=_deep
    legacy.scan=scan
    legacy.status=status
    return scan
