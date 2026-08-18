"""Indicator/feature library for the 3–10 day Spot universe.

No Futures fields are used here. All inputs come from Binance Spot candles.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd


def _ema(s,n):
    return s.ewm(span=n,adjust=False,min_periods=n).mean()


def _rsi(close,n=14):
    d=close.diff()
    up=d.clip(lower=0).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=up/dn.replace(0,np.nan)
    out=100-100/(1+rs)
    out=out.mask((dn==0)&(up>0),100.0)
    out=out.mask((up==0)&(dn>0),0.0)
    out=out.mask((up==0)&(dn==0),50.0)
    return out.fillna(50)


def _atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([
        df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()
    ],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


def _adx(df,n=14):
    up=df.high.diff(); dn=-df.low.diff()
    plus=np.where((up>dn)&(up>0),up,0.0)
    minus=np.where((dn>up)&(dn>0),dn,0.0)
    atr=_atr(df,n).replace(0,np.nan)
    plus_di=100*pd.Series(plus,index=df.index).ewm(alpha=1/n,adjust=False,min_periods=n).mean()/atr
    minus_di=100*pd.Series(minus,index=df.index).ewm(alpha=1/n,adjust=False,min_periods=n).mean()/atr
    dx=100*(plus_di-minus_di).abs()/(plus_di+minus_di).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean().fillna(0),plus_di.fillna(0),minus_di.fillna(0)


def enrich(df):
    x=df.copy()
    if x.empty:
        return x
    for c in ("open","high","low","close","volume","quote_volume","taker_buy_base"):
        if c in x:
            x[c]=pd.to_numeric(x[c],errors="coerce")
    close=x.close
    for n in (10,20,50,100,200):
        x[f"ema{n}"]=_ema(close,n)
    x["rsi"]=_rsi(close,14)
    x["atr"]=_atr(x,14)
    x["atr_pct"]=x.atr/close.replace(0,np.nan)*100
    x["adx"],x["plus_di"],x["minus_di"]=_adx(x,14)

    # MACD
    macd=_ema(close,12)-_ema(close,26)
    signal=_ema(macd,9)
    x["macd_hist"]=macd-signal

    # OBV
    direction=np.sign(close.diff()).fillna(0)
    x["obv"]=(direction*x.volume.fillna(0)).cumsum()
    x["obv_ema20"]=_ema(x.obv,20)

    # Chaikin Money Flow
    rng=(x.high-x.low).replace(0,np.nan)
    mf_mult=((close-x.low)-(x.high-close))/rng
    mf_vol=mf_mult.fillna(0)*x.volume.fillna(0)
    x["cmf20"]=mf_vol.rolling(20,min_periods=10).sum()/x.volume.rolling(20,min_periods=10).sum().replace(0,np.nan)

    # Money Flow Index
    tp=(x.high+x.low+close)/3
    raw=tp*x.volume.fillna(0)
    pos=raw.where(tp.diff()>0,0).rolling(14,min_periods=7).sum()
    neg=raw.where(tp.diff()<0,0).rolling(14,min_periods=7).sum()
    ratio=pos/neg.replace(0,np.nan)
    x["mfi"]=100-100/(1+ratio)
    x["mfi"]=x.mfi.mask((neg==0)&(pos>0),100.0)
    x["mfi"]=x.mfi.mask((pos==0)&(neg>0),0.0)
    x["mfi"]=x.mfi.mask((pos==0)&(neg==0),50.0)
    x["mfi"]=x.mfi.fillna(50)

    # Volume and taker-flow features.
    x["volume_median30"]=x.volume.rolling(30,min_periods=10).median()
    x["volume_ratio7_30"]=x.volume.rolling(7,min_periods=3).mean()/x.volume_median30.replace(0,np.nan)
    x["taker_buy_share"]=(x.taker_buy_base/x.volume.replace(0,np.nan)).clip(0,1).fillna(.5)
    x["taker_buy_share7"]=x.taker_buy_share.rolling(7,min_periods=3).mean()
    typical=(x.high+x.low+close)/3
    pv=typical*x.volume.fillna(0)
    x["vwap20"]=pv.rolling(20,min_periods=10).sum()/x.volume.rolling(20,min_periods=10).sum().replace(0,np.nan)

    # Return/continuity features used for weekly-horizon continuation.
    for n in (3,7,14,30,60):
        x[f"ret{n}"]=(close/close.shift(n)-1)*100
    daily_ret=close.pct_change()
    x["realized_vol14"]=daily_ret.rolling(14,min_periods=7).std()*100
    net=(close/close.shift(14)-1).abs()
    path=daily_ret.abs().rolling(14,min_periods=7).sum().replace(0,np.nan)
    x["path_eff14"]=(net/path).clip(0,1)
    x["positive_days14"]=(daily_ret>0).astype(float).rolling(14,min_periods=7).mean()
    x["max_day14"]=daily_ret.rolling(14,min_periods=7).max()*100
    x["min_day14"]=daily_ret.rolling(14,min_periods=7).min()*100

    # Breakout / compression.
    x["high20_prev"]=x.high.shift(1).rolling(20,min_periods=10).max()
    x["high55_prev"]=x.high.shift(1).rolling(55,min_periods=25).max()
    x["low20_prev"]=x.low.shift(1).rolling(20,min_periods=10).min()
    mid=close.rolling(20,min_periods=10).mean()
    std=close.rolling(20,min_periods=10).std()
    x["bb_width"]=(4*std/mid.replace(0,np.nan))*100
    x["bb_width_med60"]=x.bb_width.rolling(60,min_periods=20).median()

    # Distance/extension.
    x["dist_ema20_atr"]=(close-x.ema20)/x.atr.replace(0,np.nan)
    x["dist_ema50_atr"]=(close-x.ema50)/x.atr.replace(0,np.nan)
    return x.replace([np.inf,-np.inf],np.nan)


def last_features(df):
    x=enrich(df)
    if x.empty:
        return None
    row=x.iloc[-1]
    return {k:(float(v) if isinstance(v,(int,float,np.floating,np.integer)) and pd.notna(v) else v)
            for k,v in row.items()}
