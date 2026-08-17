"""Korkovts V11.2 orthogonal alpha features.

This module never creates a trade. The core strategy and V11 Production gates
must pass first. Each factor contribution is stored separately so the research
lab can later measure whether it actually improved forward-test outcomes.
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
    ofi_1m: float
    ofi_5m: float
    squeeze_release: bool
    beta: float
    residual_6h_pct: float
    quarter_hour: bool
    notes: tuple[str, ...]


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


async def _ofi(symbol):
    try:
        rows=await asyncio.wait_for(
            _get("/fapi/v1/aggTrades",{"symbol":str(symbol).upper(),"limit":1000}),
            timeout=7,
        )
        now_ms=int(time.time()*1000)
        sums={60_000:[0.0,0.0],300_000:[0.0,0.0]}
        for r in rows if isinstance(rows,list) else []:
            ts=int(r.get("T",0) or 0)
            notional=float(r.get("p",0) or 0)*float(r.get("q",0) or 0)
            # m=True means the buyer was maker, therefore the aggressor sold.
            taker_buy=not bool(r.get("m",False))
            age=now_ms-ts
            for window,bucket in sums.items():
                if 0<=age<=window:
                    bucket[0 if taker_buy else 1]+=notional
        def imbalance(bucket):
            buy,sell=bucket; total=buy+sell
            return (buy-sell)/total if total else 0.0
        return imbalance(sums[60_000]),imbalance(sums[300_000])
    except Exception:
        return 0.0,0.0


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


def _beta_residual(coin,btc,side):
    if coin is None or btc is None or len(coin)<30 or len(btc)<30:
        return 1.0,0.0
    a=coin.close.astype(float).pct_change().dropna().tail(48).reset_index(drop=True)
    b=btc.close.astype(float).pct_change().dropna().tail(48).reset_index(drop=True)
    n=min(len(a),len(b))
    if n<20:
        return 1.0,0.0
    a=a.tail(n).reset_index(drop=True); b=b.tail(n).reset_index(drop=True)
    var=float(b.var())
    beta=float(a.cov(b)/var) if var>1e-12 else 1.0
    last=min(6,n)
    residual=float((a.tail(last)-beta*b.tail(last)).sum()*100)
    if side=="SHORT":
        residual=-residual
    return beta,residual


def component_scores(signal,fresh,age,breakout_fresh,momentum,ofi1,ofi5,
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
    aligned1=direction*ofi1; aligned5=direction*ofi5
    if aligned1>=.08 and aligned5>=.05:
        components["ofi"]+=2.0; notes.append("persistent aggressor flow")
    elif aligned1<=-.15 and aligned5<=-.10:
        components["ofi"]-=4.0; notes.append("order flow contradicts signal")
    elif aligned5<=-.05:
        components["ofi"]-=1.5

    if squeeze_release:
        components["squeeze"]+=1.5; notes.append("volatility squeeze released")
    elif squeeze_on and "ПРОБОЙ" in str(getattr(signal,"setup_type","")).upper():
        components["squeeze"]-=1.0

    if residual>=.50:
        components["residual"]+=1.5; notes.append("positive BTC-residual strength")
    elif residual<=-.50:
        components["residual"]-=1.5; notes.append("weak vs BTC beta")

    if quarter and str(signal.timeframe).upper()=="1H" and aligned5>=.05:
        components["quarter"]+=1.0; notes.append("quarter-hour flow alignment")

    return components,notes


async def analyze(signal,tickers=None,btc=None):
    interval="15m" if str(signal.timeframe).upper()=="15M" else "1h"
    try:
        tasks=[get_klines(signal.symbol,interval,100),_ofi(signal.symbol)]
        coin,ofi=await asyncio.gather(*tasks)
        if tickers is None:
            tickers=await get_tickers()
        if btc is None:
            btc=await get_klines("BTCUSDT",interval,100)
    except Exception:
        return Alpha(0.0,{},50,50,0,0,False,1,0,False,("alpha data unavailable",))

    fresh,age,breakout_fresh=_freshness(coin,signal.side,getattr(signal,"setup_type",""))
    squeeze_on,squeeze_release=_squeeze_release(coin)
    momentum=_momentum_percentile(signal.symbol,tickers,signal.side)
    beta,residual=_beta_residual(coin,btc,signal.side)
    ofi1,ofi5=ofi
    minute=pd.Timestamp.now(tz="UTC").minute
    quarter=min(minute%15,15-(minute%15))<=2

    components,notes=component_scores(
        signal,fresh,age,breakout_fresh,momentum,ofi1,ofi5,
        squeeze_on,squeeze_release,residual,quarter
    )
    raw=max(-10.0,min(6.0,sum(components.values())))
    return Alpha(raw,components,fresh,momentum,ofi1,ofi5,squeeze_release,beta,residual,quarter,tuple(notes))


async def annotate(signals):
    if not signals:
        return []

    # Shared market context: all-tickers is fetched once, and BTC candles once
    # per timeframe represented in this result pool.
    try:
        tickers=await asyncio.wait_for(get_tickers(),timeout=8)
    except Exception:
        tickers={}

    timeframes={("15m" if str(s.timeframe).upper()=="15M" else "1h") for s in signals}
    btc_by_interval={}
    async def load_btc(interval):
        try:
            frame=await asyncio.wait_for(get_klines("BTCUSDT",interval,100),timeout=8)
        except Exception:
            frame=None
        return interval,frame
    loaded=await asyncio.gather(*(load_btc(i) for i in timeframes))
    btc_by_interval=dict(loaded)

    metrics=await asyncio.gather(*(
        analyze(
            s,
            tickers=tickers,
            btc=btc_by_interval.get("15m" if str(s.timeframe).upper()=="15M" else "1h"),
        )
        for s in signals
    ))

    for s,a in zip(signals,metrics):
        s.alpha_raw_adjustment=float(a.raw_adjustment)
        s.alpha_adjustment=float(a.raw_adjustment)
        s.alpha_components=dict(a.components)
        s.alpha_fresh_score=float(a.fresh_score)
        s.alpha_momentum_percentile=float(a.momentum_percentile)
        s.alpha_ofi_1m=float(a.ofi_1m)
        s.alpha_ofi_5m=float(a.ofi_5m)
        s.alpha_squeeze_release=bool(a.squeeze_release)
        s.alpha_beta=float(a.beta)
        s.alpha_residual_6h_pct=float(a.residual_6h_pct)
        s.alpha_quarter_hour=bool(a.quarter_hour)
        s.alpha_notes=list(a.notes)
        s.feature_snapshot.setdefault("alpha_v112",{}).update({
            "raw_adjustment":a.raw_adjustment,
            "components":dict(a.components),
            "fresh_score":a.fresh_score,
            "momentum_percentile":a.momentum_percentile,
            "ofi_1m":a.ofi_1m,
            "ofi_5m":a.ofi_5m,
            "squeeze_release":a.squeeze_release,
            "beta":a.beta,
            "residual_6h_pct":a.residual_6h_pct,
            "quarter_hour":a.quarter_hour,
            "notes":list(a.notes),
        })
    return signals
