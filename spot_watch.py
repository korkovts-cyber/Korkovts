"""Persistent Spot WATCH -> SPOT BUY NOW watchtower for V11.7.1.

A WATCH candidate is not a trade. The watchtower only promotes a candidate after
price enters the original zone and the full Spot stack is revalidated again.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import DATABASE_PATH

SPOT_RELEASE_KEY="11.8.1-market-intelligence"


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
        c.execute("""
            CREATE TABLE IF NOT EXISTS spot_watchlist(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                base_asset TEXT NOT NULL,
                score REAL NOT NULL,
                setup_type TEXT,
                entry_low REAL NOT NULL,
                entry_high REAL NOT NULL,
                invalidation REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                tp3 REAL NOT NULL,
                relative_percentile REAL,
                excess_btc_14d REAL,
                market_json TEXT,
                feature_json TEXT,
                portfolio_cluster TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                last_check_at TEXT,
                last_ask REAL,
                last_reason TEXT,
                candidate_state TEXT NOT NULL DEFAULT 'WATCH',
                confirm_streak INTEGER NOT NULL DEFAULT 0,
                last_ready_at TEXT,
                ready_score REAL,
                promoted_signal_id INTEGER,
                release_key TEXT
            )
        """)
        cols={r[1] for r in c.execute("PRAGMA table_info(spot_watchlist)")}
        migrations={
            "candidate_state":"TEXT NOT NULL DEFAULT 'WATCH'",
            "confirm_streak":"INTEGER NOT NULL DEFAULT 0",
            "last_ready_at":"TEXT",
            "ready_score":"REAL",
            "release_key":"TEXT",
        }
        for name,definition in migrations.items():
            if name not in cols:
                c.execute(f"ALTER TABLE spot_watchlist ADD COLUMN {name} {definition}")
        c.execute("""
            UPDATE spot_watchlist
            SET status='CANCELLED',candidate_state='WATCH',confirm_streak=0,
                last_reason='Spot release changed; candidate must be rescanned'
            WHERE status='ACTIVE' AND COALESCE(release_key,'')<>?
        """,(SPOT_RELEASE_KEY,))
        c.execute("CREATE INDEX IF NOT EXISTS idx_spot_watch_active ON spot_watchlist(status,updated_at,score)")


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def upsert(signal,ttl_hours=36):
    """Persist/refresh a WATCH or BUY candidate from a fresh broad scan.

    BUY is still only a candidate here. It needs two separated full confirmations
    before the watchtower may promote it to a delivered SPOT BUY NOW.
    """
    incoming=str(getattr(signal,"status","")).upper()
    if incoming not in {"WATCH","BUY"}:
        return None
    init(); now=_iso_now()
    feat=dict(getattr(signal,"feature_snapshot",{}) or {})
    market=dict(feat.get("market") or {})
    portfolio=dict(feat.get("portfolio") or {})
    cluster=str(portfolio.get("cluster_key") or signal.symbol)
    with _db() as c:
        c.execute("""
            INSERT INTO spot_watchlist(
                symbol,base_asset,score,setup_type,entry_low,entry_high,invalidation,
                tp1,tp2,tp3,relative_percentile,excess_btc_14d,market_json,feature_json,
                portfolio_cluster,created_at,updated_at,expires_at,status,candidate_state,
                release_key
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now',?),'ACTIVE',?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                base_asset=excluded.base_asset,
                score=excluded.score,
                setup_type=excluded.setup_type,
                entry_low=excluded.entry_low,
                entry_high=excluded.entry_high,
                invalidation=excluded.invalidation,
                tp1=excluded.tp1,tp2=excluded.tp2,tp3=excluded.tp3,
                relative_percentile=excluded.relative_percentile,
                excess_btc_14d=excluded.excess_btc_14d,
                market_json=excluded.market_json,feature_json=excluded.feature_json,
                portfolio_cluster=excluded.portfolio_cluster,
                created_at=CASE
                    WHEN spot_watchlist.release_key<>excluded.release_key
                      OR spot_watchlist.setup_type<>excluded.setup_type
                      OR spot_watchlist.entry_low<>excluded.entry_low
                      OR spot_watchlist.entry_high<>excluded.entry_high
                      OR spot_watchlist.invalidation<>excluded.invalidation
                    THEN excluded.created_at ELSE spot_watchlist.created_at END,
                updated_at=excluded.updated_at,
                expires_at=excluded.expires_at,status='ACTIVE',last_reason=NULL,
                candidate_state=CASE
                    WHEN excluded.candidate_state='WATCH' THEN 'WATCH'
                    WHEN spot_watchlist.release_key<>excluded.release_key
                      OR spot_watchlist.setup_type<>excluded.setup_type
                      OR spot_watchlist.entry_low<>excluded.entry_low
                      OR spot_watchlist.entry_high<>excluded.entry_high
                      OR spot_watchlist.invalidation<>excluded.invalidation
                    THEN 'READY_PENDING'
                    ELSE spot_watchlist.candidate_state END,
                confirm_streak=CASE
                    WHEN excluded.candidate_state='WATCH' THEN 0
                    WHEN spot_watchlist.release_key<>excluded.release_key
                      OR spot_watchlist.setup_type<>excluded.setup_type
                      OR spot_watchlist.entry_low<>excluded.entry_low
                      OR spot_watchlist.entry_high<>excluded.entry_high
                      OR spot_watchlist.invalidation<>excluded.invalidation
                    THEN 0
                    ELSE spot_watchlist.confirm_streak END,
                last_ready_at=CASE
                    WHEN excluded.candidate_state='WATCH' THEN NULL
                    WHEN spot_watchlist.release_key<>excluded.release_key
                      OR spot_watchlist.setup_type<>excluded.setup_type
                      OR spot_watchlist.entry_low<>excluded.entry_low
                      OR spot_watchlist.entry_high<>excluded.entry_high
                      OR spot_watchlist.invalidation<>excluded.invalidation
                    THEN NULL
                    ELSE spot_watchlist.last_ready_at END,
                ready_score=CASE
                    WHEN excluded.candidate_state='WATCH' THEN NULL
                    WHEN spot_watchlist.release_key<>excluded.release_key
                      OR spot_watchlist.setup_type<>excluded.setup_type
                      OR spot_watchlist.entry_low<>excluded.entry_low
                      OR spot_watchlist.entry_high<>excluded.entry_high
                      OR spot_watchlist.invalidation<>excluded.invalidation
                    THEN NULL
                    ELSE spot_watchlist.ready_score END,
                promoted_signal_id=NULL,
                release_key=excluded.release_key
        """,(
            str(signal.symbol).upper(),str(signal.base_asset).upper(),float(signal.score),
            str(signal.setup_type),float(signal.entry_low),float(signal.entry_high),
            float(signal.invalidation),float(signal.tp1),float(signal.tp2),float(signal.tp3),
            float(signal.relative_percentile),float(signal.excess_btc_14d),
            json.dumps(market,ensure_ascii=False,sort_keys=True,default=str),
            json.dumps(feat,ensure_ascii=False,sort_keys=True,default=str),cluster,
            now,now,f"+{int(ttl_hours)} hours",("READY_PENDING" if incoming=="BUY" else "WATCH"),
            SPOT_RELEASE_KEY
        ))
        row=c.execute("SELECT id FROM spot_watchlist WHERE symbol=?",(str(signal.symbol).upper(),)).fetchone()
    return int(row[0]) if row else None


def get(symbol):
    init()
    with _db() as c:
        row=c.execute("SELECT * FROM spot_watchlist WHERE symbol=?",(str(symbol).upper(),)).fetchone()
    return dict(row) if row else None


def reset_ready(symbol,reason="",ask=None):
    init(); now=_iso_now()
    with _db() as c:
        c.execute("""
            UPDATE spot_watchlist
            SET candidate_state='WATCH',confirm_streak=0,last_ready_at=NULL,ready_score=NULL,
                last_check_at=?,last_ask=?,last_reason=?
            WHERE symbol=? AND status='ACTIVE'
        """,(now,float(ask) if ask is not None else None,str(reason or "")[:300],str(symbol).upper()))


def record_ready(symbol,score=None,ask=None,min_gap_sec=60):
    """Record a separated full BUY confirmation and return the persisted streak."""
    init(); now=datetime.now(timezone.utc)
    with _db() as c:
        row=c.execute("""SELECT confirm_streak,last_ready_at FROM spot_watchlist
                         WHERE symbol=? AND status='ACTIVE'""",(str(symbol).upper(),)).fetchone()
        if not row:
            return 0
        streak=int(row["confirm_streak"] or 0)
        last=row["last_ready_at"]
        separated=True
        if last:
            try:
                dt=datetime.fromisoformat(str(last).replace("Z","+00:00"))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                separated=(now-dt).total_seconds()>=float(min_gap_sec)
            except Exception:
                separated=True
        if separated:
            streak=min(2,streak+1)
            last_ready=now.isoformat()
        else:
            last_ready=last
        c.execute("""
            UPDATE spot_watchlist
            SET candidate_state='READY_PENDING',confirm_streak=?,last_ready_at=?,ready_score=?,
                last_check_at=?,last_ask=?,last_reason=?
            WHERE symbol=? AND status='ACTIVE'
        """,(streak,last_ready,float(score) if score is not None else None,now.isoformat(),
              float(ask) if ask is not None else None,f"BUY confirmation {streak}/2",str(symbol).upper()))
    return streak


def expire_due():
    init(); now=_iso_now()
    with _db() as c:
        cur=c.execute("""
            UPDATE spot_watchlist SET status='EXPIRED',last_reason='WATCH TTL expired',last_check_at=?
            WHERE status='ACTIVE' AND julianday(expires_at)<=julianday('now')
        """,(now,))
    return int(cur.rowcount or 0)


def active(limit=16):
    init(); expire_due()
    with _db() as c:
        rows=c.execute("""
            SELECT * FROM spot_watchlist
            WHERE status='ACTIVE' AND release_key=?
            ORDER BY score DESC,updated_at DESC LIMIT ?
        """,(SPOT_RELEASE_KEY,int(limit))).fetchall()
    return [dict(r) for r in rows]


def count_active():
    init(); expire_due()
    with _db() as c:
        row=c.execute(
            "SELECT COUNT(*) FROM spot_watchlist WHERE status='ACTIVE' AND release_key=?",
            (SPOT_RELEASE_KEY,)
        ).fetchone()
    return int(row[0] or 0)


def record_check(symbol,ask=None,reason=None):
    init(); now=_iso_now()
    with _db() as c:
        c.execute("""
            UPDATE spot_watchlist SET last_check_at=?,last_ask=?,last_reason=?
            WHERE symbol=? AND status='ACTIVE'
        """,(now,float(ask) if ask is not None else None,str(reason or "")[:300],str(symbol).upper()))


def close(symbol,status,reason="",promoted_signal_id=None):
    init(); now=_iso_now()
    status=str(status).upper()
    if status not in {"PROMOTED","PENDING_DELIVERY","CANCELLED","EXPIRED","COOLDOWN"}:
        status="CANCELLED"
    with _db() as c:
        c.execute("""
            UPDATE spot_watchlist SET status=?,last_check_at=?,last_reason=?,promoted_signal_id=?
            WHERE symbol=? AND status IN ('ACTIVE','PENDING_DELIVERY')
        """,(
            status,now,str(reason or "")[:300],
            int(promoted_signal_id) if promoted_signal_id is not None else None,
            str(symbol).upper()
        ))




def reconcile_pending_delivery():
    """Close watch rows whose queued Spot signal expired before anyone received it."""
    init(); now=_iso_now()
    with _db() as c:
        cur=c.execute("""
            UPDATE spot_watchlist
            SET status='CANCELLED',last_check_at=?,
                last_reason='BUY NOW delivery expired before successful Telegram send'
            WHERE status='PENDING_DELIVERY'
              AND promoted_signal_id IN (
                    SELECT id FROM spot_signals
                    WHERE delivered_at IS NULL AND state='CLOSED'
                      AND result='DELIVERY_EXPIRED'
              )
        """,(now,))
    return int(cur.rowcount or 0)


def recent(limit=12):
    init(); expire_due()
    with _db() as c:
        rows=c.execute("""
            SELECT symbol,score,setup_type,entry_low,entry_high,invalidation,relative_percentile,
                   portfolio_cluster,status,candidate_state,confirm_streak,ready_score,last_ready_at,
                   last_ask,last_reason,updated_at,expires_at
            FROM spot_watchlist ORDER BY id DESC LIMIT ?
        """,(int(limit),)).fetchall()
    return [dict(r) for r in rows]


def text():
    rows=recent(12)
    if not rows:
        return "👀 <b>SPOT WATCHTOWER</b>\n━━━━━━━━━━━━━━━━━━\nСильных WATCH-кандидатов пока нет."
    lines=[
        "👀 <b>SPOT WATCHTOWER · 3–10 DAYS</b>","━━━━━━━━━━━━━━━━━━",
        "WATCH не является входом. BUY появится только после повторного полного revalidation.",
    ]
    for r in rows:
        icon={"ACTIVE":"🟡","PROMOTED":"🟢","CANCELLED":"⚪","EXPIRED":"⌛","COOLDOWN":"🧊"}.get(str(r["status"]),"⚪")
        lines += [
            "",
            f"{icon} <b>{r['symbol']}</b> · {r['status']} · {r.get('candidate_state') or 'WATCH'} "
            f"· confirm <b>{int(r.get('confirm_streak') or 0)}/2</b> · Q {float(r['score']):.0f} · RS {float(r.get('relative_percentile') or 0):.0f}",
            f"BUY zone <b>{float(r['entry_low']):.8g} – {float(r['entry_high']):.8g}</b> · invalidation <b>{float(r['invalidation']):.8g}</b>",
        ]
        if r.get("last_ask") is not None:
            lines.append(f"Last ask <b>{float(r['last_ask']):.8g}</b>")
        if r.get("last_reason"):
            lines.append(f"└ {str(r['last_reason'])[:150]}")
    return "\n".join(lines)
