"""V11.11 sequence-synchronised Binance USD-M Futures local order book.

Implements Binance's documented snapshot + diff-depth contract for USD-M:
- buffer @depth@100ms events while fetching REST snapshot;
- first event must bridge snapshot lastUpdateId;
- every subsequent event must have pu == previous u;
- any gap forces a resync and clears stability history.
Public market data only; no account keys or order placement.
"""
from __future__ import annotations
import asyncio, json, logging, time, statistics, math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable
from websockets.asyncio.client import connect
from app.market import _get

log=logging.getLogger(__name__)
_WS_BASE="wss://fstream.binance.com/public/stream?streams="
_MAX_SYMBOLS=20
_EVENT_MAX_AGE=3.0
_MAX_EXCHANGE_LAG_SEC=2.5
_MIN_SAMPLES=8
_MIN_COVERAGE=5.0
_RECENT_GAP_BLOCK_SEC=30.0
_SNAPSHOT_LIMIT=1000
_TAPE_ANCHOR_SEC=45.0

try:
    from v11110_tape import record_event as _record_tape, prune as _prune_tape
except Exception:
    def _record_tape(*args,**kwargs): return None
    def _prune_tape(*args,**kwargs): return 0

@dataclass
class LocalBook:
    symbol:str
    bids:dict[float,float]=field(default_factory=dict)
    asks:dict[float,float]=field(default_factory=dict)
    last_update_id:int=0
    synced:bool=False
    bridge_pending:bool=False
    snapshot_ts:float=0.0
    last_event_ts:float=0.0
    updates:int=0; gaps:int=0; resyncs:int=0
    last_error:str=""; last_gap_ts:float=0.0
    last_exchange_event_ms:int=0; last_exchange_lag_sec:float=999999.0
    last_tape_anchor_ts:float=0.0
    history:deque=field(default_factory=lambda:deque(maxlen=180))

    def reset(self,reason=""):
        self.bids.clear(); self.asks.clear(); self.history.clear()
        self.last_update_id=0; self.synced=False; self.bridge_pending=False; self.snapshot_ts=0.0; self.last_event_ts=0.0
        self.last_exchange_event_ms=0; self.last_exchange_lag_sec=999999.0; self.last_tape_anchor_ts=0.0
        if reason: self.last_error=str(reason)[:300]

    def load_snapshot(self,snap):
        def parsed(rows):
            out={}
            for p,q in rows or ():
                try: p=float(p); q=float(q)
                except (TypeError,ValueError,OverflowError): continue
                if math.isfinite(p) and math.isfinite(q) and p>0 and q>0:
                    out[p]=q
            return out
        self.bids=parsed(snap.get("bids",[])); self.asks=parsed(snap.get("asks",[]))
        self.last_update_id=int(snap.get("lastUpdateId",0) or 0)
        self.snapshot_ts=float(snap.get("fetched_at") or time.time())
        self.synced=self.last_update_id>0 and bool(self.bids) and bool(self.asks)
        self.bridge_pending=bool(self.synced)
        self.last_error=""

    @staticmethod
    def _update(side,levels):
        for p,q in levels or ():
            try: p=float(p); q=float(q)
            except (TypeError,ValueError,OverflowError): return False
            if not math.isfinite(p) or not math.isfinite(q) or p<=0 or q<0:
                return False
            if q==0: side.pop(p,None)
            else: side[p]=q
        return True

    def apply_event(self,event,now=None,first_after_snapshot=False):
        if not self.synced: return "NOT_SYNCED"
        U=int(event.get("U",0) or 0); u=int(event.get("u",0) or 0)
        pu=int(event.get("pu",0) or 0)
        if U<=0 or u<=0 or u<U:
            return self._gap("invalid update ids",now)
        if u<self.last_update_id:
            return "IGNORED"
        if first_after_snapshot:
            if not (U<=self.last_update_id<=u):
                return self._gap(f"snapshot bridge missing local={self.last_update_id} event={U}-{u}",now)
            self.bridge_pending=False
        else:
            # USD-M documented continuity contract: pu of each new event equals prior u.
            if pu<=0 or pu!=self.last_update_id:
                return self._gap(f"pu continuity gap local={self.last_update_id} pu={pu} U={U} u={u}",now)
        recv=float(now or time.time()); event_ms=int(event.get("E",0) or 0)
        self.last_exchange_event_ms=event_ms
        self.last_exchange_lag_sec=max(0.0,recv-event_ms/1000.0) if event_ms>0 else 999999.0
        if not self._update(self.bids,event.get("b") or []) or not self._update(self.asks,event.get("a") or []):
            return self._gap("invalid/non-finite book level",now)
        self.last_update_id=u; self.last_event_ts=recv; self.updates+=1
        if not self.bids or not self.asks:
            return self._gap("book side became empty",now)
        self._sample(recv)
        return "APPLIED"

    def _gap(self,reason,now=None):
        self.gaps+=1; self.last_gap_ts=float(now or time.time()); self.synced=False; self.last_error=reason
        return "GAP"

    def _sample(self,now):
        if self.history and now-float(self.history[-1][0])<.45: return
        bid=max(self.bids); ask=min(self.asks); mid=(bid+ask)/2
        if mid<=0 or bid>=ask: return
        lo=mid*(1-20/10000); hi=mid*(1+20/10000)
        b=sum(p*q for p,q in self.bids.items() if p>=lo)
        a=sum(p*q for p,q in self.asks.items() if p<=hi)
        imb=(b-a)/(b+a) if b+a else 0.0
        spread=(ask-bid)/mid*10000
        self.history.append((now,spread,b,a,imb))

    def top(self,levels=100):
        n=max(1,min(int(levels),1000))
        return (sorted(self.bids.items(),reverse=True)[:n], sorted(self.asks.items())[:n])

_books={}; _buffers=defaultdict(lambda:deque(maxlen=10000)); _sync_tasks={}
_provider:Callable|None=None; _connected=False; _messages=0; _reconnects=0; _last_message=0.0; _active_symbols=(); _stop_event=None

def set_symbol_provider(provider):
    global _provider; _provider=provider

def desired_symbols(limit=_MAX_SYMBOLS):
    try: rows=_provider() if _provider else ()
    except Exception as exc:
        log.warning("V11.11 Futures L2 provider failed: %s",exc); rows=()
    out=[]; seen=set()
    for s in rows or ():
        s=str(s or "").upper()
        if s and s not in seen:
            seen.add(s); out.append(s)
        if len(out)>=int(limit): break
    return tuple(out)

def _url(symbols): return _WS_BASE+"/".join(f"{s.lower()}@depth@100ms" for s in symbols)

def _book(symbol):
    s=str(symbol or "").upper()
    if not s: return None
    if s not in _books: _books[s]=LocalBook(s)
    return _books[s]

async def _rest_snapshot(symbol):
    data=await _get("/fapi/v1/depth",{"symbol":symbol,"limit":_SNAPSHOT_LIMIT})
    data=dict(data or {}); data["fetched_at"]=time.time(); return data

async def _sync_symbol(symbol):
    symbol=str(symbol).upper(); book=_book(symbol)
    if not book: return False
    book.resyncs+=1
    try:
        snap=await asyncio.wait_for(_rest_snapshot(symbol),timeout=15)
        snap["source"]="rest"
        last=int(snap.get("lastUpdateId",0) or 0)
        if last<=0: raise RuntimeError("snapshot lastUpdateId missing")
        buffered=list(_buffers[symbol])
        relevant=[e for e in buffered if int(e.get("u",0) or 0)>=last]
        book.load_snapshot(snap)
        _record_tape(symbol,"depth_snapshot",snap,exchange_ms=0)
        book.last_tape_anchor_ts=float(snap.get("fetched_at") or time.time())
        if relevant:
            first=relevant[0]
            if not (int(first.get("U",0) or 0)<=last<=int(first.get("u",0) or 0)):
                book.reset(f"snapshot bridge missing last={last}"); return False
            state=book.apply_event(first,first_after_snapshot=True)
            if state=="GAP": return False
            for event in relevant[1:]:
                if book.apply_event(event)=="GAP": return False
        _buffers[symbol].clear(); return bool(book.synced)
    except asyncio.CancelledError: raise
    except Exception as exc:
        book.reset(f"snapshot sync failed: {type(exc).__name__}: {exc}")
        log.debug("V11.11 Futures L2 sync failed %s: %s",symbol,exc); return False
    finally:
        _sync_tasks.pop(symbol,None)

def _ensure_sync(symbol):
    s=str(symbol).upper(); task=_sync_tasks.get(s)
    if task is None or task.done(): _sync_tasks[s]=asyncio.create_task(_sync_symbol(s),name=f"v11110-futures-l2-sync-{s}")

def _record_local_anchor(book,now):
    """Persist a periodic sequence-synchronised anchor for deterministic replay."""
    if not book or not book.synced or book.bridge_pending:
        return
    if float(now)-float(book.last_tape_anchor_ts or 0)<_TAPE_ANCHOR_SEC:
        return
    bids,asks=book.top(_SNAPSHOT_LIMIT)
    if not bids or not asks:
        return
    snap={
        "lastUpdateId":int(book.last_update_id),
        "bids":bids,"asks":asks,"fetched_at":float(now),"source":"local_anchor",
        "history":[list(row) for row in list(book.history)[-120:]],
    }
    _record_tape(book.symbol,"depth_snapshot",snap,exchange_ms=int(book.last_exchange_event_ms or 0),recv_ts=float(now))
    book.last_tape_anchor_ts=float(now)

def handle_event(payload,now=None):
    global _messages,_last_message
    data=payload.get("data",payload) if isinstance(payload,dict) else {}
    if str(data.get("e") or "")!="depthUpdate": return "IGNORED"
    symbol=str(data.get("s") or "").upper()
    if not symbol: return "IGNORED"
    now=float(now or time.time()); _messages+=1; _last_message=now
    _record_tape(symbol,"depth",dict(data),exchange_ms=int(data.get("E",0) or 0))
    book=_book(symbol)
    if not book.synced:
        _buffers[symbol].append(dict(data)); _ensure_sync(symbol); return "BUFFERED"
    state=book.apply_event(data,now=now,first_after_snapshot=bool(book.bridge_pending))
    if state=="APPLIED": _record_local_anchor(book,now)
    if state=="GAP":
        _buffers[symbol].clear(); _buffers[symbol].append(dict(data)); _ensure_sync(symbol)
    return state

def symbol_health(symbol,max_age=_EVENT_MAX_AGE):
    s=str(symbol or "").upper(); b=_books.get(s); now=time.time()
    if not b: return {"symbol":s,"healthy":False,"reason":"not subscribed","synced":False}
    age=None if not b.last_event_ts else max(0.0,now-b.last_event_ts)
    lag=float(b.last_exchange_lag_sec if b.last_exchange_event_ms else 999999.0)
    healthy=bool(_connected and b.synced and not b.bridge_pending and age is not None and age<=float(max_age) and lag<=_MAX_EXCHANGE_LAG_SEC and b.bids and b.asks)
    reason="ok" if healthy else (b.last_error or ("websocket disconnected" if not _connected else "sequence not synced" if not b.synced else "snapshot bridge pending" if b.bridge_pending else f"exchange depth lag {lag:.2f}s" if lag>_MAX_EXCHANGE_LAG_SEC else "depth stream stale"))
    return {"symbol":s,"healthy":healthy,"reason":reason,"connected":bool(_connected),"synced":bool(b.synced),"event_age_sec":age,"exchange_lag_sec":lag,"last_update_id":int(b.last_update_id),"bridge_pending":bool(b.bridge_pending),"updates":int(b.updates),"gaps":int(b.gaps),"resyncs":int(b.resyncs)}

def stability(symbol,max_age=_EVENT_MAX_AGE):
    s=str(symbol or "").upper(); b=_books.get(s); now=time.time(); base=symbol_health(s,max_age)
    if not b or not base.get("healthy"): return {**base,"stability_score":0.0,"samples":0,"coverage_sec":0.0}
    rows=[r for r in b.history if now-r[0]<=60]
    if not rows: return {**base,"stability_score":0.0,"samples":0,"coverage_sec":0.0,"reason":"local depth stability warming up"}
    spreads=[r[1] for r in rows]; bids=[r[2] for r in rows]; asks=[r[3] for r in rows]; imbs=[r[4] for r in rows]
    cur=rows[-1]; med_b=statistics.median(bids); med_a=statistics.median(asks); med_sp=statistics.median(spreads); med_imb=statistics.median(imbs)
    bid_rep=cur[2]/med_b if med_b>0 else 0.0; ask_rep=cur[3]/med_a if med_a>0 else 0.0
    coverage=max(0.0,rows[-1][0]-rows[0][0]) if len(rows)>1 else 0.0
    gap_age=None if not b.last_gap_ts else max(0.0,now-b.last_gap_ts)
    prior=[r for r in rows if 2.0 <= now-r[0] <= 6.0]
    if not prior:
        prior=rows[:-1][-8:]
    prior_bid=statistics.median([r[2] for r in prior]) if prior else cur[2]
    prior_ask=statistics.median([r[3] for r in prior]) if prior else cur[3]
    prior_spread=statistics.median([r[1] for r in prior]) if prior else cur[1]
    bid_change_2s=(cur[2]/prior_bid-1.0) if prior_bid>0 else 0.0
    ask_change_2s=(cur[3]/prior_ask-1.0) if prior_ask>0 else 0.0
    spread_ratio_2s=(cur[1]/prior_spread) if prior_spread>0 else 1.0
    recent5=[r for r in rows if now-r[0] <= 5.0]
    adverse_long=(sum(1 for r in recent5 if r[4] <= -.25)/len(recent5)) if recent5 else 0.0
    adverse_short=(sum(1 for r in recent5 if r[4] >= .25)/len(recent5)) if recent5 else 0.0
    score=20.0
    score+=20 if cur[1]<=4.0 and cur[1]<=max(1.0,med_sp*1.8) else 0
    score+=15 if bid_rep>=.55 else (8 if bid_rep>=.40 else 0)
    score+=15 if ask_rep>=.55 else (8 if ask_rep>=.40 else 0)
    score+=10 if abs(med_imb)<=.65 else 0
    score+=10 if gap_age is None or gap_age>=60 else 0
    score+=10 if len(rows)>=_MIN_SAMPLES and coverage>=_MIN_COVERAGE else 0
    reason="ok"
    if len(rows)<_MIN_SAMPLES or coverage<_MIN_COVERAGE: score=min(score,55.0); reason="local depth stability warming up"
    if gap_age is not None and gap_age<_RECENT_GAP_BLOCK_SEC: score=min(score,45.0); reason="recent local depth sequence gap"
    return {**base,"reason":reason,"stability_score":min(100.0,score),"samples":len(rows),"coverage_sec":coverage,"median_spread_bps":med_sp,"current_spread_bps":cur[1],"bid_replenishment_ratio":bid_rep,"ask_replenishment_ratio":ask_rep,"median_imbalance_20bps":med_imb,"last_gap_age_sec":gap_age,"bid_depth_change_2s":bid_change_2s,"ask_depth_change_2s":ask_change_2s,"spread_ratio_2s":spread_ratio_2s,"adverse_long_share_5s":adverse_long,"adverse_short_share_5s":adverse_short,"recent5_samples":len(recent5)}

def snapshot(symbol,max_age=_EVENT_MAX_AGE,levels=100):
    b=_books.get(str(symbol or "").upper()); now=time.time()
    if not b or not b.synced or not b.last_event_ts or now-b.last_event_ts>float(max_age): return None
    bids,asks=b.top(levels)
    if not bids or not asks or bids[0][0]>=asks[0][0]: return None
    st=stability(symbol,max_age)
    best_bid=float(bids[0][0]); best_ask=float(asks[0][0]); mid=(best_bid+best_ask)/2
    return {"lastUpdateId":int(b.last_update_id),"bids":bids,"asks":asks,"fetched_at":now,"source":"local_futures_ws","event_age_sec":max(0.0,now-b.last_event_ts),"sequence_synced":True,"bridge_pending":bool(b.bridge_pending),"healthy":bool(st.get("healthy")),"best_bid":best_bid,"best_ask":best_ask,"spread_bps":((best_ask-best_bid)/mid*10000 if mid>0 else 999999.0),"stability_score":float(st.get("stability_score",0) or 0),"bid_replenishment_ratio":float(st.get("bid_replenishment_ratio",0) or 0),"ask_replenishment_ratio":float(st.get("ask_replenishment_ratio",0) or 0),"median_imbalance_20bps":float(st.get("median_imbalance_20bps",0) or 0),"book_samples":int(st.get("samples",0) or 0),"book_coverage_sec":float(st.get("coverage_sec",0) or 0),"exchange_lag_sec":float(st.get("exchange_lag_sec",999999)),"bid_depth_change_2s":float(st.get("bid_depth_change_2s",0) or 0),"ask_depth_change_2s":float(st.get("ask_depth_change_2s",0) or 0),"spread_ratio_2s":float(st.get("spread_ratio_2s",1) or 1),"adverse_long_share_5s":float(st.get("adverse_long_share_5s",0) or 0),"adverse_short_share_5s":float(st.get("adverse_short_share_5s",0) or 0),"gaps":int(b.gaps),"resyncs":int(b.resyncs)}

def _prune_local(active):
    active=set(active or ())
    for s in list(_books):
        if s not in active:
            _books.pop(s,None); _buffers.pop(s,None)
            task=_sync_tasks.pop(s,None)
            if task is not None and not task.done(): task.cancel()
    _prune_tape(900)

def health():
    wanted=desired_symbols(); rows=[symbol_health(s) for s in wanted]
    return {"connected":bool(_connected),"messages":int(_messages),"reconnects":int(_reconnects),"last_age_sec":None if not _last_message else max(0.0,time.time()-_last_message),"symbols":list(_active_symbols),"wanted":list(wanted),"healthy":sum(1 for r in rows if r.get("healthy")),"synced":sum(1 for r in rows if r.get("synced")),"total":len(rows),"gaps":sum(int((_books.get(s) or LocalBook(s)).gaps) for s in wanted),"resyncs":sum(int((_books.get(s) or LocalBook(s)).resyncs) for s in wanted)}

async def monitor():
    global _connected,_reconnects,_active_symbols,_stop_event
    backoff=1; _stop_event=asyncio.Event()
    try:
        while not _stop_event.is_set():
            symbols=desired_symbols()
            if not symbols: _connected=False; _active_symbols=(); _prune_local(()); await asyncio.sleep(2); continue
            try:
                _active_symbols=symbols
                _prune_local(symbols)
                async with connect(_url(symbols),open_timeout=10,ping_interval=20,ping_timeout=20,close_timeout=5,max_queue=4096) as ws:
                    _connected=True; backoff=1
                    for s in symbols:
                        b=_book(s); b.reset("new websocket session"); _buffers[s].clear(); _ensure_sync(s)
                    checked=time.time()
                    while not _stop_event.is_set():
                        try: handle_event(json.loads(await asyncio.wait_for(ws.recv(),timeout=10)))
                        except asyncio.TimeoutError: pass
                        if time.time()-checked>=10:
                            checked=time.time()
                            if desired_symbols()!=symbols: break
                            if _last_message and time.time()-_last_message>15: raise RuntimeError("Futures depth stream silent >15s")
            except asyncio.CancelledError: raise
            except Exception as exc:
                _connected=False; _reconnects+=1; log.warning("V11.11 Futures L2 reconnect: %s",exc); await asyncio.sleep(backoff); backoff=min(30,backoff*2)
            finally: _connected=False
    finally:
        _connected=False
        tasks=list(_sync_tasks.values())
        for t in tasks: t.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        _sync_tasks.clear()

async def stop():
    if _stop_event is not None: _stop_event.set()
