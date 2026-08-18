"""SQLite production hardening for V11.4.1.

Why:
- the bot has several concurrent readers/writers (tracker, Telegram outbox,
  lifecycle, Meta/Factor labs);
- WAL improves reader/writer coexistence;
- busy_timeout reduces transient SQLITE_BUSY failures;
- SQLite online backup provides a recoverable copy without stopping the bot.

This module does not alter signal logic.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATABASE_PATH

_original_connect=sqlite3.connect
_installed=False
_lock=threading.Lock()


def _configure_connection(conn):
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    return conn


def _connect(*args,**kwargs):
    conn=_original_connect(*args,**kwargs)
    return _configure_connection(conn)


def install_connect_wrapper():
    global _installed
    if _installed:
        return
    sqlite3.connect=_connect
    _installed=True


def harden_database():
    """Enable persistent WAL and validate that the DB is writable."""
    path=Path(DATABASE_PATH)
    path.parent.mkdir(parents=True,exist_ok=True)
    with _original_connect(DATABASE_PATH,timeout=10) as c:
        c.execute("PRAGMA busy_timeout=10000")
        journal=str(c.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA wal_autocheckpoint=1000")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("""CREATE TABLE IF NOT EXISTS v114_db_health(
            id INTEGER PRIMARY KEY CHECK(id=1),
            touched_at TEXT,
            release TEXT
        )""")
        c.execute("""INSERT INTO v114_db_health(id,touched_at,release)
            VALUES(1,CURRENT_TIMESTAMP,'11.4')
            ON CONFLICT(id) DO UPDATE SET
              touched_at=CURRENT_TIMESTAMP,release='11.4'""")
        quick=c.execute("PRAGMA quick_check").fetchone()
        if journal!="wal":
            raise RuntimeError(f"SQLite WAL not enabled: {journal}")
        if not quick or str(quick[0]).lower()!="ok":
            raise RuntimeError(f"SQLite quick_check failed: {quick}")
    install_connect_wrapper()
    return {"journal_mode":"wal","quick_check":"ok","path":str(path)}


def checkpoint():
    with sqlite3.connect(DATABASE_PATH,timeout=10) as c:
        row=c.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    return row


def _backup_dir():
    configured=os.getenv("DATABASE_BACKUP_DIR","").strip()
    if configured:
        return Path(configured)
    path=Path(DATABASE_PATH)
    return path.parent/"backups"


def backup_if_due(keep=7):
    """Create at most one online backup per UTC day and retain recent copies."""
    with _lock:
        day=datetime.now(timezone.utc).strftime("%Y%m%d")
        folder=_backup_dir()
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/f"signals-{day}.db"
        if not target.exists():
            with sqlite3.connect(DATABASE_PATH,timeout=10) as src:
                with _original_connect(str(target),timeout=10) as dst:
                    src.backup(dst)
                    check=dst.execute("PRAGMA quick_check").fetchone()
                    if not check or str(check[0]).lower()!="ok":
                        raise RuntimeError(f"backup quick_check failed: {check}")
        backups=sorted(folder.glob("signals-*.db"),reverse=True)
        for old in backups[max(1,int(keep)):]:
            try:
                old.unlink()
            except OSError:
                pass
        return str(target)


def status():
    try:
        with sqlite3.connect(DATABASE_PATH,timeout=10) as c:
            journal=str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            busy=int(c.execute("PRAGMA busy_timeout").fetchone()[0])
            quick=str(c.execute("PRAGMA quick_check").fetchone()[0])
        return {"ok":quick.lower()=="ok","journal":journal,"busy_timeout_ms":busy,
                "path":DATABASE_PATH}
    except Exception as exc:
        return {"ok":False,"journal":"unknown","busy_timeout_ms":0,
                "path":DATABASE_PATH,"error":str(exc)}
