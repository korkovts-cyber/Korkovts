from dataclasses import dataclass
from .indicators import enrich

@dataclass
class Signal:
    symbol:str; timeframe:str; side:str; score:float
    entry_low:float; entry_high:float; stop:float
    tp1:float; tp2:float; tp3:float; rr:float; reasons:list
    funding:float=0; open_interest:float=0

def analyze(symbol,timeframe,df,higher=None,min_score=75,lower=None,market_bias=None,derivatives=None):
    x=enrich(df)
    if len(x)<220: return None
    h=enrich(higher) if higher is not None and len(higher)>220 else None
    a=x.iloc[-1]; p=x.iloc[-2]; price=float(a.close); vol=float(a.atr)
    L=S=0; lr=[]; sr=[]
    if a.ema20>a.ema50>a.ema200: L+=22; lr.append("EMA 20/50/200 bullish")
    if a.ema20<a.ema50<a.ema200: S+=22; sr.append("EMA 20/50/200 bearish")
    if a.adx>=20 and a.plus_di>a.minus_di: L+=12; lr.append(f"ADX {a.adx:.0f} confirms")
    if a.adx>=20 and a.minus_di>a.plus_di: S+=12; sr.append(f"ADX {a.adx:.0f} confirms")
    if 50<=a.rsi<=68: L+=10; lr.append(f"RSI {a.rsi:.1f}")
    if 32<=a.rsi<=50: S+=10; sr.append(f"RSI {a.rsi:.1f}")
    if a.macd_hist>0 and a.macd_hist>p.macd_hist: L+=10; lr.append("MACD momentum up")
    if a.macd_hist<0 and a.macd_hist<p.macd_hist: S+=10; sr.append("MACD momentum down")
    if a.vol_z>=0.5:
        if a.close>a.open: L+=8; lr.append("volume expansion")
        elif a.close<a.open: S+=8; sr.append("volume expansion")
    if a.close>a.high20: L+=8; lr.append("20-bar breakout")
    if a.close<a.low20: S+=8; sr.append("20-bar breakdown")
    if h is not None:
        q=h.iloc[-1]
        if q.ema20>q.ema50 and q.close>q.ema200: L+=15; lr.append("4H confirms")
        if q.ema20<q.ema50 and q.close<q.ema200: S+=15; sr.append("4H confirms")
    if lower is not None and len(lower)>50:
        z=enrich(lower).iloc[-1]
        if z.close>z.ema20 and z.macd_hist>0: L+=8; lr.append("15m timing confirms")
        if z.close<z.ema20 and z.macd_hist<0: S+=8; sr.append("15m timing confirms")
    if market_bias=="LONG": L+=7; lr.append("BTC market regime bullish")
    elif market_bias=="SHORT": S+=7; sr.append("BTC market regime bearish")
    funding=float((derivatives or {}).get("funding",0))
    oi=float((derivatives or {}).get("open_interest",0))
    if funding>0.0008: L-=8; lr.append("crowded long funding penalty")
    if funding<-0.0008: S-=8; sr.append("crowded short funding penalty")
    L=max(0,min(100,L)); S=max(0,min(100,S))
    support=float(x.low.tail(50).min()); resistance=float(x.high.tail(50).max())
    if L>=min_score and L>=S:
        lo=price-vol*.25; hi=price+vol*.25
        stop=min(price-vol*1.5,support*.995); risk=max(price-stop,vol*.5)
        return Signal(symbol,timeframe,"LONG",L,lo,hi,stop,price+risk,price+2*risk,price+3*risk,2,lr,funding,oi)
    if S>=min_score and S>L:
        lo=price-vol*.25; hi=price+vol*.25
        stop=max(price+vol*1.5,resistance*1.005); risk=max(stop-price,vol*.5)
        return Signal(symbol,timeframe,"SHORT",S,lo,hi,stop,price-risk,price-2*risk,price-3*risk,2,sr,funding,oi)
    return None

def fmt(s):
    e="🟢" if s.side=="LONG" else "🔴"
    return (f"{e} <b>{s.side} — {s.symbol}</b>\nTF: <b>{s.timeframe}</b>\n"
            f"Score: <b>{s.score:.0f}/100</b>\nEntry: <b>{s.entry_low:.8g} – {s.entry_high:.8g}</b>\n"
            f"SL: <b>{s.stop:.8g}</b>\nTP1: <b>{s.tp1:.8g}</b>\nTP2: <b>{s.tp2:.8g}</b>\n"
            f"TP3: <b>{s.tp3:.8g}</b>\nR:R: <b>1:{s.rr:.1f}</b>\n"
            f"Funding: <b>{s.funding*100:.4f}%</b>\n"
            f"Why: {', '.join(s.reasons)}\n\n⚠️ Research signal, not financial advice.")
