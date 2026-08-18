"""V11.11 bounded market-tape recorder for forensic decision replay.

Keeps only public market data in memory and persists compressed decision bundles
for ARMED/READY/ENTRY checks. It never stores Telegram tokens or API secrets.
"""
from __future__ import annotations
import gzip, json, os, sqlite3, time
from collections import defaultdict, deque
from contextlib import contextmanager

try:
    from app.config import DATABASE_PATH as _DB_PATH
except Exception:
    _DB_PATH=os.getenv("DATABASE_PATH","data/signals.db")

_MAX_EVENTS_PER_SYMBOL=max(800,min(4000,int(os.getenv("V11110_TAPE_EVENTS","2500"))))
_RETENTION=max(100,min(500,int(os.getenv("V11110_TAPE_BUNDLES","200"))))
_PER_SYMBOL_RETENTION=max(10,min(80,int(os.getenv("V11120_TAPE_PER_SYMBOL","30"))))
_MAX_BUNDLE_RAW_BYTES=max(500_000,min(4_000_000,int(os.getenv("V11120_TAPE_MAX_RAW_BYTES","1500000"))))
_buffers=defaultdict(lambda:deque(maxlen=_MAX_EVENTS_PER_SYMBOL))
_last_capture={}
_last_event_record={}

_SECRET_KEYS={"token","telegram_bot_token","api_key","apikey","secret","authorization","x_bearer_token"}

def _clean(value,depth=0):
    if depth>5: return "<truncated>"
    if isinstance(value,dict):
        out={}
        for k,v in value.items():
            ks=str(k)
            if ks.lower() in _SECRET_KEYS or "secret" in ks.lower() or "token" in ks.lower():
                out[ks]="<redacted>"
            else: out[ks]=_clean(v,depth+1)
        return out
    if isinstance(value,(list,tuple)): return [_clean(v,depth+1) for v in value[:2000]]
    if isinstance(value,(str,int,float,bool)) or value is None: return value
    return str(value)

def _public_payload(kind,payload):
    p=payload or {}; kind=str(kind)
    if kind=="depth":
        return {k:p.get(k) for k in ("e","E","T","s","U","u","pu","b","a") if k in p}
    if kind=="depth_snapshot":
        return {k:p.get(k) for k in ("lastUpdateId","bids","asks","fetched_at","source","history") if k in p}
    if kind=="bookTicker":
        return {k:p.get(k) for k in ("e","E","T","s","u","b","B","a","A") if k in p}
    if kind=="flow_1s":
        return {k:p.get(k) for k in ("sec","buy_notional","sell_notional","trades") if k in p}
    return _clean(p)

def record_event(symbol,kind,payload,exchange_ms=0,recv_ts=None):
    s=str(symbol or "").upper(); kind=str(kind); now=float(recv_ts or time.time())
    if not s: return
    # bookTicker is redundant with L2 top-of-book. Keep only 2 Hz for forensics;
    # depth events are never sampled because every sequence update is required.
    key=(s,kind)
    if kind=="bookTicker" and now-float(_last_event_record.get(key,0))<.5:
        return
    _last_event_record[key]=now
    _buffers[s].append({"recv_ts":now,"exchange_ms":int(exchange_ms or 0),"kind":kind,"payload":_public_payload(kind,payload)})

def prune(max_idle_sec=900):
    now=time.time(); removed=0
    for symbol,rows in list(_buffers.items()):
        if not rows or now-float(rows[-1].get("recv_ts",0) or 0)>float(max_idle_sec):
            _buffers.pop(symbol,None); removed+=1
    for key,ts in list(_last_event_record.items()):
        if now-float(ts or 0)>float(max_idle_sec): _last_event_record.pop(key,None)
    return removed

def recent(symbol,window_sec=120,now=None):
    s=str(symbol or "").upper(); now=float(now or time.time())
    return [row for row in list(_buffers.get(s,())) if now-float(row.get("recv_ts",0))<=float(window_sec)]

def _connect():
    directory=os.path.dirname(_DB_PATH)
    if directory: os.makedirs(directory,exist_ok=True)
    c=sqlite3.connect(_DB_PATH,timeout=10); c.execute("PRAGMA busy_timeout=10000"); return c

@contextmanager
def _db():
    c=_connect()
    try: yield c; c.commit()
    except Exception: c.rollback(); raise
    finally: c.close()

def init():
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS v11110_tape_bundle(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, arm_id INTEGER, state TEXT, gate_version TEXT NOT NULL,
            captured_at REAL NOT NULL, event_count INTEGER NOT NULL,
            payload_gz BLOB NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_v11110_tape_symbol_time ON v11110_tape_bundle(symbol,captured_at DESC)")



def _gate_version(gate):
    gate=gate or {}
    return str(gate.get("version") or gate.get("gate_version") or "11.11.0-futures-l2")[:80]

def _decision_anchor(l2,now):
    """Create an exact-at-decision fallback anchor if detailed depth tape is too large."""
    l2=l2 or {}
    try:
        uid=int(l2.get("lastUpdateId",0) or 0)
        bids=list(l2.get("bids") or [])[:200]
        asks=list(l2.get("asks") or [])[:200]
    except Exception:
        return None
    if uid<=0 or not bids or not asks:
        return None
    return {"recv_ts":float(now),"exchange_ms":0,"kind":"depth_snapshot","payload":{
        "lastUpdateId":uid,"bids":_clean(bids),"asks":_clean(asks),
        "fetched_at":float(now),"source":"decision_anchor"
    }}

def _encode_bundle(bundle):
    return json.dumps(bundle,ensure_ascii=False,separators=(",",":"),default=str).encode("utf-8")

def _compact_bundle_events(bundle,l2,now):
    """Bound bundle size without silently creating an unreplayable broken sequence."""
    raw=_encode_bundle(bundle)
    if len(raw)<=_MAX_BUNDLE_RAW_BYTES:
        bundle["sequence_replay_complete"]=True
        return _encode_bundle(bundle)
    events=list(bundle.get("events") or [])
    snapshots=[i for i,e in enumerate(events) if e.get("kind")=="depth_snapshot"]
    if snapshots:
        # The most recent synced anchor plus every later diff is a complete local-book replay.
        bundle["events"]=events[snapshots[-1]:]
        raw=_encode_bundle(bundle)
        if len(raw)<=_MAX_BUNDLE_RAW_BYTES:
            bundle["sequence_replay_complete"]=True
            return _encode_bundle(bundle)
    # If even one complete anchor->decision sequence is too large, never trim it
    # into a misleading partial sequence. Persist a current decision anchor and
    # mark that only decision-state replay (not full sequence replay) is exact.
    anchor=_decision_anchor(l2,now)
    non_depth=[e for e in events if e.get("kind") in {"flow_1s","bookTicker"}][-120:]
    bundle["events"]=(([anchor] if anchor else [])+non_depth)
    bundle["sequence_replay_complete"]=False
    bundle["sequence_replay_note"]="detailed depth sequence exceeded bundle limit; exact decision anchor retained"
    return _encode_bundle(bundle)

def _frame_tail(df,limit=40):
    try:
        cols=[c for c in ("open_time","close_time","open","high","low","close","volume","taker_buy_base") if c in df.columns]
        rows=df[cols].tail(limit).copy()
        for c in ("open_time","close_time"):
            if c in rows.columns: rows[c]=rows[c].astype(str)
        return rows.to_dict("records")
    except Exception: return []

def capture_decision(row,assessment,frame1=None,frame3=None,flow=None,quote=None,l2=None,l2_stability=None,gate=None,force=False):
    """Persist a replay bundle, throttled per arm/state to control SQLite growth."""
    init(); now=time.time(); symbol=str(row.get("symbol") or "").upper(); arm_id=int(row.get("id") or 0)
    state=str(getattr(assessment,"state","") or "")
    key=(symbol,arm_id,state)
    min_gap=5 if state in ("READY","ENTER_NOW") else 120
    if not force and now-float(_last_capture.get(key,0))<min_gap: return None
    _last_capture[key]=now
    if len(_last_capture)>5000:
        cutoff=now-86400
        for old_key,ts in list(_last_capture.items()):
            if float(ts or 0)<cutoff: _last_capture.pop(old_key,None)
    events=recent(symbol,120,now)[-1500:]
    gate_version=_gate_version(gate)
    bundle={
        "schema":"v11.12.0-tape-2","gate_version":gate_version,
        "captured_at":now,"symbol":symbol,"arm":_clean(dict(row)),
        "assessment":_clean(getattr(assessment,"__dict__",{})),
        "frame1":_frame_tail(frame1),"frame3":_frame_tail(frame3),
        "flow":_clean(flow),"quote":_clean(quote),"l2":_clean(l2),"l2_stability":_clean(l2_stability),
        "gate":_clean(gate),"events":events,
    }
    raw=_compact_bundle_events(bundle,l2,now)
    blob=gzip.compress(raw,compresslevel=6)
    with _db() as c:
        cur=c.execute("INSERT INTO v11110_tape_bundle(symbol,arm_id,state,gate_version,captured_at,event_count,payload_gz) VALUES(?,?,?,?,?,?,?)",
                      (symbol,arm_id,state,gate_version,now,len(bundle["events"]),sqlite3.Binary(blob)))
        # Keep forensic diversity: one noisy symbol must not evict the whole
        # replay history for every other market.
        c.execute("""DELETE FROM v11110_tape_bundle WHERE symbol=? AND id NOT IN (
            SELECT id FROM v11110_tape_bundle WHERE symbol=? ORDER BY id DESC LIMIT ?
        )""",(symbol,symbol,_PER_SYMBOL_RETENTION))
        c.execute("DELETE FROM v11110_tape_bundle WHERE id NOT IN (SELECT id FROM v11110_tape_bundle ORDER BY id DESC LIMIT ?)",(_RETENTION,))
        return int(cur.lastrowid)

def load_bundle(bundle_id):
    init()
    with _db() as c: row=c.execute("SELECT payload_gz FROM v11110_tape_bundle WHERE id=?",(int(bundle_id),)).fetchone()
    if not row: return None
    return json.loads(gzip.decompress(row[0]).decode("utf-8"))

def status():
    init()
    with _db() as c:
        count=int(c.execute("SELECT COUNT(*) FROM v11110_tape_bundle").fetchone()[0])
        last=c.execute("SELECT MAX(captured_at) FROM v11110_tape_bundle").fetchone()[0]
    return {"buffer_symbols":len(_buffers),"buffer_events":sum(len(v) for v in _buffers.values()),"bundles":count,"last_bundle_age_sec":None if not last else max(0.0,time.time()-float(last))}
