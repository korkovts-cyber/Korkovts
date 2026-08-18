"""Small closing SQLite context helper for V11.7.1.

`sqlite3.Connection` commits/rolls back in its native context manager but does
not close on `__exit__`. Long-running scheduled jobs therefore should use this
helper so every short-lived connection is deterministically closed.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import DATABASE_PATH


@contextmanager
def db_session(path=None, timeout=10, row_factory=None):
    conn=sqlite3.connect(str(path or DATABASE_PATH), timeout=timeout)
    conn.execute("PRAGMA busy_timeout=10000")
    if row_factory is not None:
        conn.row_factory=row_factory
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
