"""Lightweight Binance USD-M public/market WebSocket monitor for V11.

Binance USD-M routes WebSocket market data by product class.  `bookTicker`
is consumed from the PUBLIC route while `aggTrade` is consumed from the MARKET
route.  Keeping the two transports independent is important for ENTRY NOW:
loss of the taker-flow feed must fail flow confirmation closed without taking
down the quote feed or the rest of the bot.

The core REST layer remains the source of truth for scans. If either live task
disconnects, the bot keeps running and the affected live feature simply ages
out and becomes unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import defaultdict, deque

from websockets.asyncio.client import connect

from app.db import open_signals

try:
    from v11110_tape import record_event as _record_tape
except Exception:
    def _record_tape(*args, **kwargs):
        return None

log = logging.getLogger(__name__)

# Binance USD-M routed WebSocket endpoints.  Do not merge aggTrade and
# bookTicker onto a legacy unrouted /stream URL: they now live on separate
# MARKET and PUBLIC routes.
_PUBLIC_BASE = "wss://fstream.binance.com/public/stream?streams="
_MARKET_BASE = "wss://fstream.binance.com/market/stream?streams="

_prices = {}
_books = {}
_connected_public = False
_connected_market = False
_connected = False  # backward-compatible aggregate: both mandatory routes up
_messages = 0
_messages_public = 0
_messages_market = 0
_reconnects = 0
_reconnects_public = 0
_reconnects_market = 0
_last_message = 0.0
_last_public_message = 0.0
_last_market_message = 0.0
_symbols = ()
_public_symbols = ()
_market_symbols = ()
_trade_flow = defaultdict(lambda: deque(maxlen=180))
_MAX_EXCHANGE_LAG_SEC = 2.5
_ROUTE_STALL_SEC = 45.0
_route_stalls_public = 0
_route_stalls_market = 0
_extra_symbol_provider = None


def set_extra_symbol_provider(provider):
    """Allow version overlays to add symbols (e.g. ARMED entry candidates)."""
    global _extra_symbol_provider
    _extra_symbol_provider = provider


def desired_symbols(limit=40):
    """BTC first, then highest-priority ARMED setups, then existing open signals."""
    ordered = ["BTCUSDT"]
    seen = {"BTCUSDT"}

    if _extra_symbol_provider is not None:
        try:
            for symbol in _extra_symbol_provider() or ():
                symbol = str(symbol or "").upper()
                if symbol and symbol not in seen:
                    ordered.append(symbol)
                    seen.add(symbol)
        except Exception as exc:
            log.warning("V11 websocket extra-symbol provider failed: %s", exc)

    for row in open_signals():
        if int(row.get("is_shadow") or 0):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in seen:
            ordered.append(symbol)
            seen.add(symbol)

    return tuple(ordered[: max(1, int(limit))])


def _public_url(symbols):
    streams = [f"{str(s).lower()}@bookTicker" for s in symbols]
    return _PUBLIC_BASE + "/".join(streams)


def _market_url(symbols):
    streams = [f"{str(s).lower()}@aggTrade" for s in symbols]
    return _MARKET_BASE + "/".join(streams)


def _url(symbols):
    """Backward-compatible alias for callers that only need the flow URL."""
    return _market_url(symbols)


def _event_clock(data, now):
    event_ms = int(data.get("T") or data.get("E") or 0)
    event_ts = (event_ms / 1000.0) if event_ms > 0 else 0.0
    lag = max(0.0, now - event_ts) if event_ts > 0 else 999999.0
    return event_ms, event_ts, lag


def _handle(payload):
    global _messages, _last_message
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    symbol = str(data.get("s") or "").upper()
    if not symbol:
        return
    now = time.time()
    event_ms, event_ts, exchange_lag = _event_clock(data, now)
    event = data.get("e")
    if event == "aggTrade" and data.get("p") is not None:
        try:
            px = float(data["p"]); qty = float(data.get("q") or 0)
        except (TypeError,ValueError,OverflowError):
            return
        if not math.isfinite(px) or not math.isfinite(qty) or px<=0 or qty<0:
            return
        _prices[symbol] = {
            "price": px,
            "ts": now,
            "event_ms": event_ms,
            "event_ts": event_ts,
            "exchange_lag_sec": exchange_lag,
        }
        # Only truly fresh exchange-time events may contribute to ENTRY NOW flow.
        if qty > 0 and event_ts > 0 and exchange_lag <= _MAX_EXCHANGE_LAG_SEC:
            notional = px * qty
            sec = int(event_ts)
            rows = _trade_flow[symbol]
            buy = notional if not bool(data.get("m")) else 0.0
            sell = notional if bool(data.get("m")) else 0.0
            # Reject out-of-order stale buckets rather than making them look current.
            if rows and sec < int(rows[-1][0]):
                pass
            elif rows and rows[-1][0] == sec:
                bucket = rows[-1]
                bucket[1] += buy
                bucket[2] += sell
                bucket[3] += 1
                bucket[4] = now
            else:
                if rows:
                    prev = rows[-1]
                    _record_tape(
                        symbol,
                        "flow_1s",
                        {
                            "sec": int(prev[0]),
                            "buy_notional": float(prev[1]),
                            "sell_notional": float(prev[2]),
                            "trades": int(prev[3]),
                        },
                        exchange_ms=int(prev[0]) * 1000,
                        recv_ts=now,
                    )
                rows.append([sec, buy, sell, 1, now])
    if event == "bookTicker" or ("b" in data and "a" in data and event is None):
        _record_tape(
            symbol,
            "bookTicker",
            dict(data),
            exchange_ms=int(data.get("E") or data.get("T") or 0),
            recv_ts=now,
        )
        try:
            bid = float(data.get("b") or 0); ask = float(data.get("a") or 0)
        except (TypeError,ValueError,OverflowError):
            return
        if not math.isfinite(bid) or not math.isfinite(ask) or bid<=0 or ask<=0 or ask<bid:
            return
        # Futures bookTicker normally carries exchange timestamps, but the
        # routed stream contract should remain usable if a quote arrives without
        # E/T. In that case use receive-time only for this quote feed. Safety is
        # preserved by the <=3s cross-feed freshness gate and independent L2
        # top-of-book coherence check before ENTRY NOW.
        quote_event_ts = event_ts if event_ts > 0 else now
        quote_exchange_lag = exchange_lag if event_ts > 0 else 0.0
        _books[symbol] = {
            "bid": bid,
            "ask": ask,
            "ts": now,
            "event_ms": event_ms,
            "event_ts": quote_event_ts,
            "exchange_lag_sec": quote_exchange_lag,
            "timestamp_source": "exchange" if event_ts > 0 else "recv",
        }
    _messages += 1
    _last_message = now


def price(symbol, max_age=20):
    row = _prices.get(str(symbol).upper())
    now = time.time()
    if not row or now - row["ts"] > max_age:
        return None
    if float(row.get("exchange_lag_sec", 999999)) > _MAX_EXCHANGE_LAG_SEC:
        return None
    if not row.get("event_ts") or now - float(row["event_ts"]) > max_age:
        return None
    return float(row["price"])


def book(symbol, max_age=20):
    row = _books.get(str(symbol).upper())
    now = time.time()
    if not row or now - row["ts"] > max_age:
        return None
    if float(row.get("exchange_lag_sec", 999999)) > _MAX_EXCHANGE_LAG_SEC:
        return None
    if not row.get("event_ts") or now - float(row["event_ts"]) > max_age:
        return None
    return dict(row)


def flow(symbol, window_sec=60, max_age=20):
    """Rolling aggressive-flow summary from 1-second aggTrade buckets."""
    symbol = str(symbol).upper()
    rows = _trade_flow.get(symbol)
    now = time.time()
    if not rows:
        return None
    while rows and now - float(rows[0][0]) > 180:
        rows.popleft()
    recent = [r for r in rows if now - float(r[0]) <= float(window_sec)]
    if not recent:
        return None
    latest = max(float(r[0]) for r in recent)
    if now - latest > float(max_age):
        return None
    buy = sum(float(r[1]) for r in recent)
    sell = sum(float(r[2]) for r in recent)
    trades = sum(int(r[3]) for r in recent)
    bucket_totals = [float(r[1]) + float(r[2]) for r in recent]
    total = buy + sell
    if total <= 0:
        return None
    first_sec=min(float(r[0]) for r in recent)
    active_seconds=len(recent)
    coverage=max(0.0,latest-first_sec)
    max_bucket_share=(max(bucket_totals)/total) if bucket_totals else 1.0
    recent10=[r for r in rows if now-float(r[0])<=10.0]
    total10=sum(float(r[1])+float(r[2]) for r in recent10)
    buy10=sum(float(r[1]) for r in recent10)
    trades10=sum(int(r[3]) for r in recent10)
    active10=len(recent10)
    coverage10=(max(float(r[0]) for r in recent10)-min(float(r[0]) for r in recent10)) if len(recent10)>1 else 0.0
    max_bucket10=(max(float(r[1])+float(r[2]) for r in recent10)/total10) if total10>0 else 1.0
    buy_share10=(buy10/total10) if total10>0 else .5
    return {
        "buy_notional": buy,
        "sell_notional": sell,
        "total_notional": total,
        "buy_share": buy / total,
        "imbalance": (buy - sell) / total,
        "trades": trades,
        "age_sec": max(0.0, now - latest),
        "window_sec": float(window_sec),
        "active_seconds": int(active_seconds),
        "coverage_sec": float(coverage),
        "max_bucket_share": float(max_bucket_share),
        "recent10_total_notional": float(total10),
        "recent10_trades": int(trades10),
        "active_seconds_10s": int(active10),
        "coverage_10s": float(coverage10),
        "max_bucket_share_10s": float(max_bucket10),
        "buy_share_10s": float(buy_share10),
    }


def _sync_connected():
    global _connected
    _connected = bool(_connected_public and _connected_market)


def _route_age(last_ts):
    return None if not last_ts else max(0.0, time.time() - float(last_ts))


def health():
    age = _route_age(_last_message)
    public_age = _route_age(_last_public_message)
    market_age = _route_age(_last_market_message)
    return {
        "connected": bool(_connected),
        "public_connected": bool(_connected_public),
        "market_connected": bool(_connected_market),
        "messages": int(_messages),
        "messages_public": int(_messages_public),
        "messages_market": int(_messages_market),
        "reconnects": int(_reconnects),
        "reconnects_public": int(_reconnects_public),
        "reconnects_market": int(_reconnects_market),
        "last_age_sec": age,
        "public_last_age_sec": public_age,
        "market_last_age_sec": market_age,
        "symbols": list(_symbols),
        "public_symbols": list(_public_symbols),
        "market_symbols": list(_market_symbols),
        "fresh_prices": sum(1 for r in _prices.values() if time.time() - r["ts"] <= 20),
        "fresh_books": sum(1 for r in _books.values() if time.time() - r["ts"] <= 20),
        "fresh_flow": sum(1 for symbol in _trade_flow if flow(symbol, 60, 20) is not None),
        "exchange_lagged_prices": sum(
            1
            for r in _prices.values()
            if float(r.get("exchange_lag_sec", 999999)) > _MAX_EXCHANGE_LAG_SEC
        ),
        "exchange_lagged_books": sum(
            1
            for r in _books.values()
            if float(r.get("exchange_lag_sec", 999999)) > _MAX_EXCHANGE_LAG_SEC
        ),
        "recv_time_books": sum(1 for r in _books.values() if r.get("timestamp_source")=="recv"),
        "route_stalls_public": int(_route_stalls_public),
        "route_stalls_market": int(_route_stalls_market),
    }


def _safe_desired_symbols():
    try:
        return desired_symbols()
    except Exception as exc:
        # Database/transient read failures must not kill live monitoring.
        log.warning("V11 websocket symbol refresh failed, BTC fallback: %s", exc)
        return ("BTCUSDT",)


async def _route_monitor(route):
    """Reconnect one routed transport independently of the other."""
    global _connected_public, _connected_market, _reconnects, _reconnects_public
    global _reconnects_market, _last_public_message, _last_market_message
    global _messages_public, _messages_market, _symbols, _public_symbols, _market_symbols
    global _route_stalls_public, _route_stalls_market

    if route not in {"public", "market"}:
        raise ValueError(f"unknown websocket route: {route}")

    backoff = 1
    while True:
        try:
            current = _safe_desired_symbols()
            _symbols = current
            if route == "public":
                url = _public_url(current)
                _public_symbols = current
            else:
                url = _market_url(current)
                _market_symbols = current

            async with connect(
                url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_queue=512,
            ) as ws:
                if route == "public":
                    _connected_public = True
                else:
                    _connected_market = True
                _sync_connected()
                backoff = 1
                checked = time.time()
                session_started = checked

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=20)
                        now = time.time()
                        if route == "public":
                            _messages_public += 1
                            _last_public_message = now
                        else:
                            _messages_market += 1
                            _last_market_message = now
                        _handle(json.loads(raw))
                    except asyncio.TimeoutError:
                        # A quiet route is allowed to continue until the next health
                        # consumer decides the feed is stale; ping/pong still guards
                        # transport liveness.
                        pass

                    if time.time() - checked >= 20:
                        checked = time.time()
                        if _safe_desired_symbols() != current:
                            break
                        # Fail-safe self-heal for a transport that still answers
                        # ping/pong but stopped delivering application data. BTC is
                        # always subscribed, so 45s without route data is abnormal.
                        last = _last_public_message if route == "public" else _last_market_message
                        reference = float(last or session_started)
                        if time.time() - reference > _ROUTE_STALL_SEC:
                            if route == "public":
                                _route_stalls_public += 1
                            else:
                                _route_stalls_market += 1
                            raise RuntimeError(f"{route} websocket application data stalled >{_ROUTE_STALL_SEC:.0f}s")
        except asyncio.CancelledError:
            if route == "public":
                _connected_public = False
            else:
                _connected_market = False
            _sync_connected()
            raise
        except Exception as exc:
            if route == "public":
                _connected_public = False
                _reconnects_public += 1
            else:
                _connected_market = False
                _reconnects_market += 1
            _reconnects += 1
            _sync_connected()
            log.warning("V11 %s websocket reconnect: %s", route, exc)
            await asyncio.sleep(backoff)
            backoff = min(30, backoff * 2)
        finally:
            if route == "public":
                _connected_public = False
            else:
                _connected_market = False
            _sync_connected()


async def monitor():
    """Run PUBLIC quote and MARKET aggTrade routes as independent tasks."""
    tasks = [
        asyncio.create_task(_route_monitor("public"), name="v11-live-public"),
        asyncio.create_task(_route_monitor("market"), name="v11-live-market"),
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
