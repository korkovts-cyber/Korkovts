"""Production data-health guard for V11.2.1."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass

import pandas as pd

from app.config import DATABASE_PATH
from app.market import _get, get_klines
from v11_live import health as ws_health


@dataclass(frozen=True)
class Health:
    status:str
    hard_pause:bool
    rest_latency_ms:float
    candle_age_sec:float
    server_clock_skew_ms:float
    ws_connected:bool
    ws_age_sec:float
    db_ok:bool
    db_persistent:bool
    db_path:str
    reasons:tuple[str,...]


_cache={"ts":0.0,"value":None}


def classify(rest_ok,latency_ms,candle_age_sec,clock_skew_ms,ws_connected,ws_age_sec,
             db_ok=True,db_persistent=True):
    reasons=[]; hard=False
    if not rest_ok:
        hard=True; reasons.append("Binance REST unavailable")
    if candle_age_sec>180:
        hard=True; reasons.append(f"BTC 1m candle stale {candle_age_sec:.0f}s")
    if abs(clock_skew_ms)>10_000:
        hard=True; reasons.append(f"clock skew {clock_skew_ms/1000:.1f}s")
    if not db_ok:
        hard=True; reasons.append("database unavailable")
    if latency_ms>3000:
        reasons.append(f"REST latency {latency_ms:.0f}ms")
    if not ws_connected or ws_age_sec>45:
        reasons.append("live websocket degraded")
    if not db_persistent:
        reasons.append("database path may reset after deploy")
    status="PAUSE" if hard else ("DEGRADED" if reasons else "OK")
    return status,hard,tuple(reasons)


def _db_health():
    persistent=str(DATABASE_PATH).startswith("/data/")
    try:
        parent=os.path.dirname(DATABASE_PATH) or "."
        os.makedirs(parent,exist_ok=True)
        with sqlite3.connect(DATABASE_PATH,timeout=3) as c:
            c.execute("SELECT 1").fetchone()
        return True,persistent
    except Exception:
        return False,persistent


async def check(force=False):
    now=time.time()
    if not force and _cache["value"] is not None and now-_cache["ts"]<45:
        return _cache["value"]

    rest_ok=True; latency=9999.0; candle_age=9999.0; skew=999999.0
    try:
        t0=time.perf_counter()
        payload=await asyncio.wait_for(_get("/fapi/v1/time"),timeout=8)
        latency=(time.perf_counter()-t0)*1000
        server_ms=float((payload or {}).get("serverTime",0) or 0)
        skew=(time.time()*1000-server_ms) if server_ms else 999999.0

        frame=await asyncio.wait_for(get_klines("BTCUSDT","1m",5),timeout=8)
        if frame is None or frame.empty:
            rest_ok=False
        else:
            last_open=pd.Timestamp(frame.iloc[-1].open_time)
            if last_open.tzinfo is None:
                last_open=last_open.tz_localize("UTC")
            close_time=last_open+pd.Timedelta(seconds=60)
            candle_age=max(0.0,(pd.Timestamp.now(tz="UTC")-close_time).total_seconds())
    except Exception:
        rest_ok=False

    db_ok,db_persistent=_db_health()
    wh=ws_health()
    ws_connected=bool(wh.get("connected"))
    age=wh.get("last_age_sec")
    ws_age=float(age) if age is not None else 9999.0

    status,hard,reasons=classify(
        rest_ok,latency,candle_age,skew,ws_connected,ws_age,
        db_ok=db_ok,db_persistent=db_persistent,
    )
    value=Health(
        status,hard,latency,candle_age,skew,ws_connected,ws_age,
        db_ok,db_persistent,str(DATABASE_PATH),reasons
    )
    _cache.update(ts=now,value=value)
    return value


def text(h):
    icon={"OK":"✅","DEGRADED":"⚠️","PAUSE":"🛑"}.get(h.status,"⚪")
    reasons=", ".join(h.reasons) if h.reasons else "критических проблем нет"
    storage=("PERSISTENT /data" if h.db_persistent else "LOCAL / MAY RESET")
    return (
        f"📡 <b>PRODUCTION HEALTH</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{icon} Статус: <b>{h.status}</b>\n"
        f"REST latency: <b>{h.rest_latency_ms:.0f} ms</b>\n"
        f"BTC 1m freshness: <b>{h.candle_age_sec:.0f} sec</b>\n"
        f"Clock skew: <b>{h.server_clock_skew_ms/1000:+.1f} sec</b>\n"
        f"WebSocket: <b>{'ONLINE' if h.ws_connected and h.ws_age_sec<=45 else 'DEGRADED'}</b>\n"
        f"Database: <b>{'OK' if h.db_ok else 'ERROR'}</b> · <b>{storage}</b>\n"
        f"Path: <code>{h.db_path}</code>\n"
        f"Причины: <b>{reasons}</b>\n\n"
        "PAUSE блокирует новые сигналы, но не удаляет историю."
    )
