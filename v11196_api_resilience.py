"""V11.21.1 · Binance API resilience overlay.

Prevents full-universe research traffic from starving production health probes,
adds proactive request-weight headroom, and makes health diagnostics distinguish
real rate-limit cooldowns from transient probe failures.
"""
from __future__ import annotations

import asyncio
import os
import time
import httpx
from datetime import datetime, timezone

import v1141_governor as governor
import v112_health as health

ANALYSIS_CONCURRENCY=max(3,min(5,int(os.getenv("V11200_ANALYSIS_CONCURRENCY","4"))))
SOFT_WEIGHT_CEILING=max(900,min(1400,int(os.getenv("V11200_SOFT_WEIGHT_CEILING","1150"))))
RECENT_HEALTH_GRACE_SEC=max(30,min(180,int(os.getenv("V11196_HEALTH_GRACE_SEC","90"))))
_analysis_sem=asyncio.Semaphore(ANALYSIS_CONCURRENCY)
_original_governed_get=governor.governed_get
_budget={"minute":None,"last_used":0,"soft_waits":0}
_last_good={"at":0.0,"latency":None,"candle_age":None,"skew":None}


def _critical(path,params=None):
    path=str(path or ""); params=dict(params or {})
    if path.endswith("/time"): return True
    return (path.endswith("/klines")
            and str(params.get("symbol") or "").upper()=="BTCUSDT"
            and str(params.get("interval") or "")=="1m"
            and int(params.get("limit") or 0)<=10)


def _estimated_weight(path,params=None):
    path=str(path or ""); p=dict(params or {})
    if path.endswith("/ticker/24hr"): return 1 if p.get("symbol") else 40
    if path.endswith("/premiumIndex"): return 1 if p.get("symbol") else 10
    if path.endswith("/aggTrades"): return 20
    if path.endswith("/depth"):
        limit=int(p.get("limit") or 100); return 5 if limit<=100 else (10 if limit<=500 else 20)
    if path.endswith("/klines"):
        limit=int(p.get("limit") or 500); return 1 if limit<100 else (2 if limit<500 else (5 if limit<=1000 else 10))
    return 1


async def _soft_weight_guard(path=None,params=None):
    now=time.time(); minute=int(now//60)
    if _budget["minute"]!=minute: return
    used=max(int(_budget.get("last_used",0) or 0),int(governor._state.get("last_used_weight_1m",0) or 0))
    reserve=_estimated_weight(path,params)
    if used + reserve < SOFT_WEIGHT_CEILING: return
    delay=max(.05,60.25-(now%60)); _budget["soft_waits"]+=1
    await asyncio.sleep(delay)


async def governed_get(path,params=None):
    critical=_critical(path,params)
    if critical:
        # Critical probes bypass the analysis semaphore, but NEVER bypass a real
        # server-directed 429/418 cooldown inside the original governor.
        result=await _original_governed_get(path,params)
    else:
        async with _analysis_sem:
            await _soft_weight_guard(path,params)
            result=await _original_governed_get(path,params)
    _budget["minute"]=int(time.time()//60)
    _budget["last_used"]=int(governor._state.get("last_used_weight_1m",0) or 0)
    return result



async def _telemetry_raw_get(path,params=None):
    client=governor.market._http_client(); base=str(getattr(governor.market,"BINANCE_BASE_URL",""))
    response=await client.get(f"{base}{path}",params=params)
    used=response.headers.get("x-mbx-used-weight-1m") or response.headers.get("X-MBX-USED-WEIGHT-1M")
    if used:
        try:
            value=int(used); governor._state["last_used_weight_1m"]=value
            _budget["minute"]=int(time.time()//60); _budget["last_used"]=value
        except Exception: pass
    if response.status_code in (418,429):
        raise httpx.HTTPStatusError("Binance rate limit",request=response.request,response=response)
    response.raise_for_status(); return response.json()

def _fmt_metric(value,suffix):
    try:
        value=float(value)
    except Exception:
        return "N/A"
    return f"{value:.0f} {suffix}" if value>=0 else "N/A"


async def check(force=False):
    now=time.time()
    if not force and health._cache["value"] is not None and now-health._cache["ts"]<45:
        return health._cache["value"]

    db_ok,db_persistent=health._db_health()
    wh=health.ws_health()
    ws_connected=bool(wh.get("connected"))
    age=wh.get("last_age_sec")
    ws_age=float(age) if age is not None else 9999.0

    gs=governor.status()
    cooldown=float(gs.get("cooldown_seconds",0) or 0)
    if cooldown>0:
        # A real Binance 429/418 cooldown is a legitimate temporary PAUSE. Do
        # not mislabel it as 9999ms latency / +1000s clock skew.
        lg=_last_good
        latency=float(lg["latency"]) if lg["latency"] is not None else -1.0
        skew=float(lg["skew"]) if lg["skew"] is not None else -1.0
        candle=float(lg["candle_age"]) + max(0,now-float(lg["at"])) if lg["candle_age"] is not None else -1.0
        reasons=(f"Binance rate-limit cooldown {cooldown:.0f}s",)
        value=health.Health("PAUSE",True,latency,candle,skew,ws_connected,ws_age,
                            db_ok,db_persistent,str(health.DATABASE_PATH),reasons)
        health._cache.update(ts=now,value=value)
        return value

    latency=candle_age=skew=None
    error=None
    try:
        t0=time.perf_counter()
        payload=await asyncio.wait_for(governed_get("/fapi/v1/time"),timeout=6)
        latency=(time.perf_counter()-t0)*1000
        server_ms=float((payload or {}).get("serverTime",0) or 0)
        if server_ms<=0:
            raise RuntimeError("empty Binance serverTime")
        skew=time.time()*1000-server_ms

        frame=await asyncio.wait_for(health.get_klines("BTCUSDT","1m",5),timeout=8)
        if frame is None or frame.empty:
            raise RuntimeError("BTCUSDT 1m kline empty")
        last_open=health.pd.Timestamp(frame.iloc[-1].open_time)
        if last_open.tzinfo is None:
            last_open=last_open.tz_localize("UTC")
        close_time=last_open+health.pd.Timedelta(seconds=60)
        candle_age=max(0.0,(health.pd.Timestamp.now(tz="UTC")-close_time).total_seconds())
        _last_good.update(at=time.time(),latency=latency,candle_age=candle_age,skew=skew)
    except Exception as exc:
        error=f"{type(exc).__name__}: {exc}"

    if error is None:
        status,hard,reasons=health.classify(True,latency,candle_age,skew,ws_connected,ws_age,
                                           db_ok=db_ok,db_persistent=db_persistent)
    else:
        recent=(now-float(_last_good.get("at",0) or 0))<=RECENT_HEALTH_GRACE_SEC
        live_ws=ws_connected and ws_age<=45
        if recent and live_ws and db_ok:
            elapsed=max(0,now-float(_last_good["at"]))
            latency=float(_last_good["latency"])
            skew=float(_last_good["skew"])
            candle_age=float(_last_good["candle_age"])+elapsed
            hard=False; status="DEGRADED"
            reasons=(f"transient REST health probe failed ({error}); recent verified state retained",)
        else:
            latency=-1.0; candle_age=-1.0; skew=-1.0
            hard=True; status="PAUSE"
            reasons=(f"Binance REST health probe failed: {error}",)
            if not db_ok:
                reasons+=("database unavailable",)

    value=health.Health(status,hard,float(latency),float(candle_age),float(skew),
                        ws_connected,ws_age,db_ok,db_persistent,
                        str(health.DATABASE_PATH),tuple(reasons))
    health._cache.update(ts=now,value=value)
    return value


def text(h):
    icon={"OK":"✅","DEGRADED":"⚠️","PAUSE":"🛑"}.get(h.status,"⚪")
    reasons=", ".join(h.reasons) if h.reasons else "критических проблем нет"
    storage="PERSISTENT /data" if h.db_persistent else "LOCAL / MAY RESET"
    lat="N/A" if h.rest_latency_ms<0 else f"{h.rest_latency_ms:.0f} ms"
    candle="N/A" if h.candle_age_sec<0 else f"{h.candle_age_sec:.0f} sec"
    skew="N/A" if h.server_clock_skew_ms<0 else f"{h.server_clock_skew_ms/1000:+.1f} sec"
    checked=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        "📡 <b>PRODUCTION HEALTH · V11.21.1</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Проверено: <b>{checked}</b>\n"
        f"{icon} Статус: <b>{h.status}</b>\n"
        f"REST latency: <b>{lat}</b>\n"
        f"BTC 1m freshness: <b>{candle}</b>\n"
        f"Clock skew: <b>{skew}</b>\n"
        f"WebSocket: <b>{'ONLINE' if h.ws_connected and h.ws_age_sec<=45 else 'DEGRADED'}</b>\n"
        f"Database: <b>{'OK' if h.db_ok else 'ERROR'}</b> · <b>{storage}</b>\n"
        f"Path: <code>{h.db_path}</code>\n"
        f"Причины: <b>{reasons}</b>\n\n"
        "PAUSE блокирует новые сигналы, но не удаляет историю."
    )


def install():
    governor._raw_get=_telemetry_raw_get
    governor.governed_get=governed_get
    health.check=check
    health.text=text
    return True
