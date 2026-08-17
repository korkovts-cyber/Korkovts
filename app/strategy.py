import math
from dataclasses import dataclass, field

from .config import ROUND_TRIP_COST_PCT
from .indicators import enrich


def _strength_score(raw):
    """Monotonic 0–100 display index; not a probability of winning."""
    if raw<=0: return 0.0
    return min(99.9,100-45*math.exp(-raw/70))

@dataclass
class Signal:
    symbol:str; timeframe:str; side:str; score:float
    entry_low:float; entry_high:float; stop:float
    tp1:float; tp2:float; tp3:float; rr:float; reasons:list
    funding:float=0; open_interest:float=0
    volatility_pct:float=0
    leverage:int=1; expected_window:str="6–48 часов"
    setup_type:str="ТРЕНДОВОЕ ПРОДОЛЖЕНИЕ"
    review_window:str="4 часа"
    data_quality:int=0
    data_quality_total:int=9
    estimated_cost_r:float=0
    adl_risk:str="unknown"
    liquidation:dict=field(default_factory=dict)
    market_context:dict=field(default_factory=dict)
    feature_snapshot:dict=field(default_factory=dict)
    cluster_id:int=0
    cluster_size:int=1
    cluster_rank:int=1
    cluster_correlation:float=0

def analyze(symbol,timeframe,df,higher=None,min_score=75,lower=None,market_bias=None,
            derivatives=None,news=None,market_context=None,audit=None):
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
    momentum_label="6ч" if timeframe=="15M" else "24ч"
    if a.momentum24>=2 and a.close>a.ema50: L+=3; lr.append(f"импульс {momentum_label} {a.momentum24:+.1f}%")
    if a.momentum24<=-2 and a.close<a.ema50: S+=3; sr.append(f"импульс {momentum_label} {a.momentum24:+.1f}%")
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
    elif (market_context or {}).get("independent_mode"):
        lr.append("нейтральный BTC: монета подтверждается независимо")
        sr.append("нейтральный BTC: монета подтверждается независимо")
    funding=float((derivatives or {}).get("funding",0))
    oi=float((derivatives or {}).get("open_interest",0))
    oi_change=price_change=0.0
    taker=1.0; spread=0.0; crowd=1.0; basis=0.0; book=0.0; top=1.0; top_change=0.0
    if (derivatives or {}).get("deep_data"):
        oi_change=float(derivatives.get("oi_change_pct",0)); taker=float(derivatives.get("taker_ratio",1))
        book=float(derivatives.get("book_imbalance",0)); top=float(derivatives.get("top_position_ls",1))
        top_change=float(derivatives.get("top_position_change_pct",0))
        crowd=float(derivatives.get("global_ls",1)); spread=float(derivatives.get("spread_bps",999))
        basis=float(derivatives.get("basis_bps",0))
        lookback=12 if timeframe=="15M" else 3
        price_change=(price/float(x.close.iloc[-(lookback+1)])-1)*100 if len(x)>lookback else 0
        if oi_change>=0.8 and price_change>=0.4:
            L+=6; quality_long+=1; lr.append(f"цена и OI растут: формируются новые LONG ({price_change:+.1f}%/{oi_change:+.1f}%)")
        elif oi_change>=0.8 and price_change<=-0.4:
            S+=6; quality_short+=1; sr.append(f"цена падает при росте OI: формируются новые SHORT ({price_change:+.1f}%/{oi_change:+.1f}%)")
        elif oi_change<=-0.8 and price_change>=0.4:
            L+=2; lr.append("рост преимущественно за счёт закрытия SHORT — подтверждение слабее")
        elif oi_change<=-0.8 and price_change<=-0.4:
            S+=2; sr.append("падение преимущественно за счёт ликвидации LONG — подтверждение слабее")
        if taker>=1.05: L+=6; quality_long+=1; lr.append(f"taker-покупки преобладают ({taker:.2f})")
        if taker<=0.95: S+=6; quality_short+=1; sr.append(f"taker-продажи преобладают ({taker:.2f})")
        # One REST order-book snapshot is easy to spoof, so it is context only,
        # not an independent confirmation.
        if book>=0.08: L+=2; lr.append(f"моментальный дисбаланс стакана в покупки ({book:+.0%})")
        if book<=-0.08: S+=2; sr.append(f"моментальный дисбаланс стакана в продажи ({book:+.0%})")
        if top>=1.05: L+=2; lr.append(f"крупные позиции преимущественно LONG ({top:.2f})")
        if top<=0.95: S+=2; sr.append(f"крупные позиции преимущественно SHORT ({top:.2f})")
        if crowd>=1.8: L-=5; lr.append("штраф: толпа перегружена LONG")
        if crowd<=0.60: S-=5; sr.append("штраф: толпа перегружена SHORT")
        if basis>=12: L-=4; lr.append("штраф: фьючерс заметно выше индекса")
        if basis<=-12: S-=4; sr.append("штраф: фьючерс заметно ниже индекса")
        if spread>8: L-=12; S-=12; lr.append("штраф: широкий спред"); sr.append("штраф: широкий спред")
    if funding>0.0008: L-=8; lr.append("штраф: перегретый LONG по funding")
    if funding<-0.0008: S-=8; sr.append("штраф: перегретый SHORT по funding")
    adl_risk=str((derivatives or {}).get("adl_risk","unknown")).lower()
    adl_fresh=bool((derivatives or {}).get("adl_fresh",False))
    if derivatives is not None and adl_risk=="medium":
        L-=4; S-=4
        lr.append("штраф: средний системный риск ADL Binance")
        sr.append("штраф: средний системный риск ADL Binance")
    if derivatives is not None and adl_risk=="high":
        lr.append("блокировка: высокий системный риск ADL Binance")
        sr.append("блокировка: высокий системный риск ADL Binance")
    news_score=float((news or {}).get("score",0))
    news_ok=news is None or int((news or {}).get("sources",0))>=1
    high_impact_news=int((news or {}).get("high_impact_count",0))
    breaking_news=bool((news or {}).get("breaking",False))
    # News can confirm an already valid technical setup, but the explicit
    # setup, HTF structure and complete derivatives gates below remain mandatory.
    if high_impact_news and news_score>=.45:
        L+=4; lr.append(f"сильная новость подтверждает LONG ({news_score:+.2f})")
    if high_impact_news and news_score<=-.45:
        S+=4; sr.append(f"сильная новость подтверждает SHORT ({news_score:+.2f})")
    if news_score>=0.30: lr.append(f"новостной фон позитивный ({news_score:+.2f})")
    if news_score<=-0.30: sr.append(f"новостной фон негативный ({news_score:+.2f})")
    if news_score<=-0.65: L-=10; lr.append("штраф: сильный негативный новостной фон")
    if news_score>=0.65: S-=10; sr.append("штраф: сильный позитивный новостной фон")
    # Keep uncapped evidence totals for ranking. Capping here made many very
    # different setups all appear as 100/100 and chose the priority arbitrarily.
    L=max(0,L); S=max(0,S)
    support=float(x.low.tail(3).min()); resistance=float(x.high.tail(3).max())
    # Do not let a loose sum of indicators create a trade. Every signal must
    # belong to one explicit, forward-testable setup: a confirmed breakout or
    # a controlled pullback inside an established trend.
    distance_atr=(price-float(a.ema20))/vol if vol else 999
    long_trend=a.ema20>a.ema50>a.ema200
    short_trend=a.ema20<a.ema50<a.ema200
    breakout_long=(long_trend and a.close>a.high20 and a.vol_z>=0.8
                   and a.efficiency20>=0.25 and 0<=distance_atr<=1.8)
    breakout_short=(short_trend and a.close<a.low20 and a.vol_z>=0.8
                    and a.efficiency20>=0.25 and -1.8<=distance_atr<=0)
    pullback_long=(long_trend and 0<=distance_atr<=0.75 and a.close>a.open
                   and a.macd_hist>0 and a.macd_hist>p.macd_hist
                   and a.stoch_rsi>p.stoch_rsi)
    pullback_short=(short_trend and -0.75<=distance_atr<=0 and a.close<a.open
                    and a.macd_hist<0 and a.macd_hist<p.macd_hist
                    and a.stoch_rsi<p.stoch_rsi)
    # A healthy trend often resumes without revisiting EMA20 or printing a new
    # 20-bar extreme.  The previous release recognized only those two rare
    # entry shapes and could therefore reject every liquid market even when
    # several symbols had aligned HTF trend, momentum and money flow.  Admit a
    # controlled continuation only while price is not overextended and the
    # move remains efficient; all final derivatives/ADL/spread gates still run.
    extended_long=distance_atr>1.60
    extended_short=distance_atr< -1.60
    continuation_long=(long_trend and .35<=distance_atr<=1.90
                       and a.adx>=22 and a.efficiency20>=.25
                       and 52<=a.rsi<=70 and a.macd_hist>0 and a.close>a.vwap20
                       and (not extended_long or a.close<p.close))
    continuation_short=(short_trend and -1.90<=distance_atr<=-.35
                        and a.adx>=22 and a.efficiency20>=.25
                        and 30<=a.rsi<=48 and a.macd_hist<0 and a.close<a.vwap20
                        and (not extended_short or a.close>p.close))
    if derivatives is not None:
        breakout_long=breakout_long and oi_change>=0.5 and price_change>=0.3 and taker>=1.03
        breakout_short=breakout_short and oi_change>=0.5 and price_change<=-0.3 and taker<=0.97
        continuation_long=(continuation_long and oi_change>=.2
                           and price_change>=.15 and taker>=1.03)
        continuation_short=(continuation_short and oi_change>=.2
                            and price_change<=-.15 and taker<=.97)
    setup_long=("ПРОБОЙ С ОБЪЁМОМ И OI" if breakout_long else
                ("ОТКАТ ПО ТРЕНДУ" if pullback_long else
                 ("КОНТРОЛИРУЕМОЕ ПРОДОЛЖЕНИЕ ТРЕНДА" if continuation_long else None)))
    setup_short=("ПРОБОЙ С ОБЪЁМОМ И OI" if breakout_short else
                 ("ОТКАТ ПО ТРЕНДУ" if pullback_short else
                  ("КОНТРОЛИРУЕМОЕ ПРОДОЛЖЕНИЕ ТРЕНДА" if continuation_short else None)))
    if setup_long: lr.append(f"сценарий: {setup_long.lower()}")
    if setup_short: sr.append(f"сценарий: {setup_short.lower()}")

    liquidation={key:(derivatives or {}).get(key) for key in (
        "liquidation_stream_ready","liquidation_window_min","liquidation_events",
        "liquidated_longs_usd","liquidated_shorts_usd","liquidation_notional_usd",
        "liquidation_intensity_bps","market_liquidation_usd")}
    liquidation={key:value for key,value in liquidation.items() if value is not None}

    def feature_snapshot(side,setup):
        return {
            "decision":{"side":side,"setup":setup,"threshold":float(min_score),
                        "raw_long":float(L),"raw_short":float(S),"score_gap":float(abs(L-S))},
            "technical":{"close":price,"atr":vol,"atr_pct":float(a.atr_pct),
                         "adx":float(a.adx),"plus_di":float(a.plus_di),"minus_di":float(a.minus_di),
                         "rsi":float(a.rsi),"stoch_rsi":float(a.stoch_rsi),
                         "macd_hist":float(a.macd_hist),"vol_z":float(a.vol_z),
                         "efficiency20":float(a.efficiency20),"distance_ema20_atr":float(distance_atr),
                         "cmf20":float(a.cmf20),"mfi":float(a.mfi),
                         "taker_imbalance10":float(a.taker_imbalance10)},
            "derivatives":{"funding":funding,"open_interest":oi,"oi_change_pct":oi_change,
                           "price_change_pct":price_change,"taker_ratio":taker,
                           "global_long_short":crowd,"top_position_long_short":top,
                           "top_position_change_pct":top_change,
                           "book_imbalance":book,"spread_bps":spread,"basis_bps":basis,
                           "basis_change_24h_bps":float((derivatives or {}).get("basis_change_24h_bps",0)),
                           "adl_risk":adl_risk,
                           "adl_age_minutes":float((derivatives or {}).get("adl_age_minutes",9999)),
                           "data_quality":int((derivatives or {}).get("data_quality",0)),
                           "data_quality_total":int((derivatives or {}).get("data_quality_total",9))},
            "liquidations":liquidation,
            "news":{"score":news_score,"sources":int((news or {}).get("sources",0)),
                    "event_risk":float((news or {}).get("event_risk",0)),
                    "high_impact_count":high_impact_news,"breaking":breaking_news},
            "market":dict(market_context or {"bias":market_bias}),
        }

    # Final signals require a complete-enough derivatives snapshot.  The
    # absolute top-trader ratio is useful context but has a persistent market
    # bias, so it no longer has to cross 1.00 in the trade direction.  Instead,
    # directional taker flow is mandatory and a rapid top-position move against
    # the setup remains a veto.
    adl_ok=(derivatives is None or (adl_risk in ("low","medium") and adl_fresh))
    top_long_ok=derivatives is None or top_change>=-5.0
    top_short_ok=derivatives is None or top_change<=5.0
    taker_long_ok=derivatives is None or taker>=1.03
    taker_short_ok=derivatives is None or taker<=.97
    deep_ok=(derivatives is None or ((derivatives or {}).get("deep_data") and adl_ok
                                    and taker_long_ok and top_long_ok
                                    and oi_change>=-0.5 and spread<=5 and funding<=0.0012
                                    and crowd<1.8 and basis<20))
    regime_ok=market_bias in (None,"LONG")
    leverage=1 if float(a.atr_pct)>=1.5 or adl_risk=="medium" else 2

    def write_audit():
        if audit is None:
            return
        use_long=L>=S
        side="LONG" if use_long else "SHORT"
        setup=setup_long if use_long else setup_short
        raw=float(L if use_long else S); other=float(S if use_long else L)
        htf=htf_long if use_long else htf_short
        quality=quality_long if use_long else quality_short
        trend=long_trend if use_long else short_trend
        issues=[]
        if not trend: issues.append("EMA-тренд не выстроен")
        if not setup:
            if abs(distance_atr)>1.90: issues.append("цена слишком далеко от EMA20")
            elif abs(distance_atr)>1.60: issues.append("ждём первый откат после импульса")
            elif float(a.adx)<22: issues.append("недостаточный импульс ADX")
            elif derivatives is not None: issues.append("цена/OI/taker не подтвердили вход")
            else: issues.append("нет подтверждённой точки входа")
        if raw<float(min_score): issues.append(f"оценка {raw:.0f} ниже {float(min_score):.0f}")
        if raw-other<15: issues.append("направление неоднозначно")
        if not htf: issues.append("старший таймфрейм не подтвердил")
        if quality<3: issues.append(f"подтверждений качества {quality}/3")
        if market_bias not in (None,side): issues.append("направление против режима BTC")
        if derivatives is not None:
            if not (derivatives or {}).get("deep_data"): issues.append("неполные данные деривативов")
            if not adl_ok: issues.append(f"ADL {adl_risk.upper()} или устарел")
            if spread>5: issues.append(f"спред {spread:.1f} б.п. слишком широк")
            if oi_change<-.5: issues.append(f"OI снижается {oi_change:+.1f}%")
            if use_long and not taker_long_ok: issues.append(f"taker {taker:.2f} не подтверждает LONG")
            if not use_long and not taker_short_ok: issues.append(f"taker {taker:.2f} не подтверждает SHORT")
            if use_long and not top_long_ok: issues.append("крупные позиции быстро сокращают LONG")
            if not use_long and not top_short_ok: issues.append("крупные позиции быстро наращивают LONG")
        audit.update({"symbol":symbol,"timeframe":timeframe,"side":side,"raw":raw,
                      "opposite":other,"threshold":float(min_score),"setup":setup,
                      "distance_atr":float(distance_atr),"adx":float(a.adx),
                      "htf":bool(htf),"quality":int(quality),"issues":issues,"passed":False})

    write_audit()
    if (setup_long and regime_ok and L>=min_score and L-S>=15 and htf_long
            and quality_long>=3 and a.adx>=18 and a.rsi<75 and deep_ok and news_ok
            and news_score>-0.80):
        if breakout_long:
            anchor=float(a.high20); lo=anchor-vol*.10; hi=anchor+vol*.10; entry=hi
            stop=anchor-vol*1.25
        elif continuation_long:
            if extended_long:
                lo=price-vol*.35; hi=price-vol*.15; entry=hi
                stop=min(entry-vol*1.25,float(a.ema20)-vol*.20)
            else:
                lo=price-vol*.10; hi=price+vol*.10; entry=hi
                stop=min(entry-vol*1.25,float(a.ema20)-vol*.35)
        else:
            lo=price-vol*.20; hi=price+vol*.05; entry=hi
            stop=min(entry-vol*1.35,support-vol*.10)
        risk=entry-stop
        if not vol*.75<=risk<=vol*2.2: return None
        cost_r=entry*(ROUND_TRIP_COST_PCT/100)/risk
        if cost_r>0.25: return None
        if audit is not None: audit["passed"]=True
        return Signal(symbol,timeframe,"LONG",_strength_score(L),lo,hi,stop,entry+risk,entry+2*risk,entry+3*risk,2,lr,
                      funding=funding,open_interest=oi,volatility_pct=float(a.atr_pct),
                      leverage=leverage,setup_type=setup_long,
                      review_window="1 час" if timeframe=="15M" else "4 часа",
                      data_quality=int((derivatives or {}).get("data_quality",0)),
                      data_quality_total=int((derivatives or {}).get("data_quality_total",9)),
                      estimated_cost_r=cost_r,adl_risk=adl_risk,liquidation=liquidation,
                      market_context=dict(market_context or {"bias":market_bias}),
                      feature_snapshot=feature_snapshot("LONG",setup_long))
    deep_ok=(derivatives is None or ((derivatives or {}).get("deep_data") and adl_ok
                                    and taker_short_ok and top_short_ok
                                    and oi_change>=-0.5 and spread<=5 and funding>=-0.0012
                                    and crowd>0.60 and basis>-20))
    regime_ok=market_bias in (None,"SHORT")
    if (setup_short and regime_ok and S>=min_score and S-L>=15 and htf_short
            and quality_short>=3 and a.adx>=18 and a.rsi>25 and deep_ok and news_ok
            and news_score<0.80):
        if breakout_short:
            anchor=float(a.low20); lo=anchor-vol*.10; hi=anchor+vol*.10; entry=lo
            stop=anchor+vol*1.25
        elif continuation_short:
            if extended_short:
                lo=price+vol*.15; hi=price+vol*.35; entry=lo
                stop=max(entry+vol*1.25,float(a.ema20)+vol*.20)
            else:
                lo=price-vol*.10; hi=price+vol*.10; entry=lo
                stop=max(entry+vol*1.25,float(a.ema20)+vol*.35)
        else:
            lo=price-vol*.05; hi=price+vol*.20; entry=lo
            stop=max(entry+vol*1.35,resistance+vol*.10)
        risk=stop-entry
        if not vol*.75<=risk<=vol*2.2: return None
        cost_r=entry*(ROUND_TRIP_COST_PCT/100)/risk
        if cost_r>0.25: return None
        if audit is not None: audit["passed"]=True
        return Signal(symbol,timeframe,"SHORT",_strength_score(S),lo,hi,stop,entry-risk,entry-2*risk,entry-3*risk,2,sr,
                      funding=funding,open_interest=oi,volatility_pct=float(a.atr_pct),
                      leverage=leverage,setup_type=setup_short,
                      review_window="1 час" if timeframe=="15M" else "4 часа",
                      data_quality=int((derivatives or {}).get("data_quality",0)),
                      data_quality_total=int((derivatives or {}).get("data_quality_total",9)),
                      estimated_cost_r=cost_r,adl_risk=adl_risk,liquidation=liquidation,
                      market_context=dict(market_context or {"bias":market_bias}),
                      feature_snapshot=feature_snapshot("SHORT",setup_short))
    return None

def fmt(s,priority=False):
    e="🟢" if s.side=="LONG" else "🔴"
    title="🏆 <b>ПРИОРИТЕТНЫЙ СИГНАЛ</b> 🏆\n\n" if priority else "📌 <b>ДОПОЛНИТЕЛЬНЫЙ СИГНАЛ</b>\n\n"
    side="ПОКУПКА (LONG)" if s.side=="LONG" else "ПРОДАЖА (SHORT)"
    breadth=(s.market_context or {}).get("breadth",{})
    breadth_text=(f"{float(breadth.get('up_ratio',.5))*100:.0f}% монет растут"
                  if breadth else "нет данных")
    cluster=(f"#{s.cluster_id} · лидер" if s.cluster_rank==1
             else f"#{s.cluster_id} · коррелирующий сигнал {s.cluster_rank}/{s.cluster_size}")
    liquidation_text=(f"{float(s.liquidation.get('liquidation_intensity_bps',0)):.2f} б.п. OI"
                      if s.liquidation.get("liquidation_stream_ready") else "наблюдение / прогрев")
    news_snapshot=(s.feature_snapshot or {}).get("news",{})
    news_risk="ПОВЫШЕННЫЙ" if float(news_snapshot.get("event_risk",0))>=.67 else "НОРМАЛЬНЫЙ / НЕТ СОБЫТИЙ"
    reasons=list(dict.fromkeys(s.reasons))
    shown=reasons[:10]
    hidden=f"\n• …ещё подтверждений: {len(reasons)-len(shown)}" if len(reasons)>len(shown) else ""
    return (title+f"{e} <b>{side} — {s.symbol}</b>\n⏱ Таймфрейм: <b>{s.timeframe}</b>\n"
            f"💎 Индекс условий: <b>{s.score:.0f}/100</b> <i>(не вероятность)</i>\n"
            f"🧩 Сценарий: <b>{s.setup_type}</b>\n"
            f"🧺 Кластер риска: <b>{cluster}</b>\n\n🎯 Зона входа: <b>{s.entry_low:.8g} – {s.entry_high:.8g}</b>\n"
            f"🛑 Стоп-лосс: <b>{s.stop:.8g}</b>\n✅ Цель 1: <b>{s.tp1:.8g}</b>\n✅ Цель 2: <b>{s.tp2:.8g}</b>\n"
            f"✅ Цель 3: <b>{s.tp3:.8g}</b>\n⚖️ Риск/прибыль: <b>1:{s.rr:.1f}</b>\n"
            f"🧾 Оценка издержек: <b>{s.estimated_cost_r:.2f}R</b>\n"
            f"⚙️ Максимальное плечо: <b>{s.leverage}×</b>\n"
            f"🕒 Ожидаемое окно: <b>{s.expected_window}</b>\n"
            f"🔄 Пересмотр условий: <b>через {s.review_window}</b>\n"
            f"📡 Полнота рыночных данных: <b>{s.data_quality}/{s.data_quality_total}</b>\n"
            f"🧯 ADL-риск Binance: <b>{s.adl_risk.upper()}</b>\n"
            f"🌐 Ширина рынка: <b>{breadth_text}</b>\n"
            f"💥 Ликвидации 15м: <b>{liquidation_text}</b>\n"
            f"📰 Событийный риск: <b>{news_risk}</b> <i>(телеметрия)</i>\n"
            f"💰 Funding: <b>{s.funding*100:.4f}%</b>\n\n🔍 <b>Ключевые подтверждения:</b>\n• "+"\n• ".join(shown)+hidden+
            "\n\n⚠️ Срок ориентировочный, прибыль не гарантирована. Риск на одну сделку — не более 0,25–0,5% капитала.")
