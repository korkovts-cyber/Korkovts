"""Central public-Binance request governor for V11.4.1.

The core already performs endpoint retries and 429/418 backoff. This layer adds:
- a process-wide concurrency ceiling across scanner/Alpha/L2/health requests;
- a stricter low-priority ceiling for research-heavy aggTrades;
- a shared cooldown after a rate-limit failure;
- telemetry for /system.

It does not guess endpoint REQUEST_WEIGHT values; Binance documents that weights
vary by route. The official x-mbx header handling in app.market remains the
source of truth for actual used weight.
"""

from __future__ import annotations

import asyncio
import os
import time

import app.market as market

_raw_get=market._get
_global_sem=asyncio.Semaphore(max(4,min(16,int(os.getenv("V1141_API_CONCURRENCY","10")))))
_low_sem=asyncio.Semaphore(2)
_lock=asyncio.Lock()
_state={
    "installed":False,"requests":0,"high":0,"normal":0,"low":0,
    "rate_limit_failures":0,"cooldown_until":0.0,"last_error":"",
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


async def governed_get(path,params=None):
    priority=_priority(path)
    async with _lock:
        wait=max(0.0,float(_state["cooldown_until"])-time.time())
    if wait>0:
        await asyncio.sleep(min(wait,15.0))

    low_ctx=_low_sem if priority=="low" else _NullAsyncContext()
    async with low_ctx:
        async with _global_sem:
            _state["requests"]+=1
            _state[priority]+=1
            try:
                return await _raw_get(path,params)
            except Exception as exc:
                text=f"{type(exc).__name__}: {exc}"
                _state["last_error"]=text[:300]
                response=getattr(exc,"response",None)
                status=getattr(response,"status_code",None)
                if status in (418,429):
                    retry=1.0
                    try:
                        retry=float(response.headers.get("Retry-After",1) or 1)
                    except Exception:
                        pass
                    async with _lock:
                        _state["rate_limit_failures"]+=1
                        _state["cooldown_until"]=max(
                            float(_state["cooldown_until"]),
                            time.time()+min(60.0,max(1.0,retry))
                        )
                raise


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
