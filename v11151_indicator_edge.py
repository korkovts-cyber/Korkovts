"""V11.15.1 Indicator Edge Pack: confirmation-only Futures context."""
from __future__ import annotations
import math
from typing import Any
import numpy as np
import pandas as pd
try:
    from v11_live import flow as live_flow
except Exception:
    live_flow=None
SCHEMA="11.15.1-indicator-edge-v1"

def _f(v,d=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else float(d)
    except Exception:return float(d)

def _frame(df):
    if df is None or not hasattr(df,"columns") or len(df)<25:return None
    if not {"high","low","close","volume"}.issubset(df.columns):return None
    x=df.copy()
    for c in ("open","high","low","close","volume","taker_buy_base"):
        if c in x:x[c]=pd.to_numeric(x[c],errors="coerce")
    x=x.replace([np.inf,-np.inf],np.nan).dropna(subset=["high","low","close","volume"])
    return x if len(x)>=25 else None

def _atr(df,n=14):
    prev=df.close.shift(1)
    tr=pd.concat([(df.high-df.low).abs(),(df.high-prev).abs(),(df.low-prev).abs()],axis=1).max(axis=1)
    return _f(tr.tail(n).mean())

def _avwap(df,side):
    tail=df.tail(min(90,len(df))); look=tail.tail(min(60,len(tail))); side=str(side).upper()
    anchor=look.high.idxmax() if side=="SHORT" else look.low.idxmin()
    try:
        loc=tail.index.get_loc(anchor); loc=loc.start if isinstance(loc,slice) else int(np.asarray(loc).flat[0])
        a=tail.iloc[loc:]
    except Exception:a=tail.tail(30)
    typical=(a.high+a.low+a.close)/3; vol=a.volume.clip(lower=0); den=_f(vol.sum()); close=_f(df.close.iloc[-1])
    level=_f((typical*vol).sum()/den,close) if den>0 else close; atr=max(_atr(df),abs(close)*1e-6)
    signed=(close-level)/atr*(1 if side=="LONG" else -1)
    state="SUPPORT" if 0<=signed<=1.75 else ("CONFLICT" if signed<-.45 else "NEUTRAL")
    return {"level":level,"distance_atr_signed":signed,"state":state,"anchor_bars":len(a)}

def _volume_profile(df,side,bins=32):
    x=df.tail(min(120,len(df))); px=((x.high+x.low+x.close)/3).astype(float); vol=x.volume.astype(float).clip(lower=0)
    lo,hi=_f(px.min()),_f(px.max()); close=_f(df.close.iloc[-1]); atr=max(_atr(df),abs(close)*1e-6)
    if hi<=lo or _f(vol.sum())<=0:return {"poc":close,"state":"NEUTRAL","bins":0}
    edges=np.linspace(lo,hi,bins+1); ids=np.clip(np.digitize(px.to_numpy(),edges)-1,0,bins-1); hist=np.zeros(bins)
    for i,v in zip(ids,vol.to_numpy()):hist[int(i)]+=max(0.0,_f(v))
    j=int(np.argmax(hist)); poc=float((edges[j]+edges[j+1])/2); signed=(close-poc)/atr*(1 if str(side).upper()=="LONG" else -1)
    state="SUPPORT" if 0<=signed<=2 else ("CONFLICT" if signed<-.55 else "NEUTRAL")
    ci=int(np.clip(np.digitize([close],edges)[0]-1,0,bins-1)); density=float(hist[ci]/max(hist.max(),1e-12))
    return {"poc":poc,"distance_atr_signed":signed,"state":state,"bins":bins,"current_node_density":density,"node":"LVN" if density<=.35 else ("HVN" if density>=.75 else "MID")}

def _rvol(df):
    v=df.volume.astype(float); cur=_f(v.iloc[-1]); base=_f(v.iloc[-21:-1].median()) if len(v)>=21 else _f(v.iloc[:-1].median()); ratio=cur/base if base>0 else 1
    return {"ratio":ratio,"baseline":base,"state":"SUPPORT" if ratio>=1.25 else ("CONFLICT" if ratio<.60 else "NEUTRAL")}

def _sweep(df,side):
    prior=df.iloc[-24:-4]; recent=df.iloc[-4:]; hi=_f(prior.high.max()); lo=_f(prior.low.min())
    bull=bool(((recent.low<lo)&(recent.close>lo)).any()); bear=bool(((recent.high>hi)&(recent.close<hi)).any()); side=str(side).upper()
    aligned=bull if side=="LONG" else bear; adverse=bear if side=="LONG" else bull
    if aligned and not adverse:state,typ="SUPPORT",("LOW_SWEEP_RECLAIM" if side=="LONG" else "HIGH_SWEEP_REJECT")
    elif adverse and not aligned:state,typ="CONFLICT",("HIGH_SWEEP_REJECT" if side=="LONG" else "LOW_SWEEP_RECLAIM")
    else:state,typ="NEUTRAL",("MIXED" if aligned and adverse else "NONE")
    return {"state":state,"type":typ,"prior_high":hi,"prior_low":lo}

def _cvd(df,side):
    if df is None or "taker_buy_base" not in df:return {"state":"NEUTRAL","imbalance":0.0,"source":"unavailable"}
    x=df.tail(min(20,len(df))); vol=x.volume.astype(float).clip(lower=0); buy=x.taker_buy_base.astype(float).clip(lower=0); total=_f(vol.sum()); imbalance=_f((2*buy-vol).sum())/total if total>0 else 0
    signed=imbalance*(1 if str(side).upper()=="LONG" else -1); state="SUPPORT" if signed>=.06 else ("CONFLICT" if signed<=-.10 else "NEUTRAL")
    return {"state":state,"imbalance":imbalance,"source":"closed_kline_taker_delta"}

def _oi(d,side):
    d=dict(d or {}); oi=_f(d.get("oi_change_pct")); price=_f(d.get("price_change_pct")); directional=price*(1 if str(side).upper()=="LONG" else -1)
    if oi>=.2 and directional>=.15:state,label="SUPPORT","POSITION_EXPANSION"
    elif oi>=.8 and directional<=-.35:state,label="CONFLICT","OPPOSITE_POSITION_EXPANSION"
    elif oi<-.2 and directional>0:state,label="NEUTRAL","COVERING_MOVE"
    else:state,label="NEUTRAL","MIXED"
    return {"state":state,"label":label,"oi_change_pct":oi,"price_change_pct":price}

def compute_from_frames(signal:Any,base=None,lower=None,higher=None,derivatives=None):
    df=_frame(base); low=_frame(lower); side=str(getattr(signal,"side","")).upper(); p={"schema":SCHEMA,"available":bool(df is not None),"negative_only":True,"professional_rank_changed":False}
    if df is not None:p.update({"avwap":_avwap(df,side),"volume_profile":_volume_profile(df,side),"rvol":_rvol(df),"liquidity_sweep":_sweep(df,side)})
    else:p.update({"avwap":{"state":"NEUTRAL"},"volume_profile":{"state":"NEUTRAL"},"rvol":{"state":"NEUTRAL"},"liquidity_sweep":{"state":"NEUTRAL","type":"NONE"}})
    p["cvd"]=_cvd(low if low is not None else df,side); p["oi_matrix"]=_oi(derivatives,side); signal.feature_snapshot.setdefault("indicator_edge_v11151",{}).update(p); return signal

def annotate_live(signal:Any):
    p=signal.feature_snapshot.setdefault("indicator_edge_v11151",{}); side=str(getattr(signal,"side","")).upper(); row=None
    try:row=live_flow(str(getattr(signal,"symbol","")),60,20) if live_flow else None
    except Exception:row=None
    if row:
        imb=_f(row.get("imbalance")); signed=imb*(1 if side=="LONG" else -1); state="SUPPORT" if signed>=.08 else ("CONFLICT" if signed<=-.12 else "NEUTRAL")
        p["cvd_live"]={"state":state,"imbalance":imb,"buy_share":_f(row.get("buy_share"),.5),"coverage_sec":_f(row.get("coverage_sec")),"trades":int(row.get("trades",0) or 0),"source":"routed_aggTrade_60s"}
    else:p["cvd_live"]={"state":"NEUTRAL","source":"warming/unavailable"}
    return signal

def assessment(signal:Any):
    annotate_live(signal); p=dict((getattr(signal,"feature_snapshot",{}) or {}).get("indicator_edge_v11151") or {}); parts=[str((p.get("avwap") or {}).get("state","NEUTRAL")),str((p.get("volume_profile") or {}).get("state","NEUTRAL"))]
    location="CONFLICT" if "CONFLICT" in parts else ("SUPPORT" if "SUPPORT" in parts else "NEUTRAL"); participation=str((p.get("rvol") or {}).get("state","NEUTRAL")); structure=str((p.get("liquidity_sweep") or {}).get("state","NEUTRAL"))
    flow=str((p.get("cvd_live") or p.get("cvd") or {}).get("state","NEUTRAL")); positioning=str((p.get("oi_matrix") or {}).get("state","NEUTRAL"))
    unique={"location":location,"participation":participation,"structure":structure}; refinements={"flow":flow,"positioning":positioning}
    return {"available":bool(p.get("available")),"unique_families":unique,"unique_support":sum(v=="SUPPORT" for v in unique.values()),"unique_conflicts":sum(v=="CONFLICT" for v in unique.values()),"refinements":refinements,"refinement_conflicts":sum(v=="CONFLICT" for v in refinements.values()),"schema":SCHEMA}
