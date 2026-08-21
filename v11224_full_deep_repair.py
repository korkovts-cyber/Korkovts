"""V11.22.4 · Full-deep completion repair.

Live root cause:
- full-deep selected 14 symbols but only 2 finished before the global deadline;
- V11.22.1 had reduced analysis concurrency to 2;
- each _deep_one may wait up to 22s for a derivatives snapshot;
- the full-deep stage receives only whatever time remains after source, primary,
  multiframe and fast-screen stages.

V11.22.4 keeps the proactive request-weight governor, but removes this internal
throughput contradiction:
- analysis concurrency restored to 4;
- full-deep concurrency raised to 6;
- full scan budget raised to 420s so a minute-boundary weight wait cannot turn
  a valid scan into a false "no result";
- each deep candidate is wrapped by a 30s per-candidate deadline;
- deep timing/failure diagnostics are surfaced in AUTO/HEALTH.

No final trading threshold is weakened. A candidate that fails its deep data,
strategy, execution, evidence or risk checks still cannot become a signal.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter

import bot_v11191 as runtime
import v11191_futures_engine as futures
import v11196_api_resilience as api_resilience

base=runtime.base
VERSION="11.22.4"

# Keep request-weight protection, remove artificial analysis starvation.
try:
    api_resilience.ANALYSIS_CONCURRENCY=4
    api_resilience._analysis_sem=asyncio.Semaphore(4)
except Exception:
    pass

futures.DEEP_CONCURRENCY=6
futures.FULL_SCAN_BUDGET_SEC=max(
    420,int(getattr(futures,"FULL_SCAN_BUDGET_SEC",320) or 320)
)

# Per-candidate deep deadline. The inner derivatives snapshot already has 22s;
# 30s gives strategy/calibration enough tail without allowing one symbol to hang.
_original_deep_one=futures._deep_one
_deep_stats={
    "runs":0,
    "ok":0,
    "timeouts":0,
    "errors":0,
    "reasons":Counter(),
    "last_ms":[],
}

async def deep_one_v11224(row,kind,market_context,news,adl_risks,min_score,sem):
    started=time.monotonic()
    symbol=str(row[0]) if row else "?"
    _deep_stats["runs"]+=1
    try:
        result=await asyncio.wait_for(
            _original_deep_one(
                row,kind,market_context,news,adl_risks,min_score,sem
            ),
            timeout=30.0
        )
        elapsed=(time.monotonic()-started)*1000.0
        _deep_stats["last_ms"].append((symbol,round(elapsed)))
        _deep_stats["last_ms"]=_deep_stats["last_ms"][-14:]
        signal,reason,payload=result
        key=str(reason or "PASS")
        _deep_stats["reasons"][key]+=1
        if reason:
            if str(reason).startswith("ERROR:"):
                _deep_stats["errors"]+=1
        else:
            _deep_stats["ok"]+=1
        if isinstance(payload,dict):
            payload["_v11224_deep_ms"]=round(elapsed,1)
        return result
    except asyncio.TimeoutError:
        elapsed=(time.monotonic()-started)*1000.0
        _deep_stats["timeouts"]+=1
        _deep_stats["reasons"]["DEEP_CANDIDATE_TIMEOUT"]+=1
        _deep_stats["last_ms"].append((symbol,round(elapsed)))
        _deep_stats["last_ms"]=_deep_stats["last_ms"][-14:]
        return None,"DEEP_CANDIDATE_TIMEOUT",{
            "_error":"per-candidate deep deadline 30s exceeded",
            "_v11224_deep_ms":round(elapsed,1),
        }
    except Exception as exc:
        elapsed=(time.monotonic()-started)*1000.0
        _deep_stats["errors"]+=1
        key=f"ERROR:{type(exc).__name__}"
        _deep_stats["reasons"][key]+=1
        return None,key,{
            "_error":str(exc),
            "_v11224_deep_ms":round(elapsed,1),
        }

futures._deep_one=deep_one_v11224

# Diagnostics.
_old_hb=base.heartbeat_text
def heartbeat_text_v11224(diagnostics,**kwargs):
    text=_old_hb(diagnostics,**kwargs)
    try:
        d=dict(diagnostics or {})
        checked=int(d.get("deep_checked",0) or 0)
        complete=int(d.get("deep_complete",0) or 0)
        cancelled=int(d.get("deep_deadline_cancelled",0) or 0)
        if checked:
            text+=(
                f"\n🧪 Full-deep: <b>{complete}/{checked}</b> complete"
                f" · deadline-cancelled <b>{cancelled}</b>"
            )
        if _deep_stats["timeouts"] or _deep_stats["errors"]:
            text+=(
                f"\n⏱ Deep candidate: timeouts <b>{_deep_stats['timeouts']}</b>"
                f" · errors <b>{_deep_stats['errors']}</b>"
            )
    except Exception:
        pass
    return text
base.heartbeat_text=heartbeat_text_v11224

_old_health=base.health_text
def health_text_v11224(h):
    text=_old_health(h)
    for old in (
        "V11.22.3","V11.22.2","V11.22.1","V11.22.0",
        "V11.21.9","V11.21.8","V11.21.7","V11.21.6"
    ):
        text=text.replace(old,VERSION)
    try:
        text+=(
            f"\nFutures deep: concurrency <b>{futures.DEEP_CONCURRENCY}</b>"
            f" · scan budget <b>{futures.FULL_SCAN_BUDGET_SEC}s</b>"
            f" · analysis concurrency <b>{getattr(api_resilience,'ANALYSIS_CONCURRENCY',4)}</b>"
        )
    except Exception:
        pass
    return text
base.health_text=health_text_v11224

def install():
    futures._deep_one=deep_one_v11224
    base.APP_VERSION=VERSION
    base.config.APP_VERSION=VERSION
    base.core.APP_VERSION=VERSION
    return True
