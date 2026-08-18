"""Independent SQLite journal for V11.7.1 Spot signals."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone

from app.config import DATABASE_PATH

SPOT_RELEASE_VERSION="11.8.1-market-intelligence"

@contextmanager
def _db():
    c=sqlite3.connect(DATABASE_PATH,timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    c.row_factory=sqlite3.Row
    try:
        yield c; c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def init():
    with _db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS spot_signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                symbol TEXT NOT NULL,
                base_asset TEXT NOT NULL,
                signal_status TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'OPEN',
                score REAL NOT NULL,
                setup_type TEXT,
                entry_low REAL NOT NULL,
                entry_high REAL NOT NULL,
                entry_price REAL NOT NULL,
                invalidation REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                tp3 REAL NOT NULL,
                feature_json TEXT,
                market_regime TEXT,
                relative_percentile REAL,
                excess_btc_14d REAL,
                max_favorable_pct REAL NOT NULL DEFAULT 0,
                max_adverse_pct REAL NOT NULL DEFAULT 0,
                partial_hour_processed INTEGER NOT NULL DEFAULT 0,
                return_3d REAL,
                return_5d REAL,
                return_7d REAL,
                return_10d REAL,
                tp1_hit INTEGER NOT NULL DEFAULT 0,
                tp2_hit INTEGER NOT NULL DEFAULT 0,
                tp3_hit INTEGER NOT NULL DEFAULT 0,
                invalidated INTEGER NOT NULL DEFAULT 0,
                first_tp1_at TEXT,
                first_invalidation_at TEXT,
                delivery_uncertain INTEGER NOT NULL DEFAULT 0,
                closed_at TEXT,
                result TEXT,
                release_version TEXT NOT NULL DEFAULT '11.8.1-market-intelligence'
            )
        """)
        signal_cols={r[1] for r in c.execute("PRAGMA table_info(spot_signals)")}
        if "partial_hour_processed" not in signal_cols:
            c.execute(
                "ALTER TABLE spot_signals ADD COLUMN partial_hour_processed INTEGER NOT NULL DEFAULT 0"
            )
        if "first_tp1_at" not in signal_cols:
            c.execute("ALTER TABLE spot_signals ADD COLUMN first_tp1_at TEXT")
        if "first_invalidation_at" not in signal_cols:
            c.execute("ALTER TABLE spot_signals ADD COLUMN first_invalidation_at TEXT")
        if "delivery_uncertain" not in signal_cols:
            c.execute(
                "ALTER TABLE spot_signals ADD COLUMN delivery_uncertain INTEGER NOT NULL DEFAULT 0"
            )
        c.execute("CREATE INDEX IF NOT EXISTS idx_spot_open ON spot_signals(state,created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_spot_recent ON spot_signals(symbol,signal_status,created_at)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS spot_deliveries(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spot_signal_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                expired_at TEXT,
                sending_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                UNIQUE(spot_signal_id,chat_id)
            )
        """)
        cols={r[1] for r in c.execute("PRAGMA table_info(spot_deliveries)")}
        if "expired_at" not in cols:
            c.execute("ALTER TABLE spot_deliveries ADD COLUMN expired_at TEXT")
        if "sending_at" not in cols:
            c.execute("ALTER TABLE spot_deliveries ADD COLUMN sending_at TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_spot_delivery_pending ON spot_deliveries(delivered_at,created_at)")


def was_sent_recently(symbol,hours=72):
    init()
    with _db() as c:
        row=c.execute("""
            SELECT 1 FROM spot_signals s
            WHERE s.symbol=? AND s.signal_status='BUY'
              AND julianday(COALESCE(s.delivered_at,s.created_at))>=julianday('now',?)
              AND (
                    s.delivered_at IS NOT NULL
                    OR EXISTS(
                        SELECT 1 FROM spot_deliveries d
                        WHERE d.spot_signal_id=s.id
                          AND d.delivered_at IS NULL
                          AND d.expired_at IS NULL
                    )
              )
            LIMIT 1
        """,(str(symbol).upper(),f"-{int(hours)} hours")).fetchone()
    return bool(row)


def save(signal,delivered=False):
    init(); now=datetime.now(timezone.utc).isoformat()
    entry=float(signal.micro.get("ask") or signal.micro.get("mid") or (signal.entry_low+signal.entry_high)/2)
    with _db() as c:
        cur=c.execute("""
            INSERT INTO spot_signals(
                created_at,delivered_at,symbol,base_asset,signal_status,state,score,setup_type,
                entry_low,entry_high,entry_price,invalidation,tp1,tp2,tp3,feature_json,
                market_regime,relative_percentile,excess_btc_14d,release_version
            ) VALUES(?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(
            now,now if delivered else None,str(signal.symbol).upper(),str(signal.base_asset).upper(),
            str(signal.status),float(signal.score),str(signal.setup_type),float(signal.entry_low),
            float(signal.entry_high),entry,float(signal.invalidation),float(signal.tp1),float(signal.tp2),
            float(signal.tp3),json.dumps(signal.feature_snapshot,ensure_ascii=False,sort_keys=True,default=str),
            str(signal.market_regime),float(signal.relative_percentile),float(signal.excess_btc_14d),
            SPOT_RELEASE_VERSION,
        ))
        return int(cur.lastrowid)


def open_rows(limit=50):
    init()
    with _db() as c:
        rows=c.execute("""
            SELECT * FROM spot_signals
            WHERE delivered_at IS NOT NULL
              AND (state='OPEN' OR return_10d IS NULL)
              AND julianday(delivered_at)>=julianday('now','-45 days')
            ORDER BY delivered_at ASC LIMIT ?
        """,(int(limit),)).fetchall()
    return [dict(r) for r in rows]


def update_metrics(signal_id,**values):
    if not values:
        return
    allowed={
        "max_favorable_pct","max_adverse_pct","return_3d","return_5d","return_7d","return_10d",
        "tp1_hit","tp2_hit","tp3_hit","invalidated","partial_hour_processed",
        "first_tp1_at","first_invalidation_at","state","closed_at","result",
    }
    data={k:v for k,v in values.items() if k in allowed}
    if not data:
        return
    with _db() as c:
        sql=", ".join(f"{k}=?" for k in data)
        c.execute(f"UPDATE spot_signals SET {sql} WHERE id=?",(*data.values(),int(signal_id)))


def recent(limit=15):
    init()
    with _db() as c:
        rows=c.execute(
            "SELECT * FROM spot_signals WHERE delivered_at IS NOT NULL ORDER BY id DESC LIMIT ?",
            (int(limit),)
        ).fetchall()
    return [dict(r) for r in rows]


def stats():
    init()
    with _db() as c:
        row=c.execute("""
            SELECT COUNT(*) issued,
                   SUM(CASE WHEN state='CLOSED' THEN 1 ELSE 0 END) resolved,
                   SUM(CASE WHEN invalidated=1 THEN 1 ELSE 0 END) invalidated,
                   SUM(CASE WHEN COALESCE(result,'') LIKE 'AMBIGUOUS%' THEN 1 ELSE 0 END) ambiguous,
                   AVG(return_7d) avg_7d,
                   AVG(return_10d) avg_10d,
                   AVG(max_favorable_pct) avg_mfe,
                   AVG(max_adverse_pct) avg_mae,
                   SUM(CASE WHEN return_7d>0 THEN 1 ELSE 0 END) win7,
                   SUM(CASE WHEN return_7d IS NOT NULL THEN 1 ELSE 0 END) n7
            FROM spot_signals
            WHERE signal_status='BUY' AND delivered_at IS NOT NULL
              AND COALESCE(delivery_uncertain,0)=0
        """).fetchone()
    d=dict(row or {})
    n7=int(d.get("n7") or 0); win7=int(d.get("win7") or 0)
    d["win_rate_7d"]=(win7/n7 if n7 else None)
    return d


def enqueue_delivery(spot_signal_id,chat_id,payload):
    init(); now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute("""
            INSERT OR IGNORE INTO spot_deliveries(
                spot_signal_id,chat_id,payload,created_at
            ) VALUES(?,?,?,?)
        """,(int(spot_signal_id),int(chat_id),str(payload),now))


def expire_pending_deliveries(max_age_minutes=45):
    init(); now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        cur=c.execute("""
            UPDATE spot_deliveries
            SET expired_at=?,last_error=COALESCE(last_error,'delivery expired before fresh entry')
            WHERE delivered_at IS NULL AND expired_at IS NULL
              AND julianday(created_at)<julianday('now',?)
        """,(now,f"-{int(max_age_minutes)} minutes"))
        c.execute("""
            UPDATE spot_signals
            SET state='CLOSED',closed_at=COALESCE(closed_at,?),
                result=COALESCE(result,'DELIVERY_EXPIRED')
            WHERE delivered_at IS NULL
              AND EXISTS(SELECT 1 FROM spot_deliveries d WHERE d.spot_signal_id=spot_signals.id)
              AND NOT EXISTS(
                  SELECT 1 FROM spot_deliveries d
                  WHERE d.spot_signal_id=spot_signals.id
                    AND d.delivered_at IS NULL AND d.expired_at IS NULL
              )
        """,(now,))
        return int(cur.rowcount or 0)


def pending_deliveries(limit=100):
    init()
    with _db() as c:
        rows=c.execute("""
            SELECT id,spot_signal_id,chat_id,payload,attempts
            FROM spot_deliveries
            WHERE delivered_at IS NULL AND expired_at IS NULL
            ORDER BY id ASC LIMIT ?
        """,(int(limit),)).fetchall()
    return [tuple(r) for r in rows]


def delivery_context(spot_signal_id):
    init()
    with _db() as c:
        row=c.execute("""
            SELECT id,symbol,base_asset,entry_low,entry_high,invalidation,
                   delivered_at,state,release_version
            FROM spot_signals WHERE id=?
        """,(int(spot_signal_id),)).fetchone()
    return dict(row) if row else None


def expire_delivery(delivery_id,spot_signal_id,reason):
    now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute("""
            UPDATE spot_deliveries
            SET expired_at=COALESCE(expired_at,?),last_error=?
            WHERE id=? AND delivered_at IS NULL
        """,(now,str(reason)[:500],int(delivery_id)))
        # Only classify the logical signal as undelivered if nobody received it.
        delivered=c.execute(
            "SELECT 1 FROM spot_deliveries WHERE spot_signal_id=? AND delivered_at IS NOT NULL LIMIT 1",
            (int(spot_signal_id),)
        ).fetchone()
        pending=c.execute(
            "SELECT 1 FROM spot_deliveries WHERE spot_signal_id=? AND delivered_at IS NULL AND expired_at IS NULL LIMIT 1",
            (int(spot_signal_id),)
        ).fetchone()
        if not delivered and not pending:
            c.execute("""
                UPDATE spot_signals SET state='CLOSED',closed_at=COALESCE(closed_at,?),
                    result=COALESCE(result,'DELIVERY_EXPIRED')
                WHERE id=? AND delivered_at IS NULL
            """,(now,int(spot_signal_id)))




def claim_delivery(delivery_id):
    """Atomically mark one Spot recipient SENDING just before Telegram."""
    init(); now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        cur=c.execute("""
            UPDATE spot_deliveries SET sending_at=?
            WHERE id=? AND delivered_at IS NULL AND expired_at IS NULL
              AND sending_at IS NULL
        """,(now,int(delivery_id)))
    return int(cur.rowcount or 0)==1


def expire_stuck_sending(max_age_sec=300):
    """Suppress ambiguous sends after a process interruption; never blind-retry."""
    init()
    with _db() as c:
        rows=c.execute("""
            SELECT id,spot_signal_id FROM spot_deliveries
            WHERE delivered_at IS NULL AND expired_at IS NULL
              AND sending_at IS NOT NULL
              AND julianday(sending_at)<=julianday('now',?)
        """,(f"-{max(0,int(max_age_sec))} seconds",)).fetchall()
    for row in rows:
        expire_delivery(
            int(row["id"]),int(row["spot_signal_id"]),
            "Telegram delivery outcome unknown after process interruption; retry suppressed"
        )
    return len(rows)


def mark_delivery_uncertain(delivery_id,spot_signal_id,entry_price,reason):
    """Telegram was invoked but its outcome is unknown.

    Never retry the BUY instruction blindly. Treat the trade as potentially
    received, track it, reserve portfolio risk, and exclude it from calibration.
    """
    now=datetime.now(timezone.utc).isoformat()
    fresh_entry=float(entry_price) if entry_price is not None else None
    with _db() as c:
        c.execute("""
            UPDATE spot_deliveries
            SET expired_at=COALESCE(expired_at,?),last_error=?,
                sending_at=COALESCE(sending_at,?)
            WHERE id=? AND delivered_at IS NULL
        """,(now,str(reason)[:500],now,int(delivery_id)))
        c.execute("""
            UPDATE spot_deliveries
            SET expired_at=COALESCE(expired_at,?),
                last_error=COALESCE(last_error,'suppressed because another recipient delivery is uncertain')
            WHERE spot_signal_id=? AND delivered_at IS NULL
        """,(now,int(spot_signal_id)))
        c.execute("""
            UPDATE spot_signals
            SET entry_price=CASE
                    WHEN delivered_at IS NULL AND ? IS NOT NULL THEN ?
                    ELSE entry_price
                END,
                delivered_at=COALESCE(delivered_at,?),
                delivery_uncertain=1
            WHERE id=?
        """,(fresh_entry,fresh_entry,now,int(spot_signal_id)))
    return now


def mark_delivery_sent(delivery_id,spot_signal_id,entry_price=None):
    """Mark one recipient delivered and anchor the logical signal to the first real send.

    Auto BUY can sit in the durable outbox for several minutes. Its research
    entry must therefore be the executable ask at first delivery, not the stale
    ask captured when the broad scan originally queued it.
    """
    now=datetime.now(timezone.utc).isoformat()
    fresh_entry=float(entry_price) if entry_price is not None else None
    with _db() as c:
        c.execute(
            "UPDATE spot_deliveries SET delivered_at=?,last_error=NULL WHERE id=?",
            (now,int(delivery_id))
        )
        c.execute("""
            UPDATE spot_signals
            SET entry_price=CASE
                    WHEN delivered_at IS NULL AND ? IS NOT NULL THEN ?
                    ELSE entry_price
                END,
                delivered_at=COALESCE(delivered_at,?)
            WHERE id=?
        """,(fresh_entry,fresh_entry,now,int(spot_signal_id)))


def mark_delivery_failed(delivery_id,error):
    with _db() as c:
        c.execute("""
            UPDATE spot_deliveries
            SET attempts=attempts+1,last_error=?
            WHERE id=?
        """,(str(error)[:500],int(delivery_id)))


def active_signals(limit=10):
    init()
    with _db() as c:
        rows=c.execute("""
            SELECT id,delivered_at,symbol,base_asset,state,score,setup_type,
                   entry_price,entry_low,entry_high,invalidation,tp1,tp2,tp3,
                   market_regime,relative_percentile,max_favorable_pct,max_adverse_pct,
                   tp1_hit,tp2_hit,tp3_hit,invalidated,result
            FROM spot_signals
            WHERE delivered_at IS NOT NULL AND signal_status='BUY' AND state='OPEN'
            ORDER BY id DESC LIMIT ?
        """,(int(limit),)).fetchall()
    return [dict(r) for r in rows]


def portfolio_reserved_signals(limit=20,exclude_id=None):
    """Delivered OPEN BUYs plus current-release undelivered BUYs still in the outbox."""
    init()
    exclude=int(exclude_id) if exclude_id is not None else -1
    with _db() as c:
        rows=c.execute("""
            SELECT s.id,s.symbol,s.feature_json,s.delivered_at,s.release_version
            FROM spot_signals s
            WHERE s.id<>?
              AND s.signal_status='BUY' AND s.state='OPEN'
              AND (
                    s.delivered_at IS NOT NULL
                    OR (
                        s.delivered_at IS NULL
                        AND s.release_version=?
                        AND EXISTS(
                            SELECT 1 FROM spot_deliveries d
                            WHERE d.spot_signal_id=s.id
                              AND d.delivered_at IS NULL
                              AND d.expired_at IS NULL
                        )
                    )
              )
            ORDER BY s.id DESC LIMIT ?
        """,(exclude,SPOT_RELEASE_VERSION,int(limit))).fetchall()
    return [dict(r) for r in rows]


def active_portfolio_clusters():
    """Cluster keys reserved by delivered OPEN or current pending Spot BUYs."""
    clusters=set()
    for row in portfolio_reserved_signals(50):
        symbol=str(row.get("symbol") or "").upper()
        try:
            feat=json.loads(row.get("feature_json") or "{}")
            cluster=str((feat.get("portfolio") or {}).get("cluster_key") or symbol).upper()
        except Exception:
            cluster=symbol
        if cluster:
            clusters.add(cluster)
    return clusters


def portfolio_reserved_count():
    return len(portfolio_reserved_signals(100))


def active_open_count():
    """Number of currently OPEN delivered Spot BUYs."""
    init()
    with _db() as c:
        row=c.execute("""
            SELECT COUNT(*) FROM spot_signals
            WHERE delivered_at IS NOT NULL AND signal_status='BUY' AND state='OPEN'
        """).fetchone()
    return int(row[0] or 0)
