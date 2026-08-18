"""Independent evidence-family conflict audit for V11.7.1.

The purpose is not to add another indicator score. It prevents correlated
technical filters from being mistaken for independent evidence and vetoes a
signal when a genuinely independent family materially disagrees.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Family:
    name:str
    state:str       # SUPPORT / NEUTRAL / CONFLICT
    detail:str
    hard:bool=False


@dataclass(frozen=True)
class Audit:
    eligible:bool
    support:int
    neutral:int
    conflict:int
    hard_conflicts:tuple[str,...]
    families:tuple[Family,...]
    summary:str


def _f(x,default=0.0):
    try:return float(x)
    except Exception:return float(default)


def _family(name,state,detail,hard=False):
    return Family(name,str(state),str(detail),bool(hard))


def _finish(families,min_support):
    support=sum(f.state=="SUPPORT" for f in families)
    neutral=sum(f.state=="NEUTRAL" for f in families)
    conflict=sum(f.state=="CONFLICT" for f in families)
    hard=tuple(f"{f.name}: {f.detail}" for f in families if f.state=="CONFLICT" and f.hard)
    eligible=(not hard and support>=int(min_support) and conflict<=1)
    summary=(
        f"{support} independent families support · {conflict} conflict"
        + (f" · hard: {hard[0]}" if hard else "")
    )
    return Audit(eligible,support,neutral,conflict,hard,tuple(families),summary)


def futures(signal:Any):
    f=dict(getattr(signal,"feature_snapshot",{}) or {})
    decision=dict(f.get("decision") or {})
    tech=dict(f.get("technical") or {})
    der=dict(f.get("derivatives") or {})
    news=dict(f.get("news") or {})
    market=dict(f.get("market") or getattr(signal,"market_context",{}) or {})
    alpha=dict(f.get("alpha_v112") or {})
    ex=dict(f.get("execution_v113") or f.get("execution_v1121") or {})
    micro=dict(f.get("micro_v113") or {})
    side=str(getattr(signal,"side",decision.get("side") or "")).upper()
    long=side=="LONG"
    families=[]

    gap=_f(decision.get("score_gap"),99)
    if gap>=20:
        families.append(_family("direction separation","SUPPORT",f"raw gap {gap:.0f}"))
    elif gap>=15:
        families.append(_family("direction separation","NEUTRAL",f"raw gap {gap:.0f}"))
    else:
        families.append(_family("direction separation","CONFLICT",f"raw gap only {gap:.0f}",True))

    adx=_f(tech.get("adx")); plus=_f(tech.get("plus_di")); minus=_f(tech.get("minus_di"))
    di_ok=(plus>minus) if long else (minus>plus)
    if adx>=22 and di_ok:
        families.append(_family("trend strength","SUPPORT",f"ADX {adx:.1f}, DI aligned"))
    elif adx>=20 and not di_ok:
        families.append(_family("trend strength","CONFLICT",f"ADX {adx:.1f}, DI opposite",True))
    else:
        families.append(_family("trend strength","NEUTRAL",f"ADX {adx:.1f}"))

    rsi=_f(tech.get("rsi"),50); macd=_f(tech.get("macd_hist"))
    momentum_ok=(macd>0 and 48<=rsi<=72) if long else (macd<0 and 28<=rsi<=52)
    momentum_bad=(macd<0 and rsi<48) if long else (macd>0 and rsi>52)
    if momentum_ok:
        families.append(_family("momentum","SUPPORT",f"RSI {rsi:.1f}, MACD aligned"))
    elif momentum_bad:
        families.append(_family("momentum","CONFLICT",f"RSI {rsi:.1f}, MACD opposite"))
    else:
        families.append(_family("momentum","NEUTRAL",f"RSI {rsi:.1f}"))

    taker=_f(der.get("taker_ratio"),1); hist_ofi=_f(tech.get("taker_imbalance10")); live_ofi=_f(alpha.get("ofi_5m"))
    flow_ok=(taker>=1.03 and hist_ofi>=0 and live_ofi>=-.02) if long else (taker<=.97 and hist_ofi<=0 and live_ofi<=.02)
    flow_bad=(taker<.97 or live_ofi<=-.06) if long else (taker>1.03 or live_ofi>=.06)
    if flow_ok:
        families.append(_family("order flow","SUPPORT",f"taker {taker:.2f}, OFI5 {live_ofi:+.0%}"))
    elif flow_bad:
        families.append(_family("order flow","CONFLICT",f"taker {taker:.2f}, OFI5 {live_ofi:+.0%}",True))
    else:
        families.append(_family("order flow","NEUTRAL",f"taker {taker:.2f}, OFI5 {live_ofi:+.0%}"))

    oi=_f(der.get("oi_change_pct")); price=_f(der.get("price_change_pct"))
    pos_ok=(oi>=.2 and price>=.15) if long else (oi>=.2 and price<=-.15)
    pos_bad=(oi>=.8 and price<=-.4) if long else (oi>=.8 and price>=.4)
    if pos_ok:
        families.append(_family("positioning","SUPPORT",f"price/OI {price:+.1f}%/{oi:+.1f}%"))
    elif pos_bad:
        families.append(_family("positioning","CONFLICT",f"price/OI {price:+.1f}%/{oi:+.1f}%",True))
    else:
        families.append(_family("positioning","NEUTRAL",f"price/OI {price:+.1f}%/{oi:+.1f}%"))

    bias=str(market.get("bias") or market.get("btc_bias_raw") or "NEUTRAL").upper()
    independent=bool(market.get("independent_mode"))
    breadth_blocked=bool(market.get("breadth_blocked"))
    if breadth_blocked:
        families.append(_family("market regime","CONFLICT","breadth conflict",True))
    elif bias==side or (bias=="NEUTRAL" and independent):
        families.append(_family("market regime","SUPPORT",f"BTC {bias}"))
    elif bias in ("LONG","SHORT") and bias!=side:
        families.append(_family("market regime","CONFLICT",f"BTC {bias} vs {side}",True))
    else:
        families.append(_family("market regime","NEUTRAL",f"BTC {bias}"))

    spread=_f(der.get("spread_bps"),999); l2=str(ex.get("l2_state") or "").upper()
    cost=_f(getattr(signal,"estimated_cost_r",0))
    if spread<=3 and cost<=.20 and l2 not in ("THIN","DEGRADED"):
        families.append(_family("execution","SUPPORT",f"spread {spread:.1f}bps, cost {cost:.2f}R"))
    elif spread>5 or cost>.25 or l2=="THIN":
        families.append(_family("execution","CONFLICT",f"spread {spread:.1f}bps, cost {cost:.2f}R, L2 {l2 or 'N/A'}",True))
    else:
        families.append(_family("execution","NEUTRAL",f"spread {spread:.1f}bps, cost {cost:.2f}R"))

    ns=_f(news.get("score")); breaking=bool(news.get("breaking")); event=_f(news.get("event_risk"))
    news_bad=(breaking and event>=.67) or (ns<=-.65 if long else ns>=.65)
    news_good=(ns>=.30 if long else ns<=-.30) and not breaking
    if news_bad:
        families.append(_family("news/event","CONFLICT",f"score {ns:+.2f}, event {event:.2f}",True))
    elif news_good:
        families.append(_family("news/event","SUPPORT",f"score {ns:+.2f}"))
    else:
        families.append(_family("news/event","NEUTRAL",f"score {ns:+.2f}"))

    meta=dict(f.get("meta_v113") or {})
    report=dict(meta.get("report") or {})
    ready=bool(meta.get("ready")); mscore=_f(meta.get("score"),.5); threshold=_f(meta.get("threshold"),.6)
    if ready and mscore>=threshold:
        families.append(_family("meta OOS","SUPPORT",f"{mscore:.2f} >= {threshold:.2f}"))
    elif ready and mscore<threshold:
        families.append(_family("meta OOS","CONFLICT",f"{mscore:.2f} < {threshold:.2f}",True))
    else:
        families.append(_family("meta OOS","NEUTRAL","learning / abstain"))

    audit=_finish(families,5)
    try:
        signal.feature_snapshot.setdefault("evidence_v117",{}).update({
            "eligible":audit.eligible,"support":audit.support,"neutral":audit.neutral,
            "conflict":audit.conflict,"hard_conflicts":list(audit.hard_conflicts),
            "families":[f.__dict__ for f in audit.families],"summary":audit.summary,
        })
        signal.evidence_support=audit.support
        signal.evidence_conflicts=audit.conflict
        signal.evidence_summary=audit.summary
    except Exception:
        pass
    return audit


def spot(signal:Any):
    snap=dict(getattr(signal,"feature_snapshot",{}) or {})
    daily=dict(snap.get("daily") or {})
    h4=dict(snap.get("4h") or {})
    market=dict(snap.get("market") or getattr(signal,"market_regime",{}) or {})
    news=dict(snap.get("news") or getattr(signal,"news",{}) or {})
    micro=dict(snap.get("micro") or getattr(signal,"micro",{}) or {})
    crowd=dict(snap.get("derivatives") or getattr(signal,"derivatives_risk",{}) or {})
    families=[]

    ret14=_f(daily.get("ret14")); ret30=_f(daily.get("ret30")); path=_f(daily.get("path_eff14"))
    if ret14>0 and ret30>0 and path>=.25:
        families.append(_family("trend persistence","SUPPORT",f"14D {ret14:+.1f}%, 30D {ret30:+.1f}%, path {path:.2f}"))
    elif ret14<=0 or ret30<=0:
        families.append(_family("trend persistence","CONFLICT",f"14D {ret14:+.1f}%, 30D {ret30:+.1f}%",True))
    else:
        families.append(_family("trend persistence","NEUTRAL",f"path {path:.2f}"))

    rp=_f(getattr(signal,"relative_percentile",snap.get("relative_percentile")),50)
    required=_f(snap.get("required_relative_percentile"),75)
    excess=_f(getattr(signal,"excess_btc_14d",snap.get("excess_btc_14d")))
    if rp>=required and excess>=0:
        families.append(_family("relative strength","SUPPORT",f"RS {rp:.0f}p, BTC excess {excess:+.1f}%"))
    elif rp<required-10 or excess<-5:
        families.append(_family("relative strength","CONFLICT",f"RS {rp:.0f}p, BTC excess {excess:+.1f}%",True))
    else:
        families.append(_family("relative strength","NEUTRAL",f"RS {rp:.0f}p"))

    cmf=_f(daily.get("cmf20")); live=_f(micro.get("buy_share"),.5); f5=_f(micro.get("closed_buy_share_5m"),.5); f15=_f(micro.get("closed_buy_share_15m"),.5)
    if cmf>=.03 and live>=.52 and f5>=.50 and f15>=.50 and micro.get("flow_reliable"):
        families.append(_family("accumulation/flow","SUPPORT",f"CMF {cmf:+.2f}, live/5/15 {live:.0%}/{f5:.0%}/{f15:.0%}"))
    elif live<.48 or f15<.48:
        families.append(_family("accumulation/flow","CONFLICT",f"live/15m {live:.0%}/{f15:.0%}",True))
    else:
        families.append(_family("accumulation/flow","NEUTRAL",f"CMF {cmf:+.2f}"))

    spread=_f(micro.get("spread_bps"),999); impact=_f(micro.get("impact_5k_bps"),999); imb=_f(micro.get("book_imbalance_20bps"))
    if micro.get("healthy") and spread<=4 and impact<=10 and imb>=-.20:
        families.append(_family("execution","SUPPORT",f"spread {spread:.1f}bps, impact {impact:.1f}bps"))
    elif spread>6 or impact>15 or imb<-.30:
        families.append(_family("execution","CONFLICT",f"spread {spread:.1f}, impact {impact:.1f}, imbalance {imb:+.2f}",True))
    else:
        families.append(_family("execution","NEUTRAL",f"spread {spread:.1f}bps"))

    regime=str(market.get("regime") or getattr(signal,"market_regime","")).upper()
    risk_off=bool(market.get("risk_off")); dispersion=bool(market.get("dispersion_risk"))
    if regime=="BULL" and not risk_off and not dispersion:
        families.append(_family("market regime","SUPPORT",regime))
    elif regime=="BEAR" or risk_off or dispersion:
        families.append(_family("market regime","CONFLICT",f"{regime}, risk_off={risk_off}, dispersion={dispersion}",True))
    else:
        families.append(_family("market regime","NEUTRAL",regime or "UNKNOWN"))

    if news.get("block") or news.get("recent_negative") or news.get("degraded") or news.get("global_breaking"):
        families.append(_family("news/event","CONFLICT","fresh negative/degraded/high-impact event",True))
    elif news.get("catalyst"):
        families.append(_family("news/event","SUPPORT","independent positive catalyst"))
    else:
        families.append(_family("news/event","NEUTRAL","no independent catalyst"))

    if crowd.get("extreme") or crowd.get("degraded"):
        families.append(_family("crowding","CONFLICT",str(crowd.get("reason") or "crowding risk"),True))
    elif crowd.get("available"):
        families.append(_family("crowding","SUPPORT","USD-M crowding acceptable"))
    else:
        families.append(_family("crowding","NEUTRAL","no USD-M counterpart"))

    head=_f(snap.get("headroom_r"),999)
    if head>=1.0:
        families.append(_family("reward headroom","SUPPORT",f"{head:.2f}R to daily resistance"))
    elif head<.75:
        families.append(_family("reward headroom","CONFLICT",f"only {head:.2f}R",True))
    else:
        families.append(_family("reward headroom","NEUTRAL",f"{head:.2f}R"))

    audit=_finish(families,5)
    try:
        signal.feature_snapshot.setdefault("evidence_v117",{}).update({
            "eligible":audit.eligible,"support":audit.support,"neutral":audit.neutral,
            "conflict":audit.conflict,"hard_conflicts":list(audit.hard_conflicts),
            "families":[f.__dict__ for f in audit.families],"summary":audit.summary,
        })
        signal.evidence_support=audit.support
        signal.evidence_conflicts=audit.conflict
        signal.evidence_summary=audit.summary
    except Exception:
        pass
    return audit


def short_text(audit:Audit):
    if audit.eligible:
        return f"{audit.support} independent confirmations · conflicts {audit.conflict}"
    if audit.hard_conflicts:
        return f"BLOCK: {audit.hard_conflicts[0]}"
    return f"only {audit.support} independent confirmations"
