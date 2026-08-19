"""V11.19.3 · resilient Binance server-clock guard.

A true clock problem remains fail-closed. A single slow /time response no longer
pauses the entire public-market signal engine for a minute.
"""
from __future__ import annotations
import asyncio
import time

import v1141_integrity as legacy

_cache=(0.0,None)
_last_good=None
_last_good_at=0.0
_lock=asyncio.Lock()


async def clock_status(max_age=45,max_abs_offset_ms=5000,max_rtt_ms=6000):
    global _cache,_last_good,_last_good_at
    now=time.time()
    if _cache[1] is not None and now-_cache[0] < float(max_age):
        return _cache[1]

    async with _lock:
        now=time.time()
        if _cache[1] is not None and now-_cache[0] < float(max_age):
            return _cache[1]

        samples=[]
        errors=[]
        for _ in range(3):
            t0=time.time()*1000.0
            try:
                payload=await asyncio.wait_for(legacy._get("/fapi/v1/time"),timeout=5.5)
                t1=time.time()*1000.0
                server=float((payload or {}).get("serverTime",0) or 0)
                if server<=0:
                    raise RuntimeError("empty Binance serverTime")
                rtt=t1-t0
                offset=server-(t0+t1)/2.0
                samples.append((rtt,offset))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(.05)

        if samples:
            # Lowest RTT minimizes asymmetric network-delay error.
            rtt,offset=min(samples,key=lambda x:x[0])
            ok=abs(offset)<=float(max_abs_offset_ms) and rtt<=float(max_rtt_ms)
            reason="" if ok else f"clock offset={offset:.0f}ms rtt={rtt:.0f}ms"
            row=legacy.IntegrityStatus(bool(ok),float(offset),float(rtt),reason)
            if ok:
                _last_good=row
                _last_good_at=time.time()
            _cache=(time.time(),row)
            return row

        # Public market-data analysis does not sign orders. If the clock was
        # proven good recently, a transient /time outage is telemetry degradation,
        # not a reason to erase every market opportunity.
        if _last_good is not None and time.time()-_last_good_at<=600:
            row=legacy.IntegrityStatus(
                True,float(_last_good.offset_ms),float(_last_good.rtt_ms),
                "recent verified clock reused after transient Binance /time failure"
            )
            _cache=(time.time(),row)
            return row

        row=legacy.IntegrityStatus(
            False,999999.0,999999.0,
            "Binance clock unavailable: "+("; ".join(errors[:2]) or "no valid sample")
        )
        _cache=(time.time(),row)
        return row
