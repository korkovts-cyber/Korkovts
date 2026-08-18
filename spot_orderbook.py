"""V11.8 local Binance Spot order book + data-health watchdog.

The production Spot BUY path uses this module only for the *second* confirmation.
Broad discovery can still use REST snapshots, but a real BUY NOW requires a
sequence-synchronised local book. The synchronisation follows Binance's
snapshot + diff-depth update-id contract (U/u).

No account API key is used. This is public market data only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

from websockets.asyncio.client import connect

from spot_market import depth as rest_depth

log=logging.getLogger(__name__)

_WS_BASE="wss://stream.binance.com:9443/stream?streams="
_MAX_SYMBOLS=10
_EVENT_MAX_AGE=3.0
_MIN_STABILITY_SAMPLES=8
_MIN_STABILITY_COVERAGE=5.0
_RECENT_GAP_BLOCK_SEC=30.0
_MAX_EXCHANGE_LAG_SEC=2.5
_SNAPSHOT_LIMIT=100


@dataclass
class LocalBook:
    symbol:str
    bids:dict[float,float]=field(default_factory=dict)
    asks:dict[float,float]=field(default_factory=dict)
    last_update_id:int=0
    synced:bool=False
    snapshot_ts:float=0.0
    last_event_ts:float=0.0
    updates:int=0
    gaps:int=0
    resyncs:int=0
    last_error:str=""
    last_gap_ts:float=0.0
    last_exchange_event_ms:int=0
    last_exchange_lag_sec:float=999999.0
    history:deque=field(default_factory=lambda:deque(maxlen=120))

    def reset(self,reason=""):
        # A reconnect/gap breaks microstructure continuity. Never let pre-gap
        # replenishment/history contribute to a new BUY confirmation.
        self.bids.clear(); self.asks.clear()
        self.history.clear()
        self.last_update_id=0; self.synced=False
        self.snapshot_ts=0.0; self.last_event_ts=0.0
        self.last_exchange_event_ms=0; self.last_exchange_lag_sec=999999.0
        if reason:
            self.last_error=str(reason)[:300]

    def load_snapshot(self,snapshot):
        self.bids={float(p):float(q) for p,q in snapshot.get("bids",[]) if float(q)>0}
        self.asks={float(p):float(q) for p,q in snapshot.get("asks",[]) if float(q)>0}
        self.last_update_id=int(snapshot.get("lastUpdateId",0) or 0)
        self.snapshot_ts=float(snapshot.get("fetched_at") or time.time())
        self.synced=self.last_update_id>0 and bool(self.bids) and bool(self.asks)
        self.last_error=""

    @staticmethod
    def _update_side(side,levels):
        for p,q in levels or ():
            price=float(p); qty=float(q)
            if qty<=0:
                side.pop(price,None)
            else:
                side[price]=qty

    def apply_event(self,event,now=None):
        """Apply one Binance diff-depth event.

        Returns: APPLIED, IGNORED, GAP, NOT_SYNCED.
        """
        if not self.synced:
            return "NOT_SYNCED"
        U=int(event.get("U",0) or 0); u=int(event.get("u",0) or 0)
        if U<=0 or u<=0 or u<U:
            self.gaps+=1; self.last_gap_ts=float(now or time.time()); self.synced=False; self.last_error="invalid update ids"
            return "GAP"
        # Event fully predates the local state.
        if u<=self.last_update_id:
            return "IGNORED"
        # Binance contract: a first/subsequent applicable event must bridge
        # lastUpdateId+1. If U jumps beyond it, at least one event was missed.
        if U>self.last_update_id+1:
            self.gaps+=1; self.last_gap_ts=float(now or time.time()); self.synced=False
            self.last_error=f"sequence gap local={self.last_update_id} U={U} u={u}"
            return "GAP"
        # An event can overlap the current id; u>last and U<=last+1 is valid.
        recv_ts=float(now or time.time())
        event_ms=int(event.get("E",0) or 0)
        self.last_exchange_event_ms=event_ms
        self.last_exchange_lag_sec=(
            max(0.0,recv_ts-event_ms/1000.0) if event_ms>0 else 999999.0
        )
        self._update_side(self.bids,event.get("b") or [])
        self._update_side(self.asks,event.get("a") or [])
        self.last_update_id=u
        self.last_event_ts=recv_ts
        self.updates+=1
        if not self.bids or not self.asks:
            self.gaps+=1; self.last_gap_ts=float(now or time.time())
            self.synced=False; self.last_error="book side became empty"
            return "GAP"
        self._sample(float(now or time.time()))
        return "APPLIED"

    def _sample(self,now):
        if not self.bids or not self.asks:
            return
        bid=max(self.bids); ask=min(self.asks); mid=(bid+ask)/2
        if mid<=0 or bid>=ask:
            return
        # Throttle history to roughly 2 samples/sec even on @100ms streams.
        if self.history and now-float(self.history[-1][0])<.45:
            return
        lo=mid*(1-20/10000); hi=mid*(1+20/10000)
        b=sum(p*q for p,q in self.bids.items() if p>=lo)
        a=sum(p*q for p,q in self.asks.items() if p<=hi)
        imb=(b-a)/(b+a) if b+a else 0.0
        spread=(ask-bid)/mid*10000
        self.history.append((now,spread,b,a,imb))

    def top(self,levels=100):
        n=max(1,min(int(levels),500))
        bids=sorted(self.bids.items(),key=lambda x:x[0],reverse=True)[:n]
        asks=sorted(self.asks.items(),key=lambda x:x[0])[:n]
        return bids,asks


_books:dict[str,LocalBook]={}
_buffers=defaultdict(lambda:deque(maxlen=5000))
_sync_tasks:dict[str,asyncio.Task]={}
_provider:Callable|None=None
_connected=False
_connection_started=0.0
_last_message=0.0
_messages=0
_reconnects=0
_active_symbols=()
_stop_event:asyncio.Event|None=None


def set_symbol_provider(provider):
    global _provider
    _provider=provider


def desired_symbols(limit=_MAX_SYMBOLS):
    try:
        rows=_provider() if _provider is not None else ()
    except Exception as exc:
        log.warning("V11.8 Spot orderbook provider failed: %s",exc)
        rows=()
    out=[]; seen=set()
    for symbol in rows or ():
        s=str(symbol or "").upper()
        if not s or s in seen:
            continue
        seen.add(s); out.append(s)
        if len(out)>=int(limit):
            break
    return tuple(out)


def _stream_url(symbols):
    streams=[f"{s.lower()}@depth@100ms" for s in symbols]
    return _WS_BASE+"/".join(streams)


def _book(symbol):
    symbol=str(symbol or "").upper()
    if not symbol:
        return None
    row=_books.get(symbol)
    if row is None:
        row=LocalBook(symbol)
        _books[symbol]=row
    return row


async def _sync_symbol(symbol):
    """REST snapshot while WS events continue buffering."""
    symbol=str(symbol).upper(); book=_book(symbol)
    if book is None:
        return False
    book.resyncs+=1
    try:
        snapshot=await asyncio.wait_for(rest_depth(symbol,_SNAPSHOT_LIMIT),timeout=15)
        # Snapshot is accepted only if buffered diff events can bridge it.
        last=int(snapshot.get("lastUpdateId",0) or 0)
        if last<=0:
            raise RuntimeError("snapshot lastUpdateId missing")
        buffered=list(_buffers[symbol])
        # If every buffered event is older, load and wait for the next event.
        relevant=[e for e in buffered if int(e.get("u",0) or 0)>last]
        book.load_snapshot(snapshot)
        if relevant:
            first=relevant[0]
            U=int(first.get("U",0) or 0); u=int(first.get("u",0) or 0)
            if not (U<=last+1<=u):
                # We may have opened the snapshot too early relative to the
                # first retained event. Reset and let the monitor retry.
                book.reset(f"snapshot bridge missing last={last} first={U}-{u}")
                return False
            for event in relevant:
                state=book.apply_event(event)
                if state=="GAP":
                    return False
        _buffers[symbol].clear()
        return bool(book.synced)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        book.reset(f"snapshot sync failed: {type(exc).__name__}: {exc}")
        log.debug("V11.8 Spot orderbook sync failed %s: %s",symbol,exc)
        return False
    finally:
        _sync_tasks.pop(symbol,None)


def _ensure_sync(symbol):
    symbol=str(symbol).upper()
    task=_sync_tasks.get(symbol)
    if task is None or task.done():
        _sync_tasks[symbol]=asyncio.create_task(
            _sync_symbol(symbol),name=f"v118-spot-book-sync-{symbol}"
        )


def handle_event(payload,now=None):
    """Deterministic event handler, directly unit-testable."""
    global _messages,_last_message
    data=payload.get("data",payload) if isinstance(payload,dict) else {}
    if str(data.get("e") or "")!="depthUpdate":
        return "IGNORED"
    symbol=str(data.get("s") or "").upper()
    if not symbol:
        return "IGNORED"
    now=float(now or time.time())
    _messages+=1; _last_message=now
    book=_book(symbol)
    if not book.synced:
        _buffers[symbol].append(dict(data))
        _ensure_sync(symbol)
        return "BUFFERED"
    state=book.apply_event(data,now=now)
    if state=="GAP":
        _buffers[symbol].clear(); _buffers[symbol].append(dict(data))
        _ensure_sync(symbol)
    return state


def stability(symbol,max_age=_EVENT_MAX_AGE):
    s=str(symbol or "").upper(); book=_books.get(s); now=time.time()
    base=symbol_health(s,max_age)
    if not book or not base.get("healthy"):
        return {**base,"stability_score":0.0,"samples":0,"coverage_sec":0.0}
    rows=[r for r in book.history if now-r[0]<=60]
    if not rows:
        return {**base,"stability_score":40.0,"samples":0,"coverage_sec":0.0}
    import statistics
    spreads=[r[1] for r in rows]; bids=[r[2] for r in rows]; imbs=[r[4] for r in rows]
    current=rows[-1]; median_bid=statistics.median(bids) if bids else 0.0
    median_spread=statistics.median(spreads) if spreads else 999.0
    median_imb=statistics.median(imbs) if imbs else 0.0
    replenish=(current[2]/median_bid) if median_bid>0 else 0.0
    coverage=max(0.0,rows[-1][0]-rows[0][0]) if len(rows)>1 else 0.0
    score=25.0
    score+=20 if current[1]<=4.0 and current[1]<=max(1.0,median_spread*1.8) else 0
    score+=20 if replenish>=.60 else (10 if replenish>=.40 else 0)
    score+=15 if median_imb>=-.10 else (7 if median_imb>=-.25 else 0)
    gap_age=None if not book.last_gap_ts else max(0.0,now-book.last_gap_ts)
    score+=10 if gap_age is None or gap_age>=60 else 0
    score+=10 if len(rows)>=_MIN_STABILITY_SAMPLES and coverage>=_MIN_STABILITY_COVERAGE else 0
    reason=str(base.get("reason") or "ok")
    # Connectivity/synchronisation alone is not "stable" market structure.
    # A fresh session must rebuild several seconds of post-snapshot history, and
    # a recent sequence gap gets a longer fail-closed cooldown.
    if len(rows)<_MIN_STABILITY_SAMPLES or coverage<_MIN_STABILITY_COVERAGE:
        score=min(score,55.0); reason="local depth stability warming up"
    if gap_age is not None and gap_age<_RECENT_GAP_BLOCK_SEC:
        score=min(score,45.0); reason="recent local depth sequence gap"
    return {
        **base,"reason":reason,"stability_score":min(100.0,score),"samples":len(rows),
        "coverage_sec":coverage,"median_spread_bps":median_spread,
        "current_spread_bps":current[1],"bid_replenishment_ratio":replenish,
        "median_imbalance_20bps":median_imb,
        "last_gap_age_sec":gap_age,
    }


def snapshot(symbol,max_age=_EVENT_MAX_AGE,levels=100):
    """Return a microstructure-compatible book only when sequence/freshness is healthy."""
    book=_books.get(str(symbol or "").upper())
    now=time.time()
    if not book or not book.synced:
        return None
    age=now-float(book.last_event_ts or 0)
    if book.last_event_ts<=0 or age>float(max_age):
        return None
    bids,asks=book.top(levels)
    if not bids or not asks or bids[0][0]>=asks[0][0]:
        return None
    stable=stability(symbol,max_age)
    return {
        "lastUpdateId":int(book.last_update_id),
        "bids":bids,"asks":asks,"fetched_at":now,
        "source":"local_ws","event_age_sec":max(0.0,age),
        "sequence_synced":True,"gaps":int(book.gaps),
        "resyncs":int(book.resyncs),"updates":int(book.updates),
        "healthy":bool(stable.get("healthy")),
        "stability_score":float(stable.get("stability_score",0) or 0),
        "bid_replenishment_ratio":float(stable.get("bid_replenishment_ratio",0) or 0),
        "median_imbalance_20bps":float(stable.get("median_imbalance_20bps",0) or 0),
        "book_samples":int(stable.get("samples",0) or 0),
        "book_coverage_sec":float(stable.get("coverage_sec",0) or 0),
        "exchange_lag_sec":float(stable.get("exchange_lag_sec",999999)),
    }


def symbol_health(symbol,max_age=_EVENT_MAX_AGE):
    s=str(symbol or "").upper(); book=_books.get(s); now=time.time()
    if not book:
        return {"symbol":s,"healthy":False,"reason":"not subscribed","synced":False}
    age=None if not book.last_event_ts else max(0.0,now-book.last_event_ts)
    exchange_lag=float(book.last_exchange_lag_sec if book.last_exchange_event_ms>0 else 999999.0)
    healthy=bool(
        _connected and book.synced and age is not None and age<=float(max_age)
        and exchange_lag<=_MAX_EXCHANGE_LAG_SEC
        and book.last_exchange_event_ms>0
        and book.bids and book.asks
    )
    reason="ok" if healthy else (
        book.last_error or ("websocket disconnected" if not _connected else
        "sequence not synced" if not book.synced else
        "exchange event timestamp missing" if book.last_exchange_event_ms<=0 else
        f"exchange depth lag {exchange_lag:.2f}s" if exchange_lag>_MAX_EXCHANGE_LAG_SEC else
        "depth stream stale")
    )
    return {
        "symbol":s,"healthy":healthy,"reason":reason,
        "connected":bool(_connected),"synced":bool(book.synced),
        "event_age_sec":age,"exchange_lag_sec":exchange_lag,
        "exchange_event_ms":int(book.last_exchange_event_ms),
        "last_update_id":int(book.last_update_id),
        "updates":int(book.updates),"gaps":int(book.gaps),"resyncs":int(book.resyncs),
    }


def health():
    now=time.time(); wanted=desired_symbols()
    rows=[symbol_health(s) for s in wanted]
    return {
        "connected":bool(_connected),"messages":int(_messages),
        "reconnects":int(_reconnects),
        "last_age_sec":None if not _last_message else max(0.0,now-_last_message),
        "symbols":list(_active_symbols),"wanted":list(wanted),
        "healthy":sum(1 for r in rows if r.get("healthy")),
        "synced":sum(1 for r in rows if r.get("synced")),
        "total":len(rows),
        "gaps":sum(int((_books.get(s) or LocalBook(s)).gaps) for s in wanted),
        "resyncs":sum(int((_books.get(s) or LocalBook(s)).resyncs) for s in wanted),
    }


async def monitor():
    global _connected,_reconnects,_active_symbols,_connection_started,_stop_event
    backoff=1; _stop_event=asyncio.Event()
    try:
        while not _stop_event.is_set():
            symbols=desired_symbols()
            if not symbols:
                _connected=False; _active_symbols=()
                await asyncio.sleep(2)
                continue
            try:
                _active_symbols=symbols
                async with connect(
                    _stream_url(symbols),open_timeout=10,ping_interval=20,
                    ping_timeout=20,close_timeout=5,max_queue=2048,
                ) as ws:
                    _connected=True; _connection_started=time.time(); backoff=1
                    for symbol in symbols:
                        # New connection means diff continuity is new; force a
                        # fresh snapshot for every subscribed symbol.
                        b=_book(symbol); b.reset("new websocket session")
                        _buffers[symbol].clear(); _ensure_sync(symbol)
                    checked=time.time()
                    while not _stop_event.is_set():
                        try:
                            raw=await asyncio.wait_for(ws.recv(),timeout=10)
                            handle_event(json.loads(raw))
                        except asyncio.TimeoutError:
                            pass
                        if time.time()-checked>=10:
                            checked=time.time()
                            if desired_symbols()!=symbols:
                                break
                            # A silent connection is not healthy even if TCP is alive.
                            if _last_message and time.time()-_last_message>15:
                                raise RuntimeError("Spot depth stream silent >15s")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _connected=False; _reconnects+=1
                log.warning("V11.8 Spot orderbook reconnect: %s",exc)
                await asyncio.sleep(backoff); backoff=min(30,backoff*2)
            finally:
                _connected=False
    finally:
        _connected=False
        tasks=list(_sync_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)
        _sync_tasks.clear()


async def stop():
    if _stop_event is not None:
        _stop_event.set()
