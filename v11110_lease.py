"""SQLite singleton lease for Railway deploy/restart overlap protection."""
from __future__ import annotations
import os, socket, sqlite3, time, uuid
try:
    from app.config import DATABASE_PATH as DB_PATH
except Exception:
    DB_PATH=os.getenv("DATABASE_PATH","data/signals.db")

OWNER=f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"
LEASE_NAME="korkovts-production-bot"
TTL_SEC=max(45,min(180,int(os.getenv("V11110_LEASE_TTL_SEC","90"))))

def _connect():
    d=os.path.dirname(DB_PATH)
    if d: os.makedirs(d,exist_ok=True)
    c=sqlite3.connect(DB_PATH,timeout=10,isolation_level=None); c.execute("PRAGMA busy_timeout=10000"); return c

def init():
    c=_connect()
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS v11110_process_lease(
            name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL, heartbeat_at REAL NOT NULL
        )""")
    finally: c.close()

def acquire(now=None):
    init(); now=float(now or time.time()); c=_connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT owner,expires_at FROM v11110_process_lease WHERE name=?",(LEASE_NAME,)).fetchone()
        if row and str(row[0])!=OWNER and float(row[1])>now:
            c.execute("ROLLBACK"); return False
        c.execute("INSERT INTO v11110_process_lease(name,owner,expires_at,heartbeat_at) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at,heartbeat_at=excluded.heartbeat_at",
                  (LEASE_NAME,OWNER,now+TTL_SEC,now))
        c.execute("COMMIT"); return True
    except Exception:
        try: c.execute("ROLLBACK")
        except Exception: pass
        raise
    finally: c.close()


def acquire_with_wait(timeout_sec=60.0,poll_sec=2.0):
    """Wait briefly for a previous Railway process to release; never steal a live lease."""
    timeout=max(0.0,min(float(timeout_sec),120.0)); poll=max(.1,min(float(poll_sec),5.0))
    deadline=time.monotonic()+timeout
    while True:
        if acquire(): return True
        remaining=deadline-time.monotonic()
        if remaining<=0: return False
        time.sleep(min(poll,remaining))

def heartbeat(now=None):
    now=float(now or time.time()); c=_connect()
    try:
        cur=c.execute("UPDATE v11110_process_lease SET expires_at=?,heartbeat_at=? WHERE name=? AND owner=?",(now+TTL_SEC,now,LEASE_NAME,OWNER))
        return cur.rowcount==1
    finally: c.close()

def release():
    c=_connect()
    try: return c.execute("DELETE FROM v11110_process_lease WHERE name=? AND owner=?",(LEASE_NAME,OWNER)).rowcount==1
    finally: c.close()

def status():
    init(); c=_connect()
    try: row=c.execute("SELECT owner,expires_at,heartbeat_at FROM v11110_process_lease WHERE name=?",(LEASE_NAME,)).fetchone()
    finally: c.close()
    if not row: return {"held":False,"owner":None}
    return {"held":float(row[1])>time.time(),"owner":str(row[0]),"ours":str(row[0])==OWNER,"expires_in_sec":float(row[1])-time.time(),"heartbeat_age_sec":time.time()-float(row[2])}
