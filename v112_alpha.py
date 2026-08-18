"""Korkovts V11.4 orthogonal alpha features.

The Alpha layer never creates a trade and never rescues a Production-rejected
trade. Features are stored independently for forward ablation.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pandas as pd

from app.market import _get, get_klines, get_tickers


@dataclass(frozen=True)
class Alpha:
    raw_adjustment: float
    components: dict
    fresh_score: float
    momentum_percentile: float
    ofi_recent: float
    ofi_5m: float
    agg_coverage_sec: float
    squeeze_release: bool
    beta: float
    residual_pct: float
    residual_horizon: str
    quarter_hour: bool
    notes: tuple[str, ...]


_ofi_sem=asyncio.Semaphore(4)


def _ema(s,span):
    return s.astype(float).ewm(span=span,adjust=False).mean()


def _atr(df,period=14):
    high=df.high.astype(float); low=df.low.astype(float); close=df.close.astype(float)
    prev=close.shift(1)
    tr=pd.concat([(high-low).abs(),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period,adjust=False).mean()


def _macd_hist(close):
    fast=_ema(close,12); slow=_ema(close,26)
    macd=fast-slow
    return macd-_ema(macd,9)


def _squeeze_release(df):
    if df is None or len(df)<30:
        return False,False
    close=df.close.astype(float)
    mid=close.rolling(20).mean()
    std=close.rolling(20).std(ddof=0)
    bb_up=mid+2*std; bb_dn=mid-2*std
    atr=_atr(df,20); ema=_ema(close,20)
    kc_up=ema+1.5*atr; kc_dn=ema-1.5*atr
    squeeze=(bb_up<kc_up)&(bb_dn>kc_dn)
    return bool(squeeze.iloc[-1]),bool(squeeze.iloc[-2] and not squeeze.iloc[-1])


def _freshness(df,side,setup):
    if df is None or len(df)<35:
        return 50.0,0,False
    close=df.close.astype(float); open_=df.open.astype(float)
    ema20=_ema(close,20); hist=_macd_hist(close)
    aligned=(close>ema20)&(hist>0) if side=="LONG" else (close<ema20)&(hist<0)

    age=0
    for value in reversed(aligned.tail(8).tolist()):
        if bool(value): age+=1
        else: break

    fresh=50.0
    candle_ok=(close.iloc[-1]>open_.iloc[-1]) if side=="LONG" else (close.iloc[-1]<open_.iloc[-1])
    accel=(hist.iloc[-1]>hist.iloc[-2]) if side=="LONG" else (hist.iloc[-1]<hist.iloc[-2])
    if candle_ok: fresh+=15
    if accel: fresh+=20
    if age<=2: fresh+=15
    elif age>=5: fresh-=25

    breakout_fresh=False
    if "ПРОБОЙ" in str(setup).upper() and len(df)>=24:
        if side=="LONG":
            prior=float(df.high.astype(float).iloc[-21:-1].max())
            prev_prior=float(df.high.astype(float).iloc[-22:-2].max())
            breakout_fresh=close.iloc[-1]>prior and close.iloc[-2]<=prev_prior
        else:
            prior=float(df.low.astype(float).iloc[-21:-1].min())
            prev_prior=float(df.low.astype(float).iloc[-22:-2].min())
            breakout_fresh=close.iloc[-1]<prior and close.iloc[-2]>=prev_prior
        fresh += 15 if breakout_fresh else -15
    return max(0.0,min(100.0,fresh)),age,breakout_fresh


def _agg_imbalance(rows,now_ms=None):
    """Latest 1000 aggTrades are a *sample*, not necessarily a full minute."""
    if not rows:
        return 0.0,0.0
    now_ms=int(now_ms or time.time()*1000)
    buy=sell=0.0
    oldest=now_ms
    for r in rows:
        ts=int(r.get("T",0) or 0)
        if ts:
            oldest=min(oldest,ts)
        notional=float(r.get("p",0) or 0)*float(r.get("q",0) or 0)
        taker_buy=not bool(r.get("m",False))
        if taker_buy: buy+=notional
        else: sell+=notional
    total=buy+sell
    return ((buy-sell)/total if total else 0.0),max(0.0,(now_ms-oldest)/1000)


async def _recent_aggressor(symbol):
    try:
        async with _ofi_sem:
            rows=await asyncio.wait_for(
                _get("/fapi/v1/aggTrades",{"symbol":str(symbol).upper(),"limit":1000}),
                timeout=7,
            )
        return _agg_imbalance(rows if isinstance(rows,list) else [])
    except Exception:
        return 0.0,0.0


def _closed_5m_taker(df):
    """Five closed 1m candles: exact window from Binance kline taker volume."""
    if df is None or len(df)<5:
        return 0.0
    tail=df.tail(5)
    volume=float(tail.volume.astype(float).sum())
    if volume<=0:
        return 0.0
    taker=float(tail.taker_buy_base.astype(float).sum()) if "taker_buy_base" in tail else volume/2
    return (2*taker-volume)/volume


def _momentum_percentile(symbol,tickers,side,min_volume=5_000_000):
    rows=[
        float(v.get("change",0) or 0)
        for k,v in tickers.items()
        if str(k).endswith("USDT") and float(v.get("quote_volume",0) or 0)>=min_volume
    ]
    if not rows:
        return 50.0
    candidate=float((tickers.get(symbol) or {}).get("change",0) or 0)
    percentile=100.0*sum(x<=candidate for x in rows)/len(rows)
    return percentile if side=="LONG" else 100.0-percentile


def _beta_residual(coin,btc,side,interval):
    if coin is None or btc is None or len(coin)<30 or len(btc)<30:
        return 1.0,0.0,("3h" if interval=="15m" else "6h")

    left=coin[["open_time","close"]].rename(columns={"close":"coin"}).copy()
    right=btc[["open_time","close"]].rename(columns={"close":"btc"}).copy()
    merged=left.merge(right,on="open_time",how="inner").sort_values("open_time").tail(80)
    if len(merged)<30:
        return 1.0,0.0,("3h" if interval=="15m" else "6h")

    returns=merged[["coin","btc"]].astype(float).pct_change().dropna().tail(48)
    if len(returns)<20:
        return 1.0,0.0,("3h" if interval=="15m" else "6h")
    var=float(returns.btc.var())
    beta=float(returns.coin.cov(returns.btc)/var) if var>1e-12 else 1.0

    bars=12 if interval=="15m" else 6
    horizon="3h" if interval=="15m" else "6h"
    tail=returns.tail(min(bars,len(returns)))
    residual=float((tail.coin-beta*tail.btc).sum()*100)
    if side=="SHORT":
        residual=-residual
    return beta,residual,horizon


def component_scores(signal,fresh,age,breakout_fresh,momentum,ofi_recent,ofi5,coverage,
                     squeeze_on,squeeze_release,residual,quarter):
    components={"fresh":0.0,"momentum":0.0,"ofi":0.0,"squeeze":0.0,"residual":0.0,"quarter":0.0}
    notes=[]

    if fresh>=80:
        components["fresh"]+=2.0; notes.append("fresh trigger")
    elif fresh<45:
        components["fresh"]-=3.0; notes.append("late/sticky trigger")
    if age>=5:
        components["fresh"]-=1.5

    if "ПРОБОЙ" in str(getattr(signal,"setup_type","")).upper():
        if breakout_fresh:
            components["fresh"]+=1.5; notes.append("fresh 20-bar breakout")
        else:
            components["fresh"]-=2.5; notes.append("breakout no longer fresh")

    if momentum>=85:
        components["momentum"]+=2.0; notes.append("top liquid momentum")
    elif momentum>=70:
        components["momentum"]+=1.0
    elif momentum<30:
        components["momentum"]-=1.5; notes.append("weak relative momentum")

    direction=1 if signal.side=="LONG" else -1
    closed_flow=direction*ofi5
    sample_flow=direction*ofi_recent
    sample_conf=min(1.0,max(0.0,coverage/60.0))

    # Closed 5m flow is the stable anchor; the live aggTrade sample is a small
    # timing confirmation weighted by how much time the 1000-trade sample covers.
    if closed_flow>=.06 and sample_flow*sample_conf>=.02:
        components["ofi"]+=2.0; notes.append("persistent aggressor flow")
    elif closed_flow<=-.12 and sample_flow*sample_conf<=-.04:
        components["ofi"]-=4.0; notes.append("order flow contradicts signal")
    elif closed_flow<=-.06:
        components["ofi"]-=1.5

    if squeeze_release:
        components["squeeze"]+=1.5; notes.append("volatility squeeze released")
    elif squeeze_on and "ПРОБОЙ" in str(getattr(signal,"setup_type","")).upper():
        components["squeeze"]-=1.0

    if residual>=.50:
        components["residual"]+=1.5; notes.append("positive BTC-residual strength")
    elif residual<=-.50:
        components["residual"]-=1.5; notes.append("weak vs BTC beta")

    if quarter and str(signal.timeframe).upper()=="1H" and closed_flow>=.05:
        components["quarter"]+=1.0; notes.append("quarter-hour flow alignment")

    return components,notes


async def analyze(signal,tickers=None,btc=None,coin=None,micro=None,agg=None):
    interval="15m" if str(signal.timeframe).upper()=="15M" else "1h"
    try:
        if coin is None:
            # 350 matches the core scanner's technical-candidate cache key,
            # so whole-market scans normally reuse the already fetched candles.
            coin=await get_klines(signal.symbol,interval,350)
        if micro is None:
            micro=await get_klines(signal.symbol,"1m",7)
        if agg is None:
            agg=await _recent_aggressor(signal.symbol)
        if tickers is None:
            tickers=await get_tickers()
        if btc is None:
            btc=await get_klines("BTCUSDT",interval,350)
    except Exception:
        return Alpha(0.0,{},50,50,0,0,0,False,1,0,
                     ("3h" if interval=="15m" else "6h"),False,("alpha data unavailable",))

    fresh,age,breakout_fresh=_freshness(coin,signal.side,getattr(signal,"setup_type",""))
    squeeze_on,squeeze_release=_squeeze_release(coin)
    momentum=_momentum_percentile(signal.symbol,tickers,signal.side)
    beta,residual,horizon=_beta_residual(coin,btc,signal.side,interval)
    ofi_recent,coverage=agg
    ofi5=_closed_5m_taker(micro)
    minute=pd.Timestamp.now(tz="UTC").minute
    quarter=min(minute%15,15-(minute%15))<=2

    components,notes=component_scores(
        signal,fresh,age,breakout_fresh,momentum,ofi_recent,ofi5,coverage,
        squeeze_on,squeeze_release,residual,quarter
    )
    raw=max(-10.0,min(6.0,sum(components.values())))
    return Alpha(raw,components,fresh,momentum,ofi_recent,ofi5,coverage,
                 squeeze_release,beta,residual,horizon,quarter,tuple(notes))


async def annotate(signals):
    if not signals:
        return []

    try:
        tickers=await asyncio.wait_for(get_tickers(),timeout=8)
    except Exception:
        tickers={}

    intervals={("15m" if str(s.timeframe).upper()=="15M" else "1h") for s in signals}

    async def load_btc(interval):
        try:
            frame=await asyncio.wait_for(get_klines("BTCUSDT",interval,350),timeout=8)
        except Exception:
            frame=None
        return interval,frame

    btc_by_interval=dict(await asyncio.gather(*(load_btc(i) for i in intervals)))

    async def one(s):
        interval="15m" if str(s.timeframe).upper()=="15M" else "1h"
        try:
            coin,micro,agg=await asyncio.gather(
                asyncio.wait_for(get_klines(s.symbol,interval,350),timeout=8),
                asyncio.wait_for(get_klines(s.symbol,"1m",7),timeout=8),
                _recent_aggressor(s.symbol),
            )
        except Exception:
            coin=micro=None; agg=(0.0,0.0)
        return await analyze(
            s,tickers=tickers,btc=btc_by_interval.get(interval),
            coin=coin,micro=micro,agg=agg
        )

    metrics=await asyncio.gather(*(one(s) for s in signals))

    for s,a in zip(signals,metrics):
        s.alpha_raw_adjustment=float(a.raw_adjustment)
        s.alpha_adjustment=float(a.raw_adjustment)
        s.alpha_components=dict(a.components)
        s.alpha_fresh_score=float(a.fresh_score)
        s.alpha_momentum_percentile=float(a.momentum_percentile)
        s.alpha_ofi_1m=float(a.ofi_recent)      # compatibility alias
        s.alpha_ofi_recent=float(a.ofi_recent)
        s.alpha_ofi_5m=float(a.ofi_5m)
        s.alpha_agg_coverage_sec=float(a.agg_coverage_sec)
        s.alpha_squeeze_release=bool(a.squeeze_release)
        s.alpha_beta=float(a.beta)
        s.alpha_residual_6h_pct=float(a.residual_pct)  # compatibility alias
        s.alpha_residual_pct=float(a.residual_pct)
        s.alpha_residual_horizon=str(a.residual_horizon)
        s.alpha_quarter_hour=bool(a.quarter_hour)
        s.alpha_notes=list(a.notes)
        s.feature_snapshot.setdefault("alpha_v112",{}).update({
            "raw_adjustment":a.raw_adjustment,
            "components":dict(a.components),
            "fresh_score":a.fresh_score,
            "momentum_percentile":a.momentum_percentile,
            "ofi_recent":a.ofi_recent,
            "ofi_5m":a.ofi_5m,
            "agg_coverage_sec":a.agg_coverage_sec,
            "squeeze_release":a.squeeze_release,
            "beta":a.beta,
            "residual_pct":a.residual_pct,
            "residual_horizon":a.residual_horizon,
            "quarter_hour":a.quarter_hour,
            "notes":list(a.notes),
        })
    return signals
