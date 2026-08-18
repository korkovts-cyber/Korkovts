"""V11.7.1 Spot 3–10 day strategy.

The score is a quality index, not a probability of profit. News can veto or
modestly confirm but never manufacture a BUY. Derivatives are only a crowding
risk overlay; Spot market data remains the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass,field
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import math

from spot_indicators import enrich

@dataclass
class SpotSignal:
    symbol:str
    base_asset:str
    status:str
    score:float
    setup_type:str
    entry_low:float
    entry_high:float
    invalidation:float
    tp1:float
    tp2:float
    tp3:float
    horizon:str="3–10 дней"
    relative_percentile:float=0.0
    excess_btc_14d:float=0.0
    reasons:list=field(default_factory=list)
    risks:list=field(default_factory=list)
    market_regime:str="UNKNOWN"
    market_breadth:float=.5
    dispersion_7d:float=0.0
    news:dict=field(default_factory=dict)
    micro:dict=field(default_factory=dict)
    derivatives_risk:dict=field(default_factory=dict)
    feature_snapshot:dict=field(default_factory=dict)


def _finite(v,default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def preliminary_score(daily_feature,relative_percentile,excess_btc_14d):
    a=daily_feature
    score=0.0
    if _finite(a.get("close"))>_finite(a.get("ema100")): score+=18
    if _finite(a.get("ema20"))>_finite(a.get("ema50"))>_finite(a.get("ema100")): score+=12
    if 48<=_finite(a.get("rsi"),50)<=70: score+=8
    if _finite(a.get("ret14"))>0: score+=7
    if _finite(a.get("ret30"))>0: score+=5
    if _finite(a.get("path_eff14"))>=.28: score+=10
    if _finite(a.get("positive_days14"))>=.55: score+=5
    if _finite(a.get("cmf20"))>=.03: score+=6
    if _finite(a.get("volume_ratio7_30"))>=1.0: score+=5
    score+=max(0,min(18,(float(relative_percentile)-50)*.36))
    score+=max(0,min(6,float(excess_btc_14d)*.5))
    return score


def derivatives_crowding(snapshot):
    d=snapshot or {}
    if not d or not d.get("available",False):
        counterpart=bool(d.get("counterpart",False))
        degraded=bool(d.get("degraded",False) or counterpart)
        return {"available":False,"counterpart":counterpart,"degraded":degraded,
                "extreme":False,"penalty":(-3 if degraded else 0),
                "reason":("futures crowding data unavailable" if degraded else "no futures counterpart")}
    funding=_finite(d.get("funding")); oi=_finite(d.get("oi_change_pct")); crowd=_finite(d.get("global_ls"),1)
    top=_finite(d.get("top_position_ls"),1); taker=_finite(d.get("taker_ratio"),1)
    extreme=(abs(funding)>=.002) or (funding>=.001 and oi>=5) or (crowd>=2.5 and top>=2.5 and taker>=1.25)
    penalty=-8 if extreme else (-3 if funding>=.0008 and oi>=3 else 0)
    reason=(f"funding {funding*100:+.3f}% · OI {oi:+.1f}% · crowd {crowd:.2f}")
    return {"available":True,"counterpart":bool(d.get("counterpart",True)),"degraded":False,
            "extreme":extreme,"penalty":penalty,"reason":reason,
            "funding":funding,"oi_change_pct":oi,"global_ls":crowd,"top_position_ls":top,"taker_ratio":taker}


def analyze(symbol,base_asset,daily,frame4h,frame1h,relative_percentile,excess_btc_14d,
            market,news,micro,derivatives=None):
    xd=enrich(daily); x4=enrich(frame4h); x1=enrich(frame1h)
    if len(xd)<220 or len(x4)<120 or len(x1)<80:
        return None
    a=xd.iloc[-1]; b=x4.iloc[-1]; c=x1.iloc[-1]
    price=_finite(b.close); atr4=_finite(b.atr); atrd=_finite(a.atr)
    live_price=_finite((micro or {}).get("ask") or (micro or {}).get("mid") or price,price)
    if price<=0 or live_price<=0 or atr4<=0 or atrd<=0:
        return None

    reasons=[]; risks=[]; score=0.0

    # Daily trend / age quality.
    daily_close=_finite(a.close)
    daily_above100=daily_close>_finite(a.ema100)
    daily_stack=_finite(a.ema20)>_finite(a.ema50)>_finite(a.ema100)
    ema200=_finite(a.ema200,0)
    daily_above200=(ema200<=0 or daily_close>ema200)
    if daily_above100: score+=14; reasons.append("1D цена выше EMA100")
    if daily_stack: score+=12; reasons.append("1D EMA20/50/100 выстроены вверх")
    if daily_above200: score+=5
    else: risks.append("цена ниже 1D EMA200")

    # Weekly-horizon momentum must be positive but not a vertical pump.
    ret7=_finite(a.ret7); ret14=_finite(a.ret14); ret30=_finite(a.ret30)
    if 1<=ret14<=30: score+=8; reasons.append(f"14D momentum {ret14:+.1f}%")
    elif ret14>40: risks.append("14D движение перегрето")
    if ret30>0: score+=4
    path=_finite(a.path_eff14); posdays=_finite(a.positive_days14)
    if path>=.30: score+=9; reasons.append(f"плавный price path {path:.2f}")
    if posdays>=.55: score+=5; reasons.append(f"положительных дней 14D {posdays*100:.0f}%")
    if _finite(a.max_day14)<=12: score+=3
    else: risks.append("рост слишком зависит от одной вертикальной свечи")

    # Money/volume accumulation.
    if _finite(a.cmf20)>=.04: score+=6; reasons.append(f"CMF accumulation {_finite(a.cmf20):+.2f}")
    if 50<=_finite(a.mfi,50)<=80: score+=3; reasons.append(f"MFI {_finite(a.mfi):.0f}")
    if _finite(a.obv)>_finite(a.obv_ema20): score+=4; reasons.append("OBV подтверждает накопление")
    if _finite(a.volume_ratio7_30)>=1.05: score+=4; reasons.append(f"7D volume {_finite(a.volume_ratio7_30):.2f}× 30D median")
    if _finite(a.taker_buy_share7)>=.51: score+=3

    # 4H timing / setup.
    trend4=_finite(b.ema20)>_finite(b.ema50) and price>_finite(b.ema50)
    if trend4: score+=10; reasons.append("4H trend подтверждён")
    if _finite(b.adx)>=18 and _finite(b.plus_di)>_finite(b.minus_di): score+=4
    if 45<=_finite(b.rsi,50)<=69: score+=4
    if _finite(c.close)>_finite(c.ema20) and _finite(c.macd_hist)>=0: score+=3
    if price>=_finite(b.vwap20,price): score+=2; reasons.append("4H цена выше rolling VWAP20")

    breakout_level=_finite(b.high20_prev,price)
    breakout=price>breakout_level and _finite(b.volume_ratio7_30)>=1.05
    compression=_finite(b.bb_width)<_finite(b.bb_width_med60,999)
    if breakout:
        setup="ACCUMULATION BREAKOUT"
        support=max(_finite(b.ema20),breakout_level)
        entry_low=support-.15*atr4; entry_high=support+.35*atr4
        score+=4; reasons.append("4H breakout с объёмом")
    elif compression and trend4 and _finite(b.dist_ema20_atr)<=1.2:
        setup="COMPRESSION CONTINUATION"
        support=max(_finite(b.ema20),_finite(b.ema50))
        entry_low=support-.20*atr4; entry_high=support+.35*atr4
        score+=3; reasons.append("4H compression перед продолжением")
    else:
        setup="CONTROLLED CONTINUATION"
        support=max(_finite(b.ema20),_finite(b.ema50))
        entry_low=support-.20*atr4; entry_high=support+.40*atr4

    # Relative strength is essential for a long-only weekly Spot book.
    rp=float(relative_percentile)
    if rp>=90: score+=12; reasons.append(f"relative strength TOP {100-rp:.0f}%")
    elif rp>=75: score+=8; reasons.append(f"relative strength {rp:.0f} percentile")
    elif rp>=60: score+=3
    if excess_btc_14d>=3: score+=5; reasons.append(f"BTC excess 14D {excess_btc_14d:+.1f}%")
    elif excess_btc_14d<0: risks.append("за 14D слабее BTC")

    # Market state / dispersion.
    regime=str(market.get("regime","UNKNOWN"))
    breadth=float(market.get("breadth",.5))
    dispersion=float(market.get("dispersion_7d",0))
    if regime=="BULL": score+=6
    elif regime=="BEAR": risks.append("общий Spot market regime BEAR")
    if breadth>=.55: score+=3
    if market.get("dispersion_risk"):
        score-=4; risks.append("аномально высокая cross-sectional dispersion — BUY приостановлен")
    if market.get("risk_off"):
        score-=5; risks.append("BTC/breadth risk-off — BUY приостановлен")
    if not daily_stack:
        risks.append("1D EMA20/50/100 ещё не выстроены вверх")
    if _finite(a.min_day14)<-12:
        risks.append("за 14D был слишком резкий отрицательный день")

    # Entry location: strong setup may become WATCH instead of chasing.
    extension4=_finite(b.dist_ema20_atr)
    extension1=(_finite(c.close)-_finite(c.ema20))/_finite(c.atr,1)
    in_zone=entry_low<=live_price<=entry_high
    overextended=extension4>1.8 or extension1>2.0 or live_price>entry_high+.35*atr4
    if in_zone: score+=7; reasons.append("цена в Spot BUY zone")
    elif overextended: risks.append("цена выше хорошей зоны — не догонять")

    # Spot L2 and actual Spot taker flow.
    if micro.get("excellent"):
        score+=6; reasons.append("Spot L2/flow excellent")
    elif micro.get("healthy"):
        score+=3; reasons.append("Spot ликвидность здорова")
    else:
        risks.append("Spot execution quality недостаточна")
    if (
        micro.get("flow_reliable")
        and float(micro.get("buy_share",.5))>=.54
        and float(micro.get("closed_buy_share_15m",.5))>=.52
    ):
        score+=3
        reasons.append(
            f"Spot flow live/15m {float(micro.get('buy_share'))*100:.0f}%/"
            f"{float(micro.get('closed_buy_share_15m'))*100:.0f}%"
        )

    # News is a veto/confirmation layer, never standalone alpha.
    score+=int(news.get("adjustment",0) or 0)
    if news.get("catalyst"):
        reasons.append("позитивный catalyst подтверждён несколькими источниками")
    if news.get("block"):
        risks.append("asset-specific news risk")
    if news.get("global_breaking"):
        risks.append("свежий high-impact market event — BUY ждёт стабилизации")

    crowd=derivatives_crowding(derivatives)
    score+=int(crowd.get("penalty",0))
    if crowd.get("extreme"):
        risks.append("фьючерсная толпа перегрета — риск покупки вершины")
    elif crowd.get("degraded"):
        risks.append("futures crowding overlay недоступен — BUY понижен до WATCH")
    if news.get("degraded"):
        risks.append("новостной слой недоступен — BUY понижен до WATCH")

    # Pre-compute invalidation and overhead-supply headroom before deciding BUY.
    invalidation=max(0.0,min(support-1.20*atr4,_finite(b.low20_prev,support-.8*atr4)))
    if invalidation<=0 or invalidation>=entry_low:
        invalidation=max(0.0,entry_low-1.15*atr4)
    risk_per_unit=max(0.0,live_price-invalidation)
    overhead=[
        level for level in (_finite(a.high20_prev,0),_finite(a.high55_prev,0))
        if level>live_price*1.001
    ]
    nearest_resistance=min(overhead) if overhead else 0.0
    headroom_r=(
        (nearest_resistance-live_price)/risk_per_unit
        if nearest_resistance>0 and risk_per_unit>0 else 999.0
    )
    headroom_ok=(headroom_r>=.75)
    if not headroom_ok:
        risks.append(f"слишком близкое дневное сопротивление: {headroom_r:.2f}R")

    # Hard BUY conditions. A high score cannot rescue a structurally bad entry.
    # Weekly Spot entries still require short-horizon confirmation so a brief
    # aggressive-flow burst cannot override a weakening 1H tape.
    hourly_ok=(
        _finite(c.close)>_finite(c.ema20)
        and _finite(c.macd_hist)>=0
        and _finite(c.taker_buy_share7,.5)>=.50
    )
    execution_ok=(
        bool(micro.get("healthy"))
        and float(micro.get("spread_bps",999))<=6.0
        and float(micro.get("impact_5k_bps",999))<=15.0
        and float(micro.get("book_imbalance_20bps",0))>=-.30
    )
    required_rp=75.0 if regime=="BULL" else 85.0
    required_score=82.0 if regime=="BULL" else 86.0
    hard_buy=(
        daily_above100 and daily_above200 and daily_stack and trend4 and hourly_ok and regime!="BEAR"
        and rp>=required_rp and 0<ret14<=40 and ret30>0 and path>=.25 and posdays>=.50
        and _finite(a.max_day14)<=15 and _finite(a.min_day14)>=-12
        and not bool(market.get("dispersion_risk")) and not bool(market.get("risk_off"))
        and not overextended and in_zone and headroom_ok
        and execution_ok and bool(micro.get("flow_reliable"))
        and bool(micro.get("closed_flow_ok"))
        and float(micro.get("buy_share",.5))>=.52
        and float(micro.get("closed_buy_share_5m",.5))>=.50
        and float(micro.get("closed_buy_share_15m",.5))>=.50
        and not bool(news.get("block")) and not bool(news.get("recent_negative"))
        and not bool(news.get("degraded")) and not bool(news.get("global_breaking"))
        and not bool(crowd.get("extreme")) and not bool(crowd.get("degraded"))
    )
    score=max(0,min(99,float(score)))
    if hard_buy and score>=required_score:
        status="BUY"
    elif score>=70 and daily_above100 and trend4 and regime!="BEAR":
        status="WATCH"
    else:
        return None

    mid=(entry_low+entry_high)/2
    basis=max(live_price,mid)
    risk_for_targets=max(basis-invalidation,.25*atr4)
    tp1=basis+max(.85*atrd,1.00*risk_for_targets)
    tp2=basis+max(1.55*atrd,1.80*risk_for_targets)
    tp3=basis+max(2.30*atrd,2.60*risk_for_targets)

    snap={
        "daily":{"ret7":ret7,"ret14":ret14,"ret30":ret30,"path_eff14":path,
                 "positive_days14":posdays,"cmf20":_finite(a.cmf20),
                 "volume_ratio7_30":_finite(a.volume_ratio7_30),"rsi":_finite(a.rsi)},
        "4h":{"rsi":_finite(b.rsi),"adx":_finite(b.adx),"dist_ema20_atr":extension4,
              "ema20":_finite(b.ema20),"ema50":_finite(b.ema50)},
        "relative_percentile":rp,"excess_btc_14d":float(excess_btc_14d),"live_price":live_price,
        "required_relative_percentile":required_rp,"required_score":required_score,
        "nearest_resistance":nearest_resistance,"headroom_r":headroom_r,
        "market":dict(market),"news":dict(news),"micro":dict(micro),"derivatives":dict(crowd),
    }
    return SpotSignal(
        symbol=str(symbol),base_asset=str(base_asset),status=status,score=score,
        setup_type=setup,entry_low=entry_low,entry_high=entry_high,
        invalidation=invalidation,tp1=tp1,tp2=tp2,tp3=tp3,
        relative_percentile=rp,excess_btc_14d=float(excess_btc_14d),
        reasons=reasons[:8],risks=risks[:6],market_regime=regime,
        market_breadth=breadth,dispersion_7d=dispersion,news=dict(news),micro=dict(micro),
        derivatives_risk=dict(crowd),feature_snapshot=snap,
    )


def _round_tick(value,tick,mode):
    tick=Decimal(str(tick or 0)); value=Decimal(str(value))
    if tick<=0:
        return float(value)
    units=value/tick
    rounded=units.to_integral_value(rounding=ROUND_FLOOR if mode=="floor" else ROUND_CEILING)
    return float(rounded*tick)


def normalize(signal,meta):
    """Round Spot geometry to exchange PRICE_FILTER and reject invalid results."""
    tick=float(getattr(meta,"tick_size",0) or 0)
    if tick<=0:
        return None
    signal.entry_low=_round_tick(signal.entry_low,tick,"floor")
    signal.entry_high=_round_tick(signal.entry_high,tick,"ceil")
    signal.invalidation=_round_tick(signal.invalidation,tick,"floor")
    signal.tp1=_round_tick(signal.tp1,tick,"ceil")
    signal.tp2=_round_tick(signal.tp2,tick,"ceil")
    signal.tp3=_round_tick(signal.tp3,tick,"ceil")
    values=(signal.invalidation,signal.entry_low,signal.entry_high,signal.tp1,signal.tp2,signal.tp3)
    if not (values[0]<values[1]<=values[2]<values[3]<values[4]<values[5]):
        return None
    minp=float(getattr(meta,"min_price",0) or 0); maxp=float(getattr(meta,"max_price",0) or 0)
    if minp>0 and any(v<minp for v in values):
        return None
    if maxp>0 and any(v>maxp for v in values):
        return None
    signal.feature_snapshot.setdefault("spot_exchange",{}).update({
        "tick_size":tick,"min_price":minp,"max_price":maxp,
    })
    return signal
