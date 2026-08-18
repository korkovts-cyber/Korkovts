"""Central public-Binance request governor reused by V11.7.1.

This layer owns public USD-M request concurrency and 429/418 backoff so the
legacy core cannot retry inside a Binance IP-ban window. It adds:
- a process-wide concurrency ceiling across scanner/Alpha/L2/health requests;
- a stricter low-priority ceiling for research-heavy aggTrades;
- a shared cooldown after a rate-limit failure;
- telemetry for /system.

It does not guess endpoint REQUEST_WEIGHT limits; Binance documents that weights
vary by route. Response headers are kept as telemetry only.
"""

from __future__ import annotations

import asyncio
import os
import random
import time

import httpx

import app.market as market



async def _direct_get_once(path,params=None):
    """Exactly one public Binance HTTP attempt; no hidden rate-limit retry."""
    client=market._http_client()
    base=str(getattr(market,"BINANCE_BASE_URL",""))
    response=await client.get(f"{base}{path}",params=params)
    if response.status_code in (418,429):
        raise httpx.HTTPStatusError(
            "Binance rate limit",request=response.request,response=response
        )
    response.raise_for_status()
    used=(
        response.headers.get("x-mbx-used-weight-1m")
        or response.headers.get("X-MBX-USED-WEIGHT-1M")
    )
    if used:
        try:
            _state["last_used_weight_1m"]=int(used)
        except Exception:
            pass
    return response.json()


_raw_get=_direct_get_once

_global_sem=asyncio.Semaphore(max(4,min(16,int(os.getenv("V1142_API_CONCURRENCY",os.getenv("V1141_API_CONCURRENCY","10"))))))
_low_sem=asyncio.Semaphore(2)
_lock=asyncio.Lock()
_state={
    "installed":False,"requests":0,"high":0,"normal":0,"low":0,
    "rate_limit_failures":0,"cooldown_until":0.0,"last_error":"",
    "last_used_weight_1m":0,
}


def _priority(path):
    path=str(path)
    if any(token in path for token in (
        "symbolAdlRisk","openInterest","premiumIndex","/depth","exchangeInfo","/time"
    )):
        return "high"
    if "aggTrades" in path:
        return "low"
    return "normal"




def _retry_after_seconds(headers,status):
    default=120.0 if int(status or 0)==418 else 2.0
    try:
        value=float((headers or {}).get("Retry-After",default) or default)
    except Exception:
        value=default
    if not (value>0):
        value=default
    return min(value,3*86400.0)


async def _wait_for_shared_cooldown():
    while True:
        async with _lock:
            wait=max(0.0,float(_state["cooldown_until"])-time.time())
        if wait<=0:
            return
        await asyncio.sleep(min(wait,60.0))


async def governed_get(path,params=None):
    priority=_priority(path)
    low_ctx=_low_sem if priority=="low" else _NullAsyncContext()

    async with low_ctx:
        async with _global_sem:
            # Important: check AFTER acquiring the semaphore too. Requests that
            # queued before another task received 429 must not slip through.
            await _wait_for_shared_cooldown()

            last=None
            for attempt in range(3):
                _state["requests"]+=1
                _state[priority]+=1
                try:
                    return await _raw_get(path,params)
                except Exception as exc:
                    last=exc
                    text=f"{type(exc).__name__}: {exc}"
                    _state["last_error"]=text[:300]
                    response=getattr(exc,"response",None)
                    status=getattr(response,"status_code",None)

                    if status in (418,429):
                        retry=_retry_after_seconds(
                            getattr(response,"headers",{}),status
                        )
                        async with _lock:
                            _state["rate_limit_failures"]+=1
                            _state["cooldown_until"]=max(
                                float(_state["cooldown_until"]),
                                time.time()+retry
                            )
                        # Fail this market-data operation. Future requests wait
                        # the full server-directed interval; do not hammer retry.
                        raise

                    transient=(
                        isinstance(exc,(httpx.TimeoutException,httpx.NetworkError))
                        or (isinstance(status,int) and status>=500)
                    )
                    if not transient or attempt>=2:
                        raise
                    await asyncio.sleep(min(8.0,(2**attempt)+random.random()))
            raise last or RuntimeError("Binance governed request failed")


class _NullAsyncContext:
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False


def install():
    if _state["installed"]:
        return
    market._get=governed_get

    # Some overlay modules imported _get directly before the governor existed.
    # Patch those bound globals explicitly so every public Binance request shares
    # the same process-wide ceiling.
    for module_name in (
        "v11_liquidity","v112_alpha","v112_health","v1141_integrity"
    ):
        try:
            module=__import__(module_name)
            if hasattr(module,"_get"):
                module._get=governed_get
        except Exception:
            pass
    _state["installed"]=True


def status():
    row=dict(_state)
    row["cooldown_seconds"]=max(0.0,float(row["cooldown_until"])-time.time())
    return row
