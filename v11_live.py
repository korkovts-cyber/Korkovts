"""Lightweight Binance USD-M public WebSocket monitor for V11.

It watches BTCUSDT plus symbols with open/waiting signals. The core REST layer
remains the fallback and source of truth for scans. If this task disconnects,
the bot keeps running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from websockets.asyncio.client import connect

from app.db import open_signals

log=logging.getLogger(__name__)

_BASE="wss://fstream.binance.com/stream?streams="
_prices={}
_books={}
_connected=False
_messages=0
_reconnects=0
_last_message=0.0
_symbols=()


def desired_symbols(limit=20):
    rows=open_signals()
    symbols={"BTCUSDT"}
    for row in rows:
        if int(row.get("is_shadow") or 0):
            continue
        symbol=str(row.get("symbol") or "").upper()
        if symbol:
            symbols.add(symbol)
    ordered=["BTCUSDT"]+sorted(s for s in symbols if s!="BTCUSDT")
    return tuple(ordered[:limit])


def _url(symbols):
    streams=[]
    for s in symbols:
        x=s.lower()
        streams.extend((f"{x}@aggTrade",f"{x}@bookTicker"))
    return _BASE + "/".join(streams)


def _handle(payload):
    global _messages,_last_message
    data=payload.get("data",payload) if isinstance(payload,dict) else {}
    symbol=str(data.get("s") or "").upper()
    if not symbol:
        return
    now=time.time()
    event=data.get("e")
    if event=="aggTrade" and data.get("p") is not None:
        _prices[symbol]={"price":float(data["p"]),"ts":now,"event_ms":int(data.get("T") or data.get("E") or 0)}
    if event=="bookTicker" or ("b" in data and "a" in data and event is None):
        bid=float(data.get("b") or 0); ask=float(data.get("a") or 0)
        _books[symbol]={"bid":bid,"ask":ask,"ts":now}
    _messages+=1; _last_message=now


def price(symbol,max_age=20):
    row=_prices.get(str(symbol).upper())
    if not row or time.time()-row["ts"]>max_age:
        return None
    return float(row["price"])


def book(symbol,max_age=20):
    row=_books.get(str(symbol).upper())
    if not row or time.time()-row["ts"]>max_age:
        return None
    return dict(row)


def health():
    age=None if not _last_message else max(0.0,time.time()-_last_message)
    return {
        "connected":bool(_connected),
        "messages":int(_messages),
        "reconnects":int(_reconnects),
        "last_age_sec":age,
        "symbols":list(_symbols),
        "fresh_prices":sum(1 for r in _prices.values() if time.time()-r["ts"]<=20),
        "fresh_books":sum(1 for r in _books.values() if time.time()-r["ts"]<=20),
    }


async def monitor():
    global _connected,_reconnects,_symbols
    backoff=1
    while True:
        try:
            try:
                current=desired_symbols()
            except Exception as exc:
                # Database/transient read failures must not kill the live task.
                log.warning("V11 websocket symbol refresh failed, BTC fallback: %s",exc)
                current=("BTCUSDT",)
            _symbols=current
            async with connect(
                _url(current),
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_queue=256,
            ) as ws:
                _connected=True
                backoff=1
                checked=time.time()
                while True:
                    try:
                        raw=await asyncio.wait_for(ws.recv(),timeout=20)
                        payload=json.loads(raw)
                        _handle(payload)
                    except asyncio.TimeoutError:
                        pass
                    if time.time()-checked>=20:
                        checked=time.time()
                        if desired_symbols()!=current:
                            break
        except asyncio.CancelledError:
            _connected=False
            raise
        except Exception as exc:
            _connected=False
            _reconnects+=1
            log.warning("V11 websocket reconnect: %s",exc)
            await asyncio.sleep(backoff)
            backoff=min(30,backoff*2)
        finally:
            _connected=False
