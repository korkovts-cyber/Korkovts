import numpy as np

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def rsi(s,n=14):
    d=s.diff()
    g=d.clip(lower=0)
    l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/n,adjust=False).mean()
    al=l.ewm(alpha=1/n,adjust=False).mean()
    rs=ag/al.replace(0,np.nan)
    return 100-(100/(1+rs))

def atr(df,n=14):
    prev=df.close.shift(1)
    tr=np.maximum(df.high-df.low,np.maximum((df.high-prev).abs(),(df.low-prev).abs()))
    return tr.ewm(alpha=1/n,adjust=False).mean()

def adx(df,n=14):
    up=df.high.diff(); down=-df.low.diff()
    plus=np.where((up>down)&(up>0),up,0.0)
    minus=np.where((down>up)&(down>0),down,0.0)
    tr=atr(df,n)
    plus_di=100*np.asarray(plus)
    minus_di=100*np.asarray(minus)
    import pandas as pd
    plus_di=pd.Series(plus_di,index=df.index).ewm(alpha=1/n,adjust=False).mean()/tr
    minus_di=pd.Series(minus_di,index=df.index).ewm(alpha=1/n,adjust=False).mean()/tr
    dx=100*(plus_di-minus_di).abs()/(plus_di+minus_di).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean(),plus_di,minus_di

def enrich(df):
    x=df.copy()
    x["ema20"]=ema(x.close,20); x["ema50"]=ema(x.close,50); x["ema200"]=ema(x.close,200)
    x["rsi"]=rsi(x.close)
    m=ema(x.close,12)-ema(x.close,26)
    x["macd"]=m; x["macd_signal"]=ema(m,9); x["macd_hist"]=m-x.macd_signal
    x["atr"]=atr(x)
    x["vol_ma20"]=x.volume.rolling(20).mean()
    x["vol_std20"]=x.volume.rolling(20).std()
    x["vol_z"]=(x.volume-x.vol_ma20)/x.vol_std20.replace(0,np.nan)
    x["adx"],x["plus_di"],x["minus_di"]=adx(x)
    x["high20"]=x.high.rolling(20).max().shift(1)
    x["low20"]=x.low.rolling(20).min().shift(1)
    x["atr_pct"]=x.atr/x.close*100
    low_rsi=x.rsi.rolling(14).min(); high_rsi=x.rsi.rolling(14).max()
    x["stoch_rsi"]=(x.rsi-low_rsi)/(high_rsi-low_rsi).replace(0,np.nan)*100
    mid=x.close.rolling(20).mean(); std=x.close.rolling(20).std()
    x["bb_mid"]=mid; x["bb_upper"]=mid+2*std; x["bb_lower"]=mid-2*std
    x["bb_width"]=(x.bb_upper-x.bb_lower)/mid*100
    direction=np.sign(x.close.diff()).fillna(0)
    x["obv"]=(direction*x.volume).cumsum()
    x["obv_ema20"]=ema(x.obv,20)
    typical=(x.high+x.low+x.close)/3
    x["vwap20"]=(typical*x.volume).rolling(20).sum()/x.volume.rolling(20).sum()
    # Kaufman-style efficiency: directional progress divided by total path.
    path=x.close.diff().abs().rolling(20).sum()
    x["efficiency20"]=(x.close-x.close.shift(20)).abs()/path.replace(0,np.nan)
    taker_buy=x["taker_buy_base"] if "taker_buy_base" in x else x.volume/2
    signed_volume=2*taker_buy-x.volume
    x["taker_imbalance10"]=signed_volume.rolling(10).sum()/x.volume.rolling(10).sum().replace(0,np.nan)
    x["cvd"]=signed_volume.cumsum(); x["cvd_ema20"]=ema(x.cvd,20)
    x["momentum24"]=(x.close/x.close.shift(24)-1)*100
    return x.dropna()
