from dataclasses import dataclass
from .indicators import enrich

@dataclass
class Signal:
    symbol:str; timeframe:str; side:str; score:float
    entry_low:float; entry_high:float; stop:float
    tp1:float; tp2:float; tp3:float; rr:float; reasons:list

def analyze(symbol,timeframe,df,higher=None,min_score=75):
    x=enrich(df)
    if len(x)<220: return None
    h=enrich(higher) if higher is not None and len(higher)>220 else None
    a=x.iloc[-1]; p=x.iloc[-2]; price=float(a.close); vol=float(a.atr)
    L=S=0; lr=[]; sr=[]
    if a.ema20>a.ema50>a.ema200: L+=25; lr.append("bullish EMA trend")
    if a.ema20<a.ema50<a.ema200: S+=25; sr.append("bearish EMA trend")
    if 52<=a.rsi<=70: L+=15; lr.append(f"RSI {a.rsi:.1f}")
    if 30<=a.rsi<=48: S+=15; sr.append(f"RSI {a.rsi:.1f}")
    if a.macd_hist>0 and a.macd_hist>p.macd_hist: L+=15; lr.append("MACD rising")
    if a.macd_hist<0 and a.macd_hist<p.macd_hist: S+=15; sr.append("MACD falling")
    if a.volume>a.vol_ma20*1.2:
        if a.close>a.open: L+=15; lr.append("volume expansion")
        elif a.close<a.open: S+=15; sr.append("volume expansion")
    if h is not None:
        q=h.iloc[-1]
        if q.ema20>q.ema50>q.ema200: L+=20; lr.append("4H confirms")
        if q.ema20<q.ema50<q.ema200: S+=20; sr.append("4H confirms")
    support=float(x.low.tail(50).min()); resistance=float(x.high.tail(50).max())
    if L>=min_score and L>=S:
        lo=price-vol*.25; hi=price+vol*.25
        stop=min(price-vol*1.5,support*.995); risk=max(price-stop,vol*.5)
        return Signal(symbol,timeframe,"LONG",L,lo,hi,stop,price+risk,price+2*risk,price+3*risk,2,lr)
    if S>=min_score and S>L:
        lo=price-vol*.25; hi=price+vol*.25
        stop=max(price+vol*1.5,resistance*1.005); risk=max(stop-price,vol*.5)
        return Signal(symbol,timeframe,"SHORT",S,lo,hi,stop,price-risk,price-2*risk,price-3*risk,2,sr)
    return None

def fmt(s):
    e="🟢" if s.side=="LONG" else "🔴"
    return (f"{e} <b>{s.side} — {s.symbol}</b>\nTF: <b>{s.timeframe}</b>\n"
            f"Score: <b>{s.score:.0f}/100</b>\nEntry: <b>{s.entry_low:.8g} – {s.entry_high:.8g}</b>\n"
            f"SL: <b>{s.stop:.8g}</b>\nTP1: <b>{s.tp1:.8g}</b>\nTP2: <b>{s.tp2:.8g}</b>\n"
            f"TP3: <b>{s.tp3:.8g}</b>\nR:R: <b>1:{s.rr:.1f}</b>\n"
            f"Why: {', '.join(s.reasons)}")
