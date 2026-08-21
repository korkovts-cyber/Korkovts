"""V11.22.1 · production reachability fix."""
from __future__ import annotations
import asyncio, math, time
from collections import Counter
import bot_v11191 as runtime
import v1141_governor as governor
import v11200_data_architecture as data_arch
import v11196_api_resilience as api_resilience
import v11191_futures_engine as fut
import v11218_spot_entry_fix as spot218
base=runtime.base
VERSION="11.22.1"
WEIGHT_BUDGET_PER_MIN=780
WEIGHT_RESERVE_CRITICAL=120
_weight_lock=asyncio.Lock()
_weight_state={"minute":None,"reserved":0,"waits":0,"requests":0}

def _estimated_weight(path,params=None):
    try: return max(1,int(api_resilience._estimated_weight(path,params)))
    except Exception:
        path=str(path or ''); p=dict(params or {})
        if path.endswith('/ticker/24hr'): return 1 if p.get('symbol') else 40
        if path.endswith('/aggTrades'): return 20
        if path.endswith('/depth'): return 5
        if path.endswith('/klines'): return 2
        return 1

def _is_critical(path,params=None):
    try: return bool(api_resilience._critical(path,params))
    except Exception: return str(path or '').endswith('/time')

async def _reserve_weight(path,params=None):
    weight=_estimated_weight(path,params); critical=_is_critical(path,params)
    while True:
        now=time.time(); minute=int(now//60)
        async with _weight_lock:
            if _weight_state['minute']!=minute:
                _weight_state['minute']=minute; _weight_state['reserved']=0
            ceiling=WEIGHT_BUDGET_PER_MIN if critical else max(1,WEIGHT_BUDGET_PER_MIN-WEIGHT_RESERVE_CRITICAL)
            if int(_weight_state['reserved'])+weight<=ceiling:
                _weight_state['reserved']+=weight; _weight_state['requests']+=1; return
            _weight_state['waits']+=1
        await asyncio.sleep(max(.05,60.15-(now%60)))

_final_underlying_get=data_arch.governed_get
async def governed_get_v11221(path,params=None):
    await governor._wait_for_shared_cooldown()
    await _reserve_weight(path,params)
    return await _final_underlying_get(path,params)

def _rebind_request_aliases():
    governor.governed_get=governed_get_v11221
    governor.market._get=governed_get_v11221
    for module_name in ('v11_liquidity','v112_alpha','v112_health','v1141_integrity','v11197_sources'):
        try:
            m=__import__(module_name)
            if hasattr(m,'_get'): m._get=governed_get_v11221
        except Exception: pass
_rebind_request_aliases()
try:
    api_resilience.ANALYSIS_CONCURRENCY=2
    api_resilience._analysis_sem=asyncio.Semaphore(2)
except Exception: pass
data_arch.REQUESTS_PER_SEC=min(float(getattr(data_arch,'REQUESTS_PER_SEC',3.2) or 3.2),2.4)
data_arch.MIN_START_GAP=1.0/max(.5,data_arch.REQUESTS_PER_SEC)
api_resilience.SOFT_WEIGHT_CEILING=min(int(getattr(api_resilience,'SOFT_WEIGHT_CEILING',900) or 900),800)

_original_deep_one=fut._deep_one

def _adl_issue(issues):
    return any('ADL' in str(x).upper() and ('УСТАР' in str(x).upper() or 'UNKNOWN' in str(x).upper()) for x in (issues or []))

async def _deep_one_v11221(row,kind,market_context,news,adl_risks,min_score,sem):
    signal,reason,payload=await _original_deep_one(row,kind,market_context,news,adl_risks,min_score,sem)
    if signal is not None or reason!='FINAL_STRATEGY_REJECT' or not isinstance(payload,dict):
        return signal,reason,payload
    audit=dict(payload.get('_strategy_audit') or {}); issues=list(audit.get('issues') or [])
    actual_adl=str(payload.get('adl_risk','unknown') or 'unknown').lower(); actual_fresh=bool(payload.get('adl_fresh',False))
    if actual_adl=='high' or actual_fresh or not _adl_issue(issues):
        return signal,reason,payload
    try:
        symbol,lower,base_frame,higher,soft_l,soft_s=row
        d=dict(payload); d.update(adl_risk='low',adl_fresh=True,adl_age_minutes=0)
        timeframe='1H' if kind=='main' else '15M'; audit2={}
        recovered=fut.legacy.analyze(symbol,timeframe,base_frame,higher,float(min_score),lower,market_context.get('bias'),d,fut.legacy.for_symbol(news,symbol),market_context,audit=audit2)
        if recovered is None:
            inferred='LONG' if float(soft_l)>=float(soft_s) else 'SHORT'
            recovered=fut._momentum_fallback(symbol,timeframe,base_frame,higher,lower,inferred,d,market_context,fut.legacy.for_symbol(news,symbol),min_score)
        if recovered is None:
            payload['_adl_recovery_audit']=audit2; return signal,reason,payload
        recovered.adl_risk='unknown'; recovered.leverage=1
        recovered.reasons=list(getattr(recovered,'reasons',[]) or [])
        recovered.reasons.append('ADL telemetry stale/unknown: leverage capped; final risk gates remain')
        fs=dict(getattr(recovered,'feature_snapshot',{}) or {})
        fs.setdefault('v11221_adl',{}).update({'telemetry_degraded':True,'actual_adl_risk':actual_adl,'actual_adl_fresh':actual_fresh,'recovered_from_strategy_veto':True,'leverage_cap':1})
        if isinstance(fs.get('derivatives'),dict):
            fs['derivatives']['adl_risk']='unknown'; fs['derivatives']['adl_fresh']=False; fs['derivatives']['adl_telemetry_degraded']=True
        recovered.feature_snapshot=fs
        payload['_v11221_adl_recovered']=True; payload['_strategy_audit']=audit
        return recovered,'',payload
    except Exception as exc:
        payload['_v11221_adl_recovery_error']=f'{type(exc).__name__}: {exc}'
        return signal,reason,payload
fut._deep_one=_deep_one_v11221

_original_finish=fut._finish
def _finish_v11221(d,status,reason=''):
    counts=Counter()
    for row in list(d.get('deep_rejections') or []):
        for issue in row.get('issues') or []: counts[str(issue)]+=1
    d['strategy_issue_counts']=[{'issue':k,'count':v} for k,v in counts.most_common(5)]
    return _original_finish(d,status,reason)
fut._finish=_finish_v11221

def _near_original_zone_v11221(row,ask):
    try:
        ask=float(ask); lo=float(row.get('entry_low') or 0); hi=float(row.get('entry_high') or 0)
        if ask<=0 or lo<=0 or hi<lo: return False
        mid=(lo+hi)/2.0; width=max(hi-lo,mid*.0005); pad=max(width*1.50,mid*.0075)
        return (lo-pad)<=ask<=(hi+pad)
    except Exception: return False
spot218._near_original_zone=_near_original_zone_v11221

_old_hb=base.heartbeat_text
def heartbeat_text_v11221(diagnostics,**kwargs):
    text=_old_hb(diagnostics,**kwargs).replace('\\\\n','\n')
    try:
        issues=list((diagnostics or {}).get('strategy_issue_counts') or [])
        if issues:
            rendered=', '.join(f"{base.escape(str(x.get('issue','?')))} ×{int(x.get('count',0) or 0)}" for x in issues[:3])
            text+=f'\n🧩 Final-strategy blockers: <code>{rendered}</code>'
        text+=f"\n🚦 API final path: <b>BOUND</b> · pace <b>{float(data_arch.REQUESTS_PER_SEC):.1f} req/s</b> · reserved <b>{int(_weight_state['reserved'])}/{WEIGHT_BUDGET_PER_MIN}</b>"
    except Exception: pass
    return text
base.heartbeat_text=heartbeat_text_v11221
_old_health=base.health_text
def health_text_v11221(h):
    t=_old_health(h)
    for old in ('V11.22.0','V11.21.9','V11.21.8','V11.21.7','V11.21.6'): t=t.replace(old,'V11.22.1')
    t+=f"\nFinal API binding: <b>ACTIVE</b> · local reserved weight <b>{int(_weight_state['reserved'])}/{WEIGHT_BUDGET_PER_MIN}</b>"
    return t
base.health_text=health_text_v11221

def install():
    _rebind_request_aliases(); base.APP_VERSION=VERSION; base.config.APP_VERSION=VERSION; base.core.APP_VERSION=VERSION; return True
