"""V11.22.7 · Production stability core.

Fixes remaining live faults:
- removes hidden 22s derivatives timeout from full-deep;
- AUTO waits out short Binance cooldowns instead of skipping the whole cycle;
- foreground full scans get API priority over background USD-M jobs;
- request rates are shaped more conservatively;
- deep diagnostics report current-cycle technical truth.

Trading thresholds, execution/evidence checks and final risk gates are unchanged.
"""
from __future__ import annotations
import asyncio
import contextvars
import time
from collections import Counter

import bot_v11191 as runtime
import app.market as market
import v1141_governor as governor
import v11200_data_architecture as data_arch
import v11221_production_reachability as reach221
import v11191_futures_engine as futures
import v11224_full_deep_repair as deep224
import v11226_stable_deep_engine as deep226

base=runtime.base
VERSION="11.22.7"

# Conservative global shaping. Primary coverage is preserved by a larger frame window.
data_arch.REQUESTS_PER_SEC=3.2
data_arch.MIN_START_GAP=1.0/data_arch.REQUESTS_PER_SEC
futures.FRAME_STAGE_MAX_SEC=max(120,int(getattr(futures,"FRAME_STAGE_MAX_SEC",90) or 90))
futures.FULL_SCAN_BUDGET_SEC=max(600,int(getattr(futures,"FULL_SCAN_BUDGET_SEC",540) or 540))
futures.DEEP_CONCURRENCY=3
reach221.WEIGHT_BUDGET_PER_MIN=620
reach221.WEIGHT_RESERVE_CRITICAL=180
try:
    governor._global_sem=asyncio.Semaphore(6)
    governor._low_sem=asyncio.Semaphore(1)
except Exception:
    pass

_scan_context=contextvars.ContextVar("yk_full_scan_context",default=False)
_foreground_scans=0
_foreground_lock=asyncio.Lock()
_underlying_get=market._get
_futures_data_lock=asyncio.Lock()
_futures_data_next=0.0
_background_sem=asyncio.Semaphore(1)
_background_lock=asyncio.Lock()
_background_next=0.0
FUTURES_DATA_RPS=1.0
BACKGROUND_RPS_DURING_SCAN=0.8

async def _background_pace():
    global _background_next
    async with _background_lock:
        now=time.monotonic()
        wait=max(0.0,_background_next-now)
        if wait:
            await asyncio.sleep(wait)
        _background_next=max(time.monotonic(),_background_next)+1.0/BACKGROUND_RPS_DURING_SCAN

async def governed_get_v11227(path,params=None):
    global _futures_data_next
    path_s=str(path or "")
    in_scan=bool(_scan_context.get())
    if path_s.startswith("/futures/data/"):
        async with _futures_data_lock:
            now=time.monotonic()
            wait=max(0.0,_futures_data_next-now)
            if wait:
                await asyncio.sleep(wait)
            _futures_data_next=max(time.monotonic(),_futures_data_next)+1.0/FUTURES_DATA_RPS
    if _foreground_scans>0 and not in_scan and path_s!="/fapi/v1/time":
        async with _background_sem:
            await _background_pace()
            return await _underlying_get(path,params)
    return await _underlying_get(path,params)

market._get=governed_get_v11227
for module_name in ("v11_liquidity","v112_alpha","v112_health","v1141_integrity","v11197_sources"):
    try:
        m=__import__(module_name)
        if hasattr(m,"_get"):
            m._get=governed_get_v11227
    except Exception:
        pass

_snapshot=deep226.derivatives_snapshot_v11226

def _reset_current_deep():
    deep224._deep_stats["runs"]=0
    deep224._deep_stats["ok"]=0
    deep224._deep_stats["timeouts"]=0
    deep224._deep_stats["errors"]=0
    deep224._deep_stats["reasons"]=Counter()
    deep224._deep_stats["last_ms"]=[]

async def deep_one_v11227(row,kind,market_context,news,adl_risks,min_score,sem):
    symbol,lower,base_frame,higher,soft_l,soft_s=row
    deep224._deep_stats["runs"]+=1
    async with sem:
        started=time.monotonic()
        try:
            adl=adl_risks.get(symbol) if isinstance(adl_risks,dict) else None
            # Single authoritative timeout; old hidden 22s cutoff is bypassed.
            d=await asyncio.wait_for(_snapshot(symbol,adl),timeout=90.0)
            if not isinstance(d,dict):
                raise RuntimeError("invalid derivatives snapshot")
            if not d.get("deep_data"):
                deep224._deep_stats["reasons"]["DERIVATIVES_INCOMPLETE"]+=1
                return None,"DERIVATIVES_INCOMPLETE",d

            oi_notional=futures._f(d.get("open_interest"))*futures._f(d.get("mark_price"))
            d.update(futures.liquidation_snapshot(symbol,oi_notional))
            timeframe="1H" if kind=="main" else "15M"
            audit={}
            result=futures.legacy.analyze(
                symbol,timeframe,base_frame,higher,float(min_score),lower,
                market_context.get("bias"),d,futures.legacy.for_symbol(news,symbol),
                market_context,audit=audit
            )
            if result is None:
                inferred="LONG" if float(soft_l)>=float(soft_s) else "SHORT"
                result=futures._momentum_fallback(
                    symbol,timeframe,base_frame,higher,lower,inferred,d,market_context,
                    futures.legacy.for_symbol(news,symbol),min_score
                )
                if result is None:
                    d["_strategy_audit"]=audit
                    deep224._deep_stats["reasons"]["FINAL_STRATEGY_REJECT"]+=1
                    return None,"FINAL_STRATEGY_REJECT",d
                d["_strategy_audit"]=audit
                d["_v11210_fallback"]=True

            side=str(getattr(result,"side","") or "").upper()
            hist=max(0.0,float(futures.calibration_penalty(symbol,side,timeframe) or 0))
            result.feature_snapshot.setdefault("v11212_cohort_isolation",{}).update({
                "historical_calibration_penalty":hist,"calibration_shadow_only":True,
            })
            if kind!="main":
                result.expected_window="30 минут–4 часа"

            elapsed=(time.monotonic()-started)*1000.0
            d["_v11227_deep_ms"]=round(elapsed,1)
            deep224._deep_stats["ok"]+=1
            deep224._deep_stats["reasons"]["PASS"]+=1
            deep224._deep_stats["last_ms"].append((symbol,round(elapsed)))
            deep224._deep_stats["last_ms"]=deep224._deep_stats["last_ms"][-20:]
            return result,"",d
        except asyncio.TimeoutError:
            elapsed=(time.monotonic()-started)*1000.0
            deep224._deep_stats["timeouts"]+=1
            deep224._deep_stats["reasons"]["DEEP_EXECUTION_TIMEOUT"]+=1
            return None,"DEEP_EXECUTION_TIMEOUT",{
                "_error":"deep execution exceeded 90s after slot acquisition",
                "_v11227_deep_ms":round(elapsed,1),
            }
        except Exception as exc:
            elapsed=(time.monotonic()-started)*1000.0
            key=f"ERROR:{type(exc).__name__}"
            deep224._deep_stats["errors"]+=1
            deep224._deep_stats["reasons"][key]+=1
            return None,key,{"_error":str(exc),"_v11227_deep_ms":round(elapsed,1)}

futures._deep_one=deep_one_v11227

def deep_verification_v11227(d):
    rejections=dict((d or {}).get("rejections") or {})
    technical=("DERIVATIVES_INCOMPLETE","SCAN_DEADLINE","DEEP_CANDIDATE_TIMEOUT","DEEP_EXECUTION_TIMEOUT")
    failed=sum(int(rejections.get(k,0) or 0) for k in technical)
    failed+=sum(int(v or 0) for k,v in rejections.items() if str(k).startswith("ERROR:") or str(k).startswith("RATE_LIMIT"))
    total=int((d or {}).get("deep_checked",0) or 0)
    verified=max(0,total-failed)
    return total,verified,verified/max(1,total)

try:
    import v11217_reliability as rel217
    rel217._deep_verification=deep_verification_v11227
except Exception:
    pass

_prev_raw_scan=base._raw_scan
_prev_raw_short=base._raw_short

async def _run_foreground(coro):
    global _foreground_scans
    token=_scan_context.set(True)
    async with _foreground_lock:
        _foreground_scans+=1
    try:
        _reset_current_deep()
        return await coro()
    finally:
        async with _foreground_lock:
            _foreground_scans=max(0,_foreground_scans-1)
        _scan_context.reset(token)

async def raw_scan_v11227():
    return await _run_foreground(_prev_raw_scan)
async def raw_short_v11227():
    return await _run_foreground(_prev_raw_short)
base._raw_scan=raw_scan_v11227
base._raw_short=raw_short_v11227

_previous_auto_runner=base.core._run_automatic_scan
async def auto_runner_v11227(context,scanner_fn,label):
    try:
        cooldown=float((governor.status() or {}).get("cooldown_seconds",0) or 0)
    except Exception:
        cooldown=0.0
    if 0<cooldown<=180:
        await asyncio.sleep(cooldown+1.25)
        try:
            import v112_health
            if isinstance(getattr(v112_health,"_cache",None),dict):
                v112_health._cache["ts"]=0.0
                v112_health._cache["value"]=None
        except Exception:
            pass
    return await _previous_auto_runner(context,scanner_fn,label)
base.core._run_automatic_scan=auto_runner_v11227

_old_hb=base.heartbeat_text
def heartbeat_v11227(diagnostics,**kwargs):
    text=_old_hb(diagnostics,**kwargs)
    try:
        d=dict(diagnostics or {})
        total,verified,_=deep_verification_v11227(d)
        rej=dict(d.get("rejections") or {})
        if total:
            text+=(f"\n🧾 V11.22.7 deep truth: <b>{verified}/{total}</b> data-verified"
                  f" · exec-timeout <b>{int(rej.get('DEEP_EXECUTION_TIMEOUT',0) or 0)}</b>"
                  f" · legacy-timeout <b>{int(rej.get('DEEP_CANDIDATE_TIMEOUT',0) or 0)}</b>")
        text+=(f"\n🛣 API scheduler: foreground priority <b>ON</b>"
              f" · market pace <b>{data_arch.REQUESTS_PER_SEC:.1f} req/s</b>"
              f" · futures-data <b>{FUTURES_DATA_RPS:.1f} req/s</b>"
              f"\n🧯 AUTO cooldown recovery: <b>ON ≤180s</b>"
              f" · background <b>{BACKGROUND_RPS_DURING_SCAN:.1f} req/s</b>")
    except Exception:
        pass
    return text
base.heartbeat_text=heartbeat_v11227

_old_health=base.health_text
def health_v11227(h):
    text=_old_health(h)
    for old in ("V11.22.6","V11.22.5","V11.22.4","V11.22.3","V11.22.2","V11.22.1","V11.22.0","V11.21.9","V11.21.8","V11.21.7","V11.21.6"):
        text=text.replace(old,VERSION)
    text+=(f"\nScheduler stability: <b>ACTIVE</b> · market {data_arch.REQUESTS_PER_SEC:.1f} req/s"
           f" · futures-data {FUTURES_DATA_RPS:.1f} req/s · AUTO recovery ≤180s")
    return text
base.health_text=health_v11227

def install():
    market._get=governed_get_v11227
    futures._deep_one=deep_one_v11227
    base.core._run_automatic_scan=auto_runner_v11227
    base.APP_VERSION=VERSION
    base.config.APP_VERSION=VERSION
    base.core.APP_VERSION=VERSION
    return True
