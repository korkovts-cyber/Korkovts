"""Market-data integrity helpers for V11.4.1.

Adds:
- Binance server-clock guard.
- exchangeInfo metadata cache and exact tick-size rounding.
- invariant checks for LONG/SHORT geometry.
- deterministic decision-lineage hash.

No private/account endpoint is used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from dataclasses import dataclass

from app.market import _get

_clock_cache=(0.0,{"ok":False,"offset_ms":999999.0,"rtt_ms":999999.0})
_meta_cache=(0.0,{})
_clock_lock=asyncio.Lock()
_meta_lock=asyncio.Lock()


@dataclass(frozen=True)
class IntegrityStatus:
    ok:bool
    offset_ms:float
    rtt_ms:float
    reason:str


async def clock_status(max_age=60,max_abs_offset_ms=2000,max_rtt_ms=2500):
    global _clock_cache
    now=time.time()
    if now-_clock_cache[0]<float(max_age):
        row=_clock_cache[1]
        return IntegrityStatus(
            bool(row["ok"]),float(row["offset_ms"]),float(row["rtt_ms"]),
            str(row.get("reason",""))
        )
    async with _clock_lock:
        now=time.time()
        if now-_clock_cache[0]<float(max_age):
            row=_clock_cache[1]
            return IntegrityStatus(bool(row["ok"]),float(row["offset_ms"]),
                                   float(row["rtt_ms"]),str(row.get("reason","")))
        t0=time.time()*1000
        try:
            payload=await asyncio.wait_for(_get("/fapi/v1/time"),timeout=8)
            t1=time.time()*1000
            server=float((payload or {}).get("serverTime",0) or 0)
            midpoint=(t0+t1)/2
            rtt=t1-t0
            offset=server-midpoint if server else 999999.0
            ok=bool(server and abs(offset)<=max_abs_offset_ms and rtt<=max_rtt_ms)
            reason="" if ok else f"clock offset={offset:.0f}ms rtt={rtt:.0f}ms"
        except Exception as exc:
            offset=999999.0; rtt=999999.0; ok=False
            reason=f"{type(exc).__name__}: {exc}"
        row={"ok":ok,"offset_ms":offset,"rtt_ms":rtt,"reason":reason}
        _clock_cache=(time.time(),row)
        return IntegrityStatus(ok,offset,rtt,reason)


def _filters(row):
    out={}
    for f in row.get("filters") or []:
        if isinstance(f,dict) and f.get("filterType"):
            out[str(f["filterType"])]=dict(f)
    price=out.get("PRICE_FILTER",{})
    lot=out.get("LOT_SIZE",{})
    notional=out.get("MIN_NOTIONAL",{})
    result={
        "status":str(row.get("status","")),
        "contract_type":str(row.get("contractType","")),
        "tick_size":str(price.get("tickSize","0")),
        "min_price":str(price.get("minPrice","0")),
        "max_price":str(price.get("maxPrice","0")),
        "step_size":str(lot.get("stepSize","0")),
        "min_qty":str(lot.get("minQty","0")),
        "max_qty":str(lot.get("maxQty","0")),
        "min_notional":str(notional.get("notional","0")),
    }
    result["filter_hash"]=hashlib.sha256(
        json.dumps(result,sort_keys=True,separators=(",",":")).encode("utf-8")
    ).hexdigest()[:16]
    return result


async def exchange_metadata(max_age=3600):
    global _meta_cache
    now=time.time()
    if now-_meta_cache[0]<float(max_age) and _meta_cache[1]:
        return _meta_cache[1]
    async with _meta_lock:
        now=time.time()
        if now-_meta_cache[0]<float(max_age) and _meta_cache[1]:
            return _meta_cache[1]
        payload=await asyncio.wait_for(_get("/fapi/v1/exchangeInfo"),timeout=15)
        rows=(payload or {}).get("symbols") or []
        meta={str(r.get("symbol","")).upper():_filters(r)
              for r in rows if isinstance(r,dict) and r.get("symbol")}
        if not meta:
            raise RuntimeError("empty Binance exchangeInfo metadata")
        _meta_cache=(time.time(),meta)
        return meta


def _quantize(value,tick,min_price,rounding):
    tick=Decimal(str(tick))
    base=Decimal(str(min_price or "0"))
    if tick<=0:
        return float(value)
    value=Decimal(str(value))
    # Binance PRICE_FILTER is (price - minPrice) % tickSize == 0.
    steps=((value-base)/tick).to_integral_value(rounding=rounding)
    rounded=base+steps*tick
    return float(rounded)


def _apply_rounding(signal,meta):
    tick=meta.get("tick_size","0")
    min_price=meta.get("min_price","0")
    if Decimal(str(tick or "0"))<=0:
        raise RuntimeError("invalid tickSize")
    if signal.side=="LONG":
        signal.entry_low=_quantize(signal.entry_low,tick,min_price,ROUND_FLOOR)
        signal.entry_high=_quantize(signal.entry_high,tick,min_price,ROUND_CEILING)
        signal.stop=_quantize(signal.stop,tick,min_price,ROUND_FLOOR)
        signal.tp1=_quantize(signal.tp1,tick,min_price,ROUND_FLOOR)
        signal.tp2=_quantize(signal.tp2,tick,min_price,ROUND_FLOOR)
        signal.tp3=_quantize(signal.tp3,tick,min_price,ROUND_FLOOR)
    else:
        signal.entry_low=_quantize(signal.entry_low,tick,min_price,ROUND_FLOOR)
        signal.entry_high=_quantize(signal.entry_high,tick,min_price,ROUND_CEILING)
        signal.stop=_quantize(signal.stop,tick,min_price,ROUND_CEILING)
        signal.tp1=_quantize(signal.tp1,tick,min_price,ROUND_CEILING)
        signal.tp2=_quantize(signal.tp2,tick,min_price,ROUND_CEILING)
        signal.tp3=_quantize(signal.tp3,tick,min_price,ROUND_CEILING)
    return signal


def invariant_errors(signal):
    try:
        entry=float(signal.entry_high if signal.side=="LONG" else signal.entry_low)
        stop=float(signal.stop)
        tp1=float(signal.tp1); tp2=float(signal.tp2); tp3=float(signal.tp3)
    except Exception:
        return ["non-numeric signal geometry"]
    values=(entry,stop,tp1,tp2,tp3)
    if not all(math.isfinite(x) and x>0 for x in values):
        return ["non-finite/non-positive signal geometry"]
    if signal.side=="LONG":
        ok=stop<entry<tp1<tp2<tp3
    else:
        ok=stop>entry>tp1>tp2>tp3
    return [] if ok else [f"invalid {signal.side} entry/stop/TP ordering"]


async def normalize_signal(signal):
    meta_all=await exchange_metadata()
    meta=meta_all.get(str(signal.symbol).upper())
    if not meta:
        raise RuntimeError(f"symbol metadata unavailable: {signal.symbol}")
    if meta.get("status")!="TRADING" or meta.get("contract_type")!="PERPETUAL":
        raise RuntimeError(f"symbol not active perpetual: {signal.symbol}")
    _apply_rounding(signal,meta)
    issues=invariant_errors(signal)
    levels=[
        float(signal.entry_low),float(signal.entry_high),float(signal.stop),
        float(signal.tp1),float(signal.tp2),float(signal.tp3),
    ]
    min_price=float(meta.get("min_price",0) or 0)
    max_price=float(meta.get("max_price",0) or 0)
    if min_price>0 and any(x<min_price for x in levels):
        issues.append("signal price below Binance minPrice")
    if max_price>0 and any(x>max_price for x in levels):
        issues.append("signal price above Binance maxPrice")
    if issues:
        raise RuntimeError("; ".join(issues))
    signal.feature_snapshot.setdefault("exchange_meta_v1141",{}).update(meta)
    return signal


def stamp_lineage(signal):
    payload={
        "symbol":str(signal.symbol),"timeframe":str(signal.timeframe),
        "side":str(signal.side),
        "entry_low":float(signal.entry_low),"entry_high":float(signal.entry_high),
        "stop":float(signal.stop),"tp1":float(signal.tp1),
        "tp2":float(signal.tp2),"tp3":float(signal.tp3),
        "score":float(getattr(signal,"score",0)),
        "pro":float(getattr(signal,"professional_rank",0)),
        "feature_schema":"11.4.1",
        "features":{
            k:v for k,v in (getattr(signal,"feature_snapshot",{}) or {}).items()
            if k!="lineage_v1141"
        },
    }
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,
                       separators=(",",":"),default=str).encode("utf-8")
    digest=hashlib.sha256(encoded).hexdigest()
    signal.feature_snapshot.setdefault("lineage_v1141",{}).update({
        "feature_schema_version":"11.4.1",
        "sha256":digest,
        "stamped_at_epoch":time.time(),
    })
    return signal
