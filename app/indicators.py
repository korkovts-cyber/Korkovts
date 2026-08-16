import numpy as np

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def rsi(s,n=14):
    d=s.diff()
    g=d.clip(lower=0)
    l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/n,adjust=False).mean()
    al=l.ewm(alpha=1/n,adjust=False).mean()
    rs=ag/al.replace(0,np.nan)
    out=100-(100/(1+rs))
    out=out.mask((al==0)&(ag>0),100.0)
    out=out.mask((ag==0)&(al>0),0.0)
    return out.mask((ag==0)&(al==0),50.0)

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
    return dx.ewm(alpha=1/n,adjust=False).mean().fillna(0),plus_di.fillna(0),minus_di.fillna(0)

def enrich(df):
    x=df.copy()
    x["ema20"]=ema(x.close,20); x["ema50"]=ema(x.close,50); x["ema200"]=ema(x.close,200)
    x["rsi"]=rsi(x.close)
    m=ema(x.close,12)-ema(x.close,26)
    x["macd"]=m; x["macd_signal"]=ema(m,9); x["macd_hist"]=m-x.macd_signal
    x["atr"]=atr(x)
    x["vol_ma20"]=x.volume.rolling(20).mean()
    x["vol_std20"]=x.volume.rolling(20).std()
    x["vol_z"]=((x.volume-x.vol_ma20)/x.vol_std20.replace(0,np.nan)).fillna(0)
    x["adx"],x["plus_di"],x["minus_di"]=adx(x)
    x["high20"]=x.high.rolling(20).max().shift(1)
    x["low20"]=x.low.rolling(20).min().shift(1)
    x["atr_pct"]=x.atr/x.close*100
    low_rsi=x.rsi.rolling(14).min(); high_rsi=x.rsi.rolling(14).max()
    x["stoch_rsi"]=((x.rsi-low_rsi)/(high_rsi-low_rsi).replace(0,np.nan)*100).fillna(50)
    mid=x.close.rolling(20).mean(); std=x.close.rolling(20).std()
    x["bb_mid"]=mid; x["bb_upper"]=mid+2*std; x["bb_lower"]=mid-2*std
    x["bb_width"]=(x.bb_upper-x.bb_lower)/mid*100
    direction=np.sign(x.close.diff()).fillna(0)
    x["obv"]=(direction*x.volume).cumsum()
    x["obv_ema20"]=ema(x.obv,20)
    typical=(x.high+x.low+x.close)/3
    x["vwap20"]=((typical*x.volume).rolling(20).sum()/x.volume.rolling(20).sum().replace(0,np.nan)).fillna(x.close)
    # Kaufman-style efficiency: directional progress divided by total path.
    path=x.close.diff().abs().rolling(20).sum()
    x["efficiency20"]=((x.close-x.close.shift(20)).abs()/path.replace(0,np.nan)).fillna(0)
    taker_buy=x["taker_buy_base"] if "taker_buy_base" in x else x.volume/2
    signed_volume=2*taker_buy-x.volume
    x["taker_imbalance10"]=(signed_volume.rolling(10).sum()/x.volume.rolling(10).sum().replace(0,np.nan)).fillna(0)
    x["cvd"]=signed_volume.cumsum(); x["cvd_ema20"]=ema(x.cvd,20)
    x["momentum24"]=(x.close/x.close.shift(24)-1)*100
    # Independent confirmations: price structure, volume-weighted money flow,
    # and an ATR trailing trend. They are deliberately not EMA derivatives.
    x["ichimoku_conversion"]=(x.high.rolling(9).max()+x.low.rolling(9).min())/2
    x["ichimoku_base"]=(x.high.rolling(26).max()+x.low.rolling(26).min())/2
    # Senkou spans are projected 26 bars forward. Therefore the cloud visible
    # at the current bar was calculated 26 bars earlier.
    span_a=(x.ichimoku_conversion+x.ichimoku_base)/2
    span_b=(x.high.rolling(52).max()+x.low.rolling(52).min())/2
    x["ichimoku_a"]=span_a.shift(26)
    x["ichimoku_b"]=span_b.shift(26)
    money_flow=typical*x.volume
    positive=money_flow.where(typical.diff()>0,0.0).rolling(14).sum()
    negative=money_flow.where(typical.diff()<0,0.0).rolling(14).sum()
    flow_ratio=positive/negative.replace(0,np.nan)
    x["mfi"]=100-(100/(1+flow_ratio))
    x["mfi"]=x.mfi.mask((negative==0)&(positive>0),100.0)
    x["mfi"]=x.mfi.mask((positive==0)&(negative>0),0.0)
    x["mfi"]=x.mfi.mask((positive==0)&(negative==0),50.0)
    multiplier=((x.close-x.low)-(x.high-x.close))/(x.high-x.low).replace(0,np.nan)
    x["cmf20"]=(multiplier*x.volume).rolling(20).sum()/x.volume.rolling(20).sum().replace(0,np.nan)
    x["cmf20"]=x.cmf20.fillna(0)
    basic_upper=(x.high+x.low)/2+3*x.atr
    basic_lower=(x.high+x.low)/2-3*x.atr
    final_upper=basic_upper.to_numpy(copy=True); final_lower=basic_lower.to_numpy(copy=True)
    upper=basic_upper.to_numpy(); lower=basic_lower.to_numpy(); close=x.close.to_numpy()
    trend=np.ones(len(x),dtype=int)
    for i in range(1,len(x)):
        if not np.isfinite(x.atr.iloc[i]):
            trend[i]=trend[i-1]; continue
        prev_upper=final_upper[i-1]
        prev_lower=final_lower[i-1]
        if np.isfinite(prev_upper) and not (upper[i]<prev_upper or close[i-1]>prev_upper):
            final_upper[i]=prev_upper
        if np.isfinite(prev_lower) and not (lower[i]>prev_lower or close[i-1]<prev_lower):
            final_lower[i]=prev_lower
        if trend[i-1]<0 and close[i]>final_upper[i]: trend[i]=1
        elif trend[i-1]>0 and close[i]<final_lower[i]: trend[i]=-1
        else: trend[i]=trend[i-1]
    x["supertrend_upper"]=final_upper; x["supertrend_lower"]=final_lower
    x["supertrend_dir"]=trend
    # Never silently fall back to an older candle because one oscillator is
    # undefined. Only the genuine Ichimoku/EMA warm-up rows are discarded.
    x=x.replace([np.inf,-np.inf],np.nan)
    return x.dropna(subset=["ema200","atr","ichimoku_a","ichimoku_b"])
