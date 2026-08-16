from dataclasses import dataclass
from .indicators import enrich

@dataclass
class Signal:
    symbol:str; timeframe:str; side:str; score:float
    entry_low:float; entry_high:float; stop:float
    tp1:float; tp2:float; tp3:float; rr:float; reasons:list
    funding:float=0; open_interest:float=0
    volatility_pct:float=0
    leverage:int=1; expected_window:str="6–48 часов"

def analyze(symbol,timeframe,df,higher=None,min_score=75,lower=None,market_bias=None,derivatives=None,news=None):
    x=enrich(df)
    if len(x)<220: return None
    h=enrich(higher) if higher is not None and len(higher)>220 else None
    a=x.iloc[-1]; p=x.iloc[-2]; price=float(a.close); vol=float(a.atr)
    L=S=0; lr=[]; sr=[]; htf_long=htf_short=False
    quality_long=quality_short=0
    if a.ema20>a.ema50>a.ema200: L+=18; lr.append("восходящий тренд EMA 20/50/200")
    if a.ema20<a.ema50<a.ema200: S+=18; sr.append("нисходящий тренд EMA 20/50/200")
    if a.adx>=20 and a.plus_di>a.minus_di: L+=10; lr.append(f"сила тренда ADX {a.adx:.0f}")
    if a.adx>=20 and a.minus_di>a.plus_di: S+=10; sr.append(f"сила тренда ADX {a.adx:.0f}")
    if 50<=a.rsi<=68: L+=8; lr.append(f"RSI {a.rsi:.1f}")
    if 32<=a.rsi<=50: S+=8; sr.append(f"RSI {a.rsi:.1f}")
    if a.macd_hist>0 and a.macd_hist>p.macd_hist: L+=8; lr.append("импульс MACD вверх")
    if a.macd_hist<0 and a.macd_hist<p.macd_hist: S+=8; sr.append("импульс MACD вниз")
    if a.vol_z>=0.5:
        if a.close>a.open: L+=8; lr.append("рост объёма покупателей")
        elif a.close<a.open: S+=8; sr.append("рост объёма продавцов")
    if a.close>a.high20: L+=7; lr.append("пробой максимума 20 свечей")
    if a.close<a.low20: S+=7; sr.append("пробой минимума 20 свечей")
    if a.stoch_rsi>p.stoch_rsi and 20<=a.stoch_rsi<=80: L+=5; lr.append("Stochastic RSI растёт")
    if a.stoch_rsi<p.stoch_rsi and 20<=a.stoch_rsi<=80: S+=5; sr.append("Stochastic RSI снижается")
    if a.close>a.vwap20: L+=4; lr.append("цена выше VWAP")
    if a.close<a.vwap20: S+=4; sr.append("цена ниже VWAP")
    if a.obv>a.obv_ema20: L+=4; lr.append("OBV подтверждает покупки")
    if a.obv<a.obv_ema20: S+=4; sr.append("OBV подтверждает продажи")
    if a.efficiency20<0.20:
        L-=10; S-=10; lr.append("штраф: рынок движется шумно"); sr.append("штраф: рынок движется шумно")
    elif a.efficiency20>=0.35:
        if a.close>a.ema50: L+=5; lr.append(f"устойчивость тренда {a.efficiency20:.2f}")
        if a.close<a.ema50: S+=5; sr.append(f"устойчивость тренда {a.efficiency20:.2f}")
    if a.taker_imbalance10>=0.04 and a.cvd>a.cvd_ema20:
        L+=6; lr.append(f"исторический taker-поток в покупки ({a.taker_imbalance10:+.1%})")
    if a.taker_imbalance10<=-0.04 and a.cvd<a.cvd_ema20:
        S+=6; sr.append(f"исторический taker-поток в продажи ({a.taker_imbalance10:+.1%})")
    if a.momentum24>=2 and a.close>a.ema50: L+=3; lr.append(f"импульс 24ч {a.momentum24:+.1f}%")
    if a.momentum24<=-2 and a.close<a.ema50: S+=3; sr.append(f"импульс 24ч {a.momentum24:+.1f}%")
    if a.close>a.bb_mid and a.bb_width>p.bb_width: L+=3; lr.append("Bollinger расширяется вверх")
    if a.close<a.bb_mid and a.bb_width>p.bb_width: S+=3; sr.append("Bollinger расширяется вниз")
    cloud_top=max(a.ichimoku_a,a.ichimoku_b); cloud_bottom=min(a.ichimoku_a,a.ichimoku_b)
    if a.close>cloud_top and a.ichimoku_conversion>a.ichimoku_base:
        L+=7; quality_long+=1; lr.append("Ichimoku подтверждает восходящую структуру")
    if a.close<cloud_bottom and a.ichimoku_conversion<a.ichimoku_base:
        S+=7; quality_short+=1; sr.append("Ichimoku подтверждает нисходящую структуру")
    if a.supertrend_dir>0:
        L+=5; quality_long+=1; lr.append("Supertrend направлен вверх")
    if a.supertrend_dir<0:
        S+=5; quality_short+=1; sr.append("Supertrend направлен вниз")
    if a.cmf20>=0.05 and 48<=a.mfi<=80:
        L+=6; quality_long+=1; lr.append(f"денежный поток CMF/MFI в покупки ({a.cmf20:+.2f}/{a.mfi:.0f})")
    if a.cmf20<=-0.05 and 20<=a.mfi<=52:
        S+=6; quality_short+=1; sr.append(f"денежный поток CMF/MFI в продажи ({a.cmf20:+.2f}/{a.mfi:.0f})")
    if h is not None:
        q=h.iloc[-1]
        higher_label="1H" if timeframe=="15M" else "4H"
        q_top=max(q.ichimoku_a,q.ichimoku_b); q_bottom=min(q.ichimoku_a,q.ichimoku_b)
        if q.ema20>q.ema50 and q.close>q.ema200 and q.close>q_top:
            L+=15; htf_long=True; quality_long+=1; lr.append(f"EMA и Ichimoku подтверждают тренд на {higher_label}")
        if q.ema20<q.ema50 and q.close<q.ema200 and q.close<q_bottom:
            S+=15; htf_short=True; quality_short+=1; sr.append(f"EMA и Ichimoku подтверждают тренд на {higher_label}")
    if lower is not None and len(lower)>50:
        z=enrich(lower).iloc[-1]
        lower_label="5m" if timeframe=="15M" else "15m"
        if z.close>z.ema20 and z.macd_hist>0: L+=6; quality_long+=1; lr.append(f"точка входа подтверждена на {lower_label}")
        if z.close<z.ema20 and z.macd_hist<0: S+=6; quality_short+=1; sr.append(f"точка входа подтверждена на {lower_label}")
    if market_bias=="LONG": L+=5; lr.append("рынок BTC направлен вверх")
    elif market_bias=="SHORT": S+=5; sr.append("рынок BTC направлен вниз")
    funding=float((derivatives or {}).get("funding",0))
    oi=float((derivatives or {}).get("open_interest",0))
    micro_long=micro_short=0
    if (derivatives or {}).get("deep_data"):
        oi_change=float(derivatives.get("oi_change_pct",0)); taker=float(derivatives.get("taker_ratio",1))
        book=float(derivatives.get("book_imbalance",0)); top=float(derivatives.get("top_position_ls",1))
        crowd=float(derivatives.get("global_ls",1)); spread=float(derivatives.get("spread_bps",999))
        basis=float(derivatives.get("basis_bps",0))
        if oi_change>=0.8:
            L+=4; S+=4; lr.append(f"open interest растёт на {oi_change:.1f}%"); sr.append(f"open interest растёт на {oi_change:.1f}%")
        if taker>=1.05: L+=6; micro_long+=1; quality_long+=1; lr.append(f"taker-покупки преобладают ({taker:.2f})")
        if taker<=0.95: S+=6; micro_short+=1; quality_short+=1; sr.append(f"taker-продажи преобладают ({taker:.2f})")
        if book>=0.08: L+=6; micro_long+=1; lr.append(f"дисбаланс стакана в покупки ({book:+.0%})")
        if book<=-0.08: S+=6; micro_short+=1; sr.append(f"дисбаланс стакана в продажи ({book:+.0%})")
        if top>=1.05: L+=4; micro_long+=1; lr.append(f"крупные позиции преимущественно LONG ({top:.2f})")
        if top<=0.95: S+=4; micro_short+=1; sr.append(f"крупные позиции преимущественно SHORT ({top:.2f})")
        if crowd>=1.8: L-=5; lr.append("штраф: толпа перегружена LONG")
        if crowd<=0.60: S-=5; sr.append("штраф: толпа перегружена SHORT")
        if basis>=12: L-=4; lr.append("штраф: фьючерс заметно выше индекса")
        if basis<=-12: S-=4; sr.append("штраф: фьючерс заметно ниже индекса")
        if spread>8: L-=12; S-=12; lr.append("штраф: широкий спред"); sr.append("штраф: широкий спред")
    if funding>0.0008: L-=8; lr.append("штраф: перегретый LONG по funding")
    if funding<-0.0008: S-=8; sr.append("штраф: перегретый SHORT по funding")
    news_score=float((news or {}).get("score",0))
    if news_score>=0.30: L+=4; lr.append(f"новостной фон позитивный ({news_score:+.2f})")
    if news_score<=-0.30: S+=4; sr.append(f"новостной фон негативный ({news_score:+.2f})")
    if news_score<=-0.65: L-=10; lr.append("штраф: сильный негативный новостной фон")
    if news_score>=0.65: S-=10; sr.append("штраф: сильный позитивный новостной фон")
    L=max(0,min(100,L)); S=max(0,min(100,S))
    support=float(x.low.tail(50).min()); resistance=float(x.high.tail(50).max())
    # A signal must agree with 4H, have a real trend and beat the opposite side clearly.
    deep_ok=not (derivatives or {}).get("deep_data") or micro_long>=2
    leverage=1 if float(a.atr_pct)>=1.5 else 2
    if L>=min_score and L-S>=15 and htf_long and quality_long>=3 and a.adx>=18 and a.rsi<75 and deep_ok and news_score>-0.80:
        lo=price-vol*.25; hi=price+vol*.25
        stop=min(price-vol*1.5,support*.995); risk=max(price-stop,vol*.5)
        return Signal(symbol,timeframe,"LONG",L,lo,hi,stop,price+risk,price+2*risk,price+3*risk,2,lr,funding,oi,float(a.atr_pct),leverage)
    deep_ok=not (derivatives or {}).get("deep_data") or micro_short>=2
    if S>=min_score and S-L>=15 and htf_short and quality_short>=3 and a.adx>=18 and a.rsi>25 and deep_ok and news_score<0.80:
        lo=price-vol*.25; hi=price+vol*.25
        stop=max(price+vol*1.5,resistance*1.005); risk=max(stop-price,vol*.5)
        return Signal(symbol,timeframe,"SHORT",S,lo,hi,stop,price-risk,price-2*risk,price-3*risk,2,sr,funding,oi,float(a.atr_pct),leverage)
    return None

def fmt(s,priority=False):
    e="🟢" if s.side=="LONG" else "🔴"
    title="🏆 <b>ПРИОРИТЕТНЫЙ СИГНАЛ</b> 🏆\n\n" if priority else "📌 <b>ДОПОЛНИТЕЛЬНЫЙ СИГНАЛ</b>\n\n"
    side="ПОКУПКА (LONG)" if s.side=="LONG" else "ПРОДАЖА (SHORT)"
    return (title+f"{e} <b>{side} — {s.symbol}</b>\n⏱ Таймфрейм: <b>{s.timeframe}</b>\n"
            f"💎 Сила сигнала: <b>{s.score:.0f}/100</b>\n\n🎯 Зона входа: <b>{s.entry_low:.8g} – {s.entry_high:.8g}</b>\n"
            f"🛑 Стоп-лосс: <b>{s.stop:.8g}</b>\n✅ Цель 1: <b>{s.tp1:.8g}</b>\n✅ Цель 2: <b>{s.tp2:.8g}</b>\n"
            f"✅ Цель 3: <b>{s.tp3:.8g}</b>\n⚖️ Риск/прибыль: <b>1:{s.rr:.1f}</b>\n"
            f"⚙️ Максимальное плечо: <b>{s.leverage}×</b>\n"
            f"🕒 Ожидаемое окно: <b>{s.expected_window}</b>\n"
            f"🔄 Пересмотр условий: <b>через 4 часа</b>\n"
            f"💰 Funding: <b>{s.funding*100:.4f}%</b>\n\n🔍 <b>Почему:</b>\n• "+"\n• ".join(s.reasons)+
            "\n\n⚠️ Срок ориентировочный, прибыль не гарантирована. Риск на одну сделку — не более 0,25–0,5% капитала.")
