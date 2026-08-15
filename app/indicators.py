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
    return x.dropna()
