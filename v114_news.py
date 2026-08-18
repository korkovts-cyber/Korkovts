"""Concurrency-safe news failover for V11.7.1.

Each scan owns a mutable context object stored in a ContextVar. asyncio child
tasks inherit the same object reference, so a news-fetch task can update the
state that the parent scan later reads without leaking into another concurrent
scan.

Global telemetry is kept separately for /system display only.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from datetime import datetime, timezone

log=logging.getLogger(__name__)

_ctx:ContextVar[dict|None]=ContextVar("v1141_news_context",default=None)
_telemetry={"checked":False,"degraded":False,"reason":"not checked",
            "changed_at":0.0,"healthy_sources":0}


def begin_context():
    return _ctx.set({
        "checked":False,"degraded":False,"reason":"not checked",
        "changed_at":time.time(),"healthy_sources":0,
    })


def end_context(token):
    _ctx.reset(token)


def _publish(value):
    global _telemetry
    _telemetry=dict(value)
    ctx=_ctx.get()
    if ctx is not None:
        ctx.clear()
        ctx.update(value)


def neutral_snapshot(reason="all news sources unavailable",source_total=0):
    return {
        "global":0.0,"score":0.0,"assets":{},"headlines":[],"items":[],
        "breaking_events":[],"breaking":False,
        # Core scanner requires sources>=1. This sentinel only prevents an
        # optional news outage from killing the whole Binance scan.
        "sources":1,"real_sources":0,
        "source_total":int(source_total or 0),
        "source_names":[],"failed_sources":int(source_total or 0),
        "event_risk":0.0,"high_impact_count":0,"high_impact_headlines":[],
        "x_configured":False,"x_connected":False,
        "fetched_at":datetime.now(timezone.utc).isoformat(),
        "v114_news_degraded":True,
        "v114_news_reason":str(reason),
    }


async def safe_fetch(fetcher,*args,**kwargs):
    try:
        data=await fetcher(*args,**kwargs)
        sources=int((data or {}).get("sources",0) or 0)
        if sources>=1:
            value={"checked":True,"degraded":False,"reason":"",
                   "changed_at":time.time(),"healthy_sources":sources}
            _publish(value)
            result=dict(data)
            result["real_sources"]=sources
            result["v114_news_degraded"]=False
            return result
        reason="all news sources returned unavailable"
        total=int((data or {}).get("source_total",0) or 0)
    except Exception as exc:
        reason=f"{type(exc).__name__}: {exc}"
        total=0
        log.warning("V11.7.1 news degraded: %s",reason)

    value={"checked":True,"degraded":True,"reason":reason,
           "changed_at":time.time(),"healthy_sources":0}
    _publish(value)
    return neutral_snapshot(reason,total)


def state():
    """Return the current scan's news state, falling back to global telemetry."""
    ctx=_ctx.get()
    return dict(ctx if ctx is not None else _telemetry)


def telemetry():
    return dict(_telemetry)
