"""Telemetry from Binance's public all-market liquidation stream.

Liquidations are intentionally *not* converted into a directional signal in
this release.  The raw rolling values are attached to every issued signal so
we can later test whether they add out-of-sample value without hindsight.
"""

import asyncio
import json
import logging
import time
from collections import deque

import websockets

from .config import LIQUIDATION_WINDOW_MINUTES

log=logging.getLogger(__name__)
STREAM_URL="wss://fstream.binance.com/market/ws/!forceOrder@arr"
_events=deque(maxlen=100_000)
_connected_at=0.0
_last_message_at=0.0
_socket_connected=False


def _orders(payload):
    rows=payload if isinstance(payload,list) else [payload]
    for row in rows:
        if not isinstance(row,dict):
            continue
        # The all-market stream can contain the merged USD-M/COIN-M universe.
        # st=1 is USD-M.  Older payloads may omit st.
        if int(row.get("st",1) or 1)!=1:
            continue
        order=row.get("o") or {}
        if order.get("s"):
            yield row,order


def ingest(payload,received_at=None):
    """Parse one stream payload; kept separate so it is unit-testable."""
    now=float(received_at or time.time())
    count=0
    for row,order in _orders(payload):
        price=float(order.get("ap") or order.get("p") or 0)
        qty=float(order.get("z") or order.get("l") or order.get("q") or 0)
        if price<=0 or qty<=0:
            continue
        event_ms=int(order.get("T") or row.get("E") or now*1000)
        # SELL force orders close liquidated longs; BUY closes shorts.
        liquidated_side="LONG" if str(order.get("S","")).upper()=="SELL" else "SHORT"
        _events.append((event_ms/1000,str(order["s"]),liquidated_side,price*qty))
        count+=1
    return count


def snapshot(symbol,oi_notional=0.0,minutes=LIQUIDATION_WINDOW_MINUTES):
    now=time.time(); cutoff=now-float(minutes)*60
    while _events and _events[0][0]<now-3600:
        _events.popleft()
    rows=[event for event in _events if event[0]>=cutoff]
    symbol_rows=[event for event in rows if event[1]==symbol]
    long_usd=sum(event[3] for event in symbol_rows if event[2]=="LONG")
    short_usd=sum(event[3] for event in symbol_rows if event[2]=="SHORT")
    total=long_usd+short_usd
    ready=bool(_socket_connected and _connected_at and now-_connected_at>=300)
    return {
        "liquidation_stream_ready":ready,
        "liquidation_window_min":int(minutes),
        "liquidation_events":len(symbol_rows),
        "liquidated_longs_usd":long_usd,
        "liquidated_shorts_usd":short_usd,
        "liquidation_notional_usd":total,
        "liquidation_intensity_bps":(total/float(oi_notional)*10_000) if oi_notional else 0.0,
        "market_liquidation_usd":sum(event[3] for event in rows),
    }


def stream_status():
    now=time.time()
    while _events and _events[0][0]<now-3600:
        _events.popleft()
    return {"connected":bool(_socket_connected),
            "warm":bool(_socket_connected and _connected_at and now-_connected_at>=300),
            "events_1h":len(_events),"last_message_age":now-_last_message_at if _last_message_at else None}


async def monitor():
    global _connected_at,_last_message_at,_socket_connected
    delay=1
    while True:
        try:
            async with websockets.connect(STREAM_URL,ping_interval=20,ping_timeout=20,
                                          close_timeout=10,max_queue=2048) as ws:
                _connected_at=time.time(); _last_message_at=_connected_at
                _socket_connected=True; delay=1
                log.info("Binance liquidation telemetry connected")
                async for raw in ws:
                    _last_message_at=time.time()
                    ingest(json.loads(raw),_last_message_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect on any stream/parser failure.
            _socket_connected=False
            log.warning("liquidation telemetry disconnected: %s",exc)
            await asyncio.sleep(delay)
            delay=min(60,delay*2)
        finally:
            _socket_connected=False
