"""V11.7.1 durable Futures delivery hardening.

ENTRY NOW is time-sensitive. A Telegram outage must not convert a valid entry
from minutes ago into a current instruction. This overlay adds per-recipient
expiry without modifying the legacy app package.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import DATABASE_PATH

FUTURES_DELIVERY_TTL_MIN=3


@contextmanager
def _db():
    c=sqlite3.connect(DATABASE_PATH,timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    c.row_factory=sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with _db() as c:
        cols={r[1] for r in c.execute("PRAGMA table_info(signal_deliveries)")}
        if "expired_at" not in cols:
            c.execute("ALTER TABLE signal_deliveries ADD COLUMN expired_at TEXT")
        if "sending_at" not in cols:
            c.execute("ALTER TABLE signal_deliveries ADD COLUMN sending_at TEXT")
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_delivery_v1171_pending
            ON signal_deliveries(delivered_at,expired_at,created_at)
        """)


def pending(limit=100):
    """All pending recipients plus their current AUTO-subscription state.

    Disabled recipients are intentionally returned so the runtime can expire
    them; otherwise a PENDING signal could occupy the risk slot forever.
    """
    init()
    with _db() as c:
        rows=c.execute("""
            SELECT d.id,d.signal_id,d.chat_id,d.payload,d.attempts,sig.symbol,
                   COALESCE(sub.enabled,0) AS subscriber_enabled
            FROM signal_deliveries d
            JOIN signals sig ON sig.id=d.signal_id
            LEFT JOIN subscribers sub ON sub.chat_id=d.chat_id
            WHERE d.delivered_at IS NULL
              AND d.expired_at IS NULL
              AND d.sending_at IS NULL
              AND COALESCE(sig.delivery_state,'DELIVERED') IN ('PENDING','DELIVERED')
            ORDER BY d.id
            LIMIT ?
        """,(int(limit),)).fetchall()
    return [tuple(r) for r in rows]


def context(signal_id):
    init()
    with _db() as c:
        row=c.execute("""
            SELECT id,created_at,delivered_at,symbol,timeframe,side,entry,stop,tp1,tp2,tp3,
                   status,delivery_state,feature_json,release_version
            FROM signals WHERE id=?
        """,(int(signal_id),)).fetchone()
    if not row:
        return None
    out=dict(row)
    try:
        out["features"]=json.loads(out.get("feature_json") or "{}")
    except Exception:
        out["features"]={}
    return out


def delivery_id(signal_id,chat_id):
    init()
    with _db() as c:
        row=c.execute("""
            SELECT id FROM signal_deliveries
            WHERE signal_id=? AND chat_id=?
            ORDER BY id DESC LIMIT 1
        """,(int(signal_id),int(chat_id))).fetchone()
    return int(row[0]) if row else None


def age_seconds(signal_id,now=None):
    row=context(signal_id)
    if not row:
        return None
    raw=row.get("created_at")
    if not raw:
        return None
    dt=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=timezone.utc)
    now=now or datetime.now(timezone.utc)
    return max(0.0,(now-dt).total_seconds())


def expire(delivery_id_,signal_id,reason):
    """Suppress one recipient without falsifying a successful recipient.

    If nobody received the logical signal and no recipient remains pending, the
    signal itself becomes DELIVERY_FAILED.
    """
    init(); now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute("""
            UPDATE signal_deliveries
            SET expired_at=COALESCE(expired_at,?),last_error=?
            WHERE id=? AND delivered_at IS NULL
        """,(now,str(reason)[:500],int(delivery_id_)))
        delivered=c.execute("""
            SELECT 1 FROM signal_deliveries
            WHERE signal_id=? AND delivered_at IS NOT NULL LIMIT 1
        """,(int(signal_id),)).fetchone()
        pending_row=c.execute("""
            SELECT 1 FROM signal_deliveries
            WHERE signal_id=? AND delivered_at IS NULL AND expired_at IS NULL LIMIT 1
        """,(int(signal_id),)).fetchone()
        if not delivered and not pending_row:
            c.execute("""
                UPDATE signals
                SET status='DELIVERY_FAILED',delivery_state='FAILED'
                WHERE id=? AND COALESCE(delivery_state,'PENDING')='PENDING'
            """,(int(signal_id),))




def mark_uncertain(delivery_id_,signal_id,chat_id,reason):
    """Telegram was invoked but the HTTP outcome is unknowable.

    Fail-safe policy:
    - never resend the trading instruction;
    - reserve/track the logical trade as ACTIVE from the send-attempt time;
    - mark delivery_state UNCERTAIN so it is excluded from learning/calibration;
    - suppress every remaining recipient for this signal.
    """
    init(); now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute("""
            UPDATE signal_deliveries
            SET expired_at=COALESCE(expired_at,?),last_error=?,
                sending_at=COALESCE(sending_at,?)
            WHERE id=? AND delivered_at IS NULL
        """,(now,str(reason)[:500],now,int(delivery_id_)))
        c.execute("""
            UPDATE signal_deliveries
            SET expired_at=COALESCE(expired_at,?),
                last_error=COALESCE(last_error,'suppressed because another recipient delivery is uncertain')
            WHERE signal_id=? AND delivered_at IS NULL
        """,(now,int(signal_id)))
        c.execute("""
            UPDATE signals
            SET status='ACTIVE',
                delivery_state='UNCERTAIN',
                delivered_at=COALESCE(delivered_at,?),
                activated_at=COALESCE(activated_at,?),
                last_checked_at=COALESCE(last_checked_at,?),
                source_chat_id=COALESCE(source_chat_id,?)
            WHERE id=?
              AND COALESCE(delivery_state,'PENDING') IN ('PENDING','UNCERTAIN')
        """,(now,now,now,int(chat_id),int(signal_id)))
    return now


def expire_all_for_signal(signal_id,reason):
    init()
    with _db() as c:
        ids=[int(r[0]) for r in c.execute("""
            SELECT id FROM signal_deliveries
            WHERE signal_id=? AND delivered_at IS NULL AND expired_at IS NULL
        """,(int(signal_id),)).fetchall()]
    for did in ids:
        expire(did,signal_id,reason)
    return len(ids)






def claim(delivery_id_):
    """Atomically claim one recipient immediately before Telegram send."""
    init(); now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        cur=c.execute("""
            UPDATE signal_deliveries
            SET sending_at=?
            WHERE id=? AND delivered_at IS NULL AND expired_at IS NULL
              AND sending_at IS NULL
        """,(now,int(delivery_id_)))
    return int(cur.rowcount or 0)==1


def expire_stuck_sending(max_age_sec=300):
    """Never blindly retry a send whose outcome became unknown after a crash."""
    init()
    with _db() as c:
        rows=c.execute("""
            SELECT id,signal_id FROM signal_deliveries
            WHERE delivered_at IS NULL AND expired_at IS NULL
              AND sending_at IS NOT NULL
              AND julianday(sending_at)<=julianday('now',?)
        """,(f"-{max(0,int(max_age_sec))} seconds",)).fetchall()
    for row in rows:
        expire(
            int(row["id"]),int(row["signal_id"]),
            "Telegram delivery outcome unknown after process interruption; retry suppressed"
        )
    return len(rows)




def reconcile_failed_arms():
    """Release ENTRY NOW arms whose logical Telegram signal can no longer be delivered."""
    init()
    with _db() as c:
        cur=c.execute("""
            UPDATE v1142_armed
            SET status='CANCELLED',last_state='CANCEL',
                last_reason='ENTRY NOW delivery failed/expired before confirmed Telegram delivery',
                last_check_at=CAST(strftime('%s','now') AS REAL)
            WHERE status='PENDING_DELIVERY'
              AND triggered_signal_id IN (
                    SELECT id FROM signals
                    WHERE COALESCE(delivery_state,'')='FAILED'
                       OR status='DELIVERY_FAILED'
              )
        """)
    return int(cur.rowcount or 0)


def other_live_count(signal_id):
    init()
    with _db() as c:
        row=c.execute("""
            SELECT COUNT(*) FROM signals
            WHERE id<>?
              AND COALESCE(is_shadow,0)=0
              AND (
                    COALESCE(delivery_state,'DELIVERED') IN ('DELIVERED','UNCERTAIN')
                    OR (
                        COALESCE(delivery_state,'')='PENDING'
                        AND COALESCE(release_version,'') LIKE '11.7.1%'
                    )
              )
              AND status IN ('PENDING_DELIVERY','SENT','WAITING','ACTIVE','OPEN')
        """,(int(signal_id),)).fetchone()
    return int(row[0] or 0)


def stats():
    init()
    with _db() as c:
        pending_count=int(c.execute("""
            SELECT COUNT(*) FROM signal_deliveries
            WHERE delivered_at IS NULL AND expired_at IS NULL
        """).fetchone()[0] or 0)
        expired_24h=int(c.execute("""
            SELECT COUNT(*) FROM signal_deliveries
            WHERE expired_at>=datetime('now','-24 hours')
        """).fetchone()[0] or 0)
    return {"pending":pending_count,"expired_24h":expired_24h}
