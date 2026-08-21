"""V11.22.5 · Pipeline independence + timeout recovery."""
from __future__ import annotations
import asyncio
import bot_v11191 as runtime
import v11191_spot_engine as spot_engine
import spot_scanner as spot_legacy
import v11191_futures_engine as futures

base=runtime.base
VERSION="11.22.5"

# Spot no longer waits behind the Futures research gate.
_original_daily=spot_legacy._daily
async def daily_v11225(symbol):
    try:
        return await asyncio.wait_for(_original_daily(symbol),timeout=45.0)
    except asyncio.TimeoutError:
        return None,f"{symbol}: DAILY_TIMEOUT_45S"
spot_legacy._daily=daily_v11225

async def spot_scan_independent_v11225(*args,**kwargs):
    return await spot_engine.scan(*args,**kwargs)
base.spot_scan=spot_scan_independent_v11225

# Fast derivatives stage is ranking-only. If auxiliary OI ranking stalls, fall
# back to technical ranking; every selected name still must pass full-deep.
_original_quick_screen=futures.quick_deep_screen

def _technical_meta(row):
    symbol,lower,basef,higher,soft_l,soft_s=row
    long_side=float(soft_l)>=float(soft_s)
    soft=max(float(soft_l),float(soft_s))
    return {"symbol":symbol,"side":"LONG" if long_side else "SHORT","score":soft,"soft":soft,
            "funding_pct":0.0,"open_interest":0.0,"oi_notional":0.0,"technical_fallback":True}

async def quick_screen_v11225(rows,tickers):
    rows=list(rows or [])
    if not rows:
        return [],{"status":"EMPTY","requested":0,"complete":0,"coverage":0.0}
    try:
        screened,diag=await asyncio.wait_for(_original_quick_screen(rows,tickers),timeout=55.0)
        diag=dict(diag or {}); diag["v11225_mode"]="DERIVATIVES_RANK"
        return screened,diag
    except asyncio.TimeoutError:
        ranked=[(_technical_meta(row),row) for row in rows]
        ranked.sort(key=lambda x:float(x[0]["score"]),reverse=True)
        return ranked,{"status":"DEGRADED_RANKING_ONLY","requested":len(rows),"complete":len(rows),
                       "coverage":1.0,"cancelled":0,"errors":0,"elapsed_sec":55.0,
                       "premium_bulk_available":False,"v11225_mode":"TECHNICAL_RANK_FALLBACK",
                       "reason":"auxiliary fast derivatives ranking timeout; full-deep remains mandatory"}
futures.quick_deep_screen=quick_screen_v11225

_original_select=futures.select_full_deep
def select_full_deep_v11225(screened,target=None,min_opposite=3):
    rows=list(screened or [])
    if not rows: return []
    scores=sorted((float(x[0].get("score",0) or 0) for x in rows),reverse=True)
    strong=sum(s>=78.0 for s in scores); very_strong=sum(s>=84.0 for s in scores)
    adaptive=14
    if strong>=18: adaptive=16
    if strong>=22 or very_strong>=12: adaptive=18
    if strong>=28 or very_strong>=16: adaptive=20
    return _original_select(rows,min(adaptive,len(rows)),min_opposite)
futures.select_full_deep=select_full_deep_v11225

_old_hb=base.heartbeat_text
def heartbeat_v11225(diagnostics,**kwargs):
    text=_old_hb(diagnostics,**kwargs)
    try:
        d=dict(diagnostics or {}); screen=dict(d.get("deep_screen") or {})
        mode=str(screen.get("v11225_mode") or "")
        if mode: text+=f"\n🧬 Fast-screen mode: <b>{base.escape(mode)}</b>"
        text+="\n🟢 Spot/Futures research locks: <b>INDEPENDENT</b>"
    except Exception: pass
    return text
base.heartbeat_text=heartbeat_v11225

_old_health=base.health_text
def health_v11225(h):
    text=_old_health(h)
    for old in ("V11.22.4","V11.22.3","V11.22.2","V11.22.1","V11.22.0","V11.21.9","V11.21.8","V11.21.7","V11.21.6"):
        text=text.replace(old,VERSION)
    return text
base.health_text=health_v11225

def install():
    spot_legacy._daily=daily_v11225
    base.spot_scan=spot_scan_independent_v11225
    futures.quick_deep_screen=quick_screen_v11225
    futures.select_full_deep=select_full_deep_v11225
    base.APP_VERSION=VERSION; base.config.APP_VERSION=VERSION; base.core.APP_VERSION=VERSION
    return True
