"""V11.21.6 · shared Binance data architecture · stability audited."""
from __future__ import annotations
import asyncio, copy, os, time
import v1141_governor as governor
import v11196_api_resilience as api_resilience

# Stale Railway variables may slow research, but may not raise it above the audited 4.5 req/s.
REQUESTS_PER_SEC=max(3.5,min(4.5,float(os.getenv("V11200_RESEARCH_RPS","4.5"))))
MIN_START_GAP=1.0/REQUESTS_PER_SEC
SPOT_DERIVATIVE_SNAPSHOT_TTL=max(60,min(240,int(os.getenv("V11200_SPOT_DERIV_TTL_SEC","150"))))
_previous_governed_get=governor.governed_get
_pace_lock=asyncio.Lock()
_inflight={}
_inflight_lock=asyncio.Lock()
_cache={}
_cache_lock=asyncio.Lock()
_next_start=0.0
_stats={"requests":0,"critical":0,"realtime":0,"paced":0,"cache_hits":0,"singleflight_hits":0,
        "leader_cancellations":0,"spot_snapshot_hits":0,"spot_snapshot_misses":0}

def _key(path,params):
    return str(path),tuple(sorted((str(k),str(v)) for k,v in dict(params or {}).items()))

def _critical(path,params=None):
    path=str(path or ""); params=dict(params or {})
    if path.endswith("/time") or path.endswith("/exchangeInfo"):
        return True
    return path.endswith("/klines") and str(params.get("symbol") or "").upper()=="BTCUSDT" and str(params.get("interval") or "")=="1m"


def _realtime(path,params=None):
    """Only tiny bookTicker probes bypass research pacing.

    REST depth/aggTrades remain governed research traffic because the broad/deep
    scanners also use them. Live Futures execution primarily consumes the local
    websocket order-book/tape layers, so bypassing all depth calls would let
    research traffic evade the weight guard.
    """
    path=str(path or "")
    return path.endswith("/bookTicker")

def _ttl(path,params=None):
    path=str(path or "")
    # Contract metadata changes slowly and is requested by Futures source-stage
    # plus Spot WATCH crowding checks. Five-minute reuse removes redundant
    # exchangeInfo traffic without making price/execution data stale.
    if path.endswith("/exchangeInfo"): return 300.0
    if path.endswith("/ticker/24hr"): return 75.0
    if path.endswith("/premiumIndex"): return 12.0
    if path.endswith("/openInterest"): return 12.0
    if "openInterestHist" in path: return 45.0
    if "takerlongshortRatio" in path: return 35.0
    if "globalLongShortAccountRatio" in path: return 45.0
    if "topLongShortPositionRatio" in path: return 45.0
    if "/futures/data/basis" in path: return 60.0
    if "symbolAdlRisk" in path: return 30.0
    # Never cache order book, candles, health time, or 24h ticker here.
    return 0.0

async def _pace():
    global _next_start
    async with _pace_lock:
        now=time.monotonic()
        wait=max(0.0,_next_start-now)
        if wait: await asyncio.sleep(wait)
        _next_start=max(time.monotonic(),_next_start)+MIN_START_GAP
        _stats["paced"]+=1

async def _cached(key,ttl):
    if ttl<=0: return None
    async with _cache_lock:
        row=_cache.get(key)
        if not row: return None
        if float(row["expires"])<=time.monotonic():
            _cache.pop(key,None); return None
        _stats["cache_hits"]+=1
        return copy.deepcopy(row["value"])

async def _store(key,value,ttl):
    if ttl<=0: return
    async with _cache_lock:
        _cache[key]={"expires":time.monotonic()+ttl,"value":copy.deepcopy(value)}

async def governed_get(path,params=None):
    critical=_critical(path,params)
    key=_key(path,params); ttl=_ttl(path,params)
    hit=await _cached(key,ttl)
    if hit is not None: return hit
    async with _inflight_lock:
        future=_inflight.get(key)
        if future is None:
            future=asyncio.get_running_loop().create_future()
            _inflight[key]=future; leader=True
        else: leader=False
    if not leader:
        _stats["singleflight_hits"]+=1
        # A cancelled follower must never cancel the shared Future for everyone.
        return copy.deepcopy(await asyncio.shield(future))
    try:
        if critical:
            _stats["critical"]+=1
        elif _realtime(path,params):
            _stats["realtime"]+=1
        else:
            await _pace()
        _stats["requests"]+=1
        value=await _previous_governed_get(path,params)
        await _store(key,value,ttl)
        if not future.done():
            future.set_result(copy.deepcopy(value))
        return value
    except asyncio.CancelledError:
        # Scanner deadlines intentionally cancel leaders. Wake every follower
        # immediately instead of leaving an unresolved Future in the process.
        _stats["leader_cancellations"]+=1
        if not future.done():
            future.cancel()
        raise
    except Exception as exc:
        if not future.done():
            future.set_exception(exc)
            try: future.exception()
            except Exception: pass
        raise
    finally:
        async with _inflight_lock:
            if _inflight.get(key) is future: _inflight.pop(key,None)

_spot_snapshot_cache={}
_spot_snapshot_lock=asyncio.Lock()

def _install_spot_snapshot_reuse():
    try: import spot_scanner
    except Exception: return False
    original=spot_scanner.get_derivatives_snapshot
    if getattr(original,"_v11200_spot_cached",False): return True
    async def spot_cached_snapshot(symbol,*args,**kwargs):
        symbol=str(symbol or "").upper(); now=time.monotonic()
        async with _spot_snapshot_lock:
            row=_spot_snapshot_cache.get(symbol)
            if row and float(row["expires"])>now:
                _stats["spot_snapshot_hits"]+=1
                return copy.deepcopy(row["value"])
        _stats["spot_snapshot_misses"]+=1
        value=await original(symbol,*args,**kwargs)
        async with _spot_snapshot_lock:
            _spot_snapshot_cache[symbol]={"expires":time.monotonic()+SPOT_DERIVATIVE_SNAPSHOT_TTL,
                                          "value":copy.deepcopy(value)}
        return value
    spot_cached_snapshot._v11200_spot_cached=True
    spot_scanner.get_derivatives_snapshot=spot_cached_snapshot
    return True

def install():
    api_resilience.SOFT_WEIGHT_CEILING=min(int(getattr(api_resilience,"SOFT_WEIGHT_CEILING",1150) or 1150),1150)
    governor.governed_get=governed_get
    return True

def install_after_base():
    return _install_spot_snapshot_reuse()

def status():
    row=dict(_stats)
    row.update({"research_rps":REQUESTS_PER_SEC,
                "soft_weight_ceiling":int(getattr(api_resilience,"SOFT_WEIGHT_CEILING",0) or 0),
                "rest_cache_entries":len(_cache),"singleflight_active":len(_inflight),
                "spot_snapshot_cache":len(_spot_snapshot_cache),
                "spot_snapshot_ttl_sec":SPOT_DERIVATIVE_SNAPSHOT_TTL})
    try: row["governor"]=governor.status()
    except Exception: row["governor"]={}
    return row
