"""Delivery-aware tracker adapter for V11.7.1.

For delivered production signals:
- the effective CREATED time is fixed once at max(original created_at, delivered_at);
- the effective LAST_CHECKED time may move forward normally.

This distinction is critical: app.tracker calculates ENTRY_EXPIRY from created_at.
If created_at were replaced by last_checked_at on every pass, the entry deadline
would slide forward forever.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.config import DATABASE_PATH
from v1171_sqlite import db_session


def _epoch(value):
    if not value:
        return None
    text=str(value).replace("Z","+00:00")
    try:
        dt=datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _later(a,b):
    ea=_epoch(a); eb=_epoch(b)
    if ea is None:
        return b
    if eb is None:
        return a
    return a if ea>=eb else b


def effective_created_at(created_at,delivered_at,is_shadow=False):
    if is_shadow or not delivered_at:
        return created_at
    return _later(created_at,delivered_at)


def effective_last_checked_at(created_at,delivered_at,last_checked_at,is_shadow=False):
    if is_shadow:
        return last_checked_at or created_at
    base=effective_created_at(created_at,delivered_at,False)
    return _later(last_checked_at,base) if last_checked_at else base


def effective_tracking_start(created_at,delivered_at,last_checked_at,is_shadow=False):
    """Backward-compatible helper: this is the next history-load boundary."""
    return effective_last_checked_at(
        created_at,delivered_at,last_checked_at,is_shadow
    )


def delivery_aware_open_signals(n=500):
    with db_session(timeout=10) as c:
        c.row_factory=sqlite3.Row
        rows=[dict(r) for r in c.execute("""
            SELECT id,created_at,activated_at,last_checked_at,status,symbol,timeframe,side,
                   entry,stop,tp1,tp2,tp3,max_favorable_r,max_adverse_r,source_chat_id,
                   is_shadow,shadow_reason,delivery_state,delivered_at
            FROM signals
            WHERE status IN ('SENT','WAITING','ACTIVE','OPEN')
            ORDER BY COALESCE(is_shadow,0),id LIMIT ?
        """,(int(n),))]

    for row in rows:
        is_shadow=bool(int(row.get("is_shadow") or 0))
        if is_shadow:
            continue

        original_created=row.get("created_at")
        delivered=row.get("delivered_at")
        original_checked=row.get("last_checked_at")

        # FIX: created_at is anchored to delivery only once. It is NEVER
        # replaced by a later tracker checkpoint.
        row["created_at"]=effective_created_at(
            original_created,delivered,False
        )
        row["last_checked_at"]=effective_last_checked_at(
            original_created,delivered,original_checked,False
        )
    return rows
