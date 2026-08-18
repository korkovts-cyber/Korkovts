"""Exact Telegram detail references for V11.7.1."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass

from app.config import DATABASE_PATH
from v1171_sqlite import db_session


def init():
    with db_session(timeout=10) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS v112_detail_snapshots(
                id INTEGER PRIMARY KEY,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                signal_id INTEGER,
                symbol TEXT,
                timeframe TEXT,
                payload_json TEXT NOT NULL
            )
        """)
        c.execute("""
            DELETE FROM v112_detail_snapshots
            WHERE created_at<datetime('now','-14 days')
        """)


def save_snapshot(signal,signal_id=None):
    init()
    payload={
        "symbol":str(signal.symbol),
        "timeframe":str(signal.timeframe),
        "side":str(signal.side),
        "score":float(signal.score),
        "entry":float(signal.entry_high if signal.side=="LONG" else signal.entry_low),
        "stop":float(signal.stop),
        "tp1":float(signal.tp1),
        "tp2":float(signal.tp2),
        "tp3":float(signal.tp3),
        "setup_type":str(getattr(signal,"setup_type","") or ""),
        "feature_json":json.dumps(getattr(signal,"feature_snapshot",{}) or {},ensure_ascii=False,default=str),
        "status":"SNAPSHOT",
        "result":None,
        "pnl_r":None,
    }
    with db_session(timeout=10) as c:
        cur=c.execute("""
            INSERT INTO v112_detail_snapshots(signal_id,symbol,timeframe,payload_json)
            VALUES(?,?,?,?)
        """,(int(signal_id) if signal_id is not None else None,
             payload["symbol"],payload["timeframe"],
             json.dumps(payload,ensure_ascii=False,separators=(",",":"))))
        return int(cur.lastrowid)


def signal_row(signal_id):
    with db_session(timeout=10) as c:
        c.row_factory=sqlite3.Row
        row=c.execute("SELECT * FROM signals WHERE id=?",(int(signal_id),)).fetchone()
    return dict(row) if row else None


def snapshot_row(snapshot_id):
    with db_session(timeout=10) as c:
        c.row_factory=sqlite3.Row
        row=c.execute("""
            SELECT id,signal_id,payload_json FROM v112_detail_snapshots WHERE id=?
        """,(int(snapshot_id),)).fetchone()
    if not row:
        return None
    try:
        payload=json.loads(row["payload_json"])
    except Exception:
        return None
    payload["_snapshot_id"]=int(row["id"])
    payload["_signal_id"]=row["signal_id"]
    return payload
