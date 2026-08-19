from __future__ import annotations
from app.indicators import enrich
from app.strategy import Signal,_strength_score
from app.config import ROUND_TRIP_COST_PCT

def _f(x,d=0.0):
    try:return float(x)
    except Exception:return float(d)

def _recover(args,kwargs,audit):
    symbol=kwargs.get("symbol",args[0] if len(args)>0 else "")
    timeframe=kwargs.get("timeframe",args[1] if len(args)>1 else "1H")
    df=kwargs.get("df",args[2] if len(args)>2 else None)
    derivatives=kwargs.get("derivatives",args[7] if len(args)>7 else None)
    news=kwargs.get("news",args[8] if len(args)>8 else None)
    market_context=kwargs.get("market_context",args[9] if len(args)>9 else None)
    if df is None or not audit or list(audit.get("issues") or []): return None
    if not audit.get("setup") or not audit.get("htf") or int(audit.get("quality",0) or 0)<3:return None
    side=str(audit.get("side","")).upper(); raw=_f(audit.get("raw")); other=_f(audit.get("opposite"))
    if side not in ("LONG","SHORT") or raw<_f(audit.get("threshold"),75) or raw-other<15:return None
    x=enrich(df)
    if len(x)<220:return None
    a=x.iloc[-1]; price=_f(a.close); atr=_f(a.atr)
    if price<=0 or atr<=0 or _f(a.adx)<18:return None
    if side=="LONG" and _f(a.rsi)>=75:return None
    if side=="SHORT" and _f(a.rsi)<=25:return None
    ns=_f((news or {}).get("score"))
    sources=int((news or {}).get("sources",1) or 0) if news is not None else 1
    if sources<1:return None
    if side=="LONG" and ns<=-.80:return None
    if side=="SHORT" and ns>=.80:return None
    d=dict(derivatives or {})
    if derivatives is not None and not d.get("deep_data"):return None
    adl=str(d.get("adl_risk","unknown")).lower()
    if derivatives is not None and (adl=="high" or not d.get("adl_fresh",False)):return None
    if side=="LONG":
        lo=price-.60*atr; hi=price-.35*atr; stop=hi-1.35*atr; risk=hi-stop
        tp1,tp2,tp3=hi+risk,hi+2*risk,hi+3*risk
    else:
        lo=price+.35*atr; hi=price+.60*atr; stop=lo+1.35*atr; risk=stop-lo
        tp1,tp2,tp3=lo-risk,lo-2*risk,lo-3*risk
    if risk<=0:return None
    cost=price*(ROUND_TRIP_COST_PCT/100)/risk
    if cost>.25:return None
    lev=1 if _f(getattr(a,"atr_pct",0))>=1.5 or adl=="medium" else 2
    return Signal(str(symbol),str(timeframe),side,_strength_score(raw),lo,hi,stop,tp1,tp2,tp3,2.0,[
        "сильный сетап подтверждён, но текущая цена слишком далеко от безопасного стопа",
        "V11.19.5: вместо погони за ценой выставлена контролируемая pullback-зона",
        "ENTRY NOW повторно проверит live цену, L2 и поток перед публикацией",
    ],funding=_f(d.get("funding")),open_interest=_f(d.get("open_interest")),
      volatility_pct=_f(getattr(a,"atr_pct",0)),leverage=lev,
      setup_type="КОНТРОЛИРУЕМЫЙ PULLBACK",
      review_window="1 час" if str(timeframe).upper()=="15M" else "4 часа",
      data_quality=int(d.get("data_quality",0) or 0),
      data_quality_total=int(d.get("data_quality_total",9) or 9),
      estimated_cost_r=cost,adl_risk=adl,market_context=dict(market_context or {}),
      feature_snapshot={"v11195_geometry_recovery":{"recovered":True,"raw":raw,"opposite":other,
          "original_distance_atr":_f(audit.get("distance_atr")),"entry_offset_atr":.35,"risk_atr":1.35}})

def install(scanner_module,core_module=None):
    original=scanner_module.analyze
    if getattr(original,"_v11195_geometry_wrapper",False):return original
    def wrapped(*args,**kwargs):
        audit=kwargs.get("audit") if isinstance(kwargs.get("audit"),dict) else {}
        call=dict(kwargs); call["audit"]=audit
        result=original(*args,**call)
        if result is not None:return result
        result=_recover(args,call,audit)
        if result is not None:
            audit["passed"]=True; audit["geometry_recovered"]=True
        return result
    wrapped._v11195_geometry_wrapper=True
    scanner_module.analyze=wrapped
    if core_module is not None:core_module.analyze=wrapped
    return wrapped
