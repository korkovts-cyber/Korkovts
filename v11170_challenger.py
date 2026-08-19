"""V11.17 shadow challenger decision journal with bounded retention."""
from __future__ import annotations
import json,sqlite3
from datetime import datetime,timezone
SCHEMA="11.17-challenger-v2"
MAX_ROWS=50000


def _db_path():
    from app.config import DATABASE_PATH
    return DATABASE_PATH


def init():
    con=sqlite3.connect(_db_path(),timeout=10)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS v11170_challenger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,captured_at TEXT NOT NULL,
            symbol TEXT,timeframe TEXT,side TEXT,production_pass INTEGER NOT NULL,
            challenger_label TEXT NOT NULL,snapshot_fp TEXT,payload_json TEXT NOT NULL)""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_v11170_challenger_time ON v11170_challenger(captured_at)")
        con.commit()
    finally: con.close()


def record(signal,production_pass,snapshot_fp,payload=None,label="BASELINE_SHADOW"):
    init(); con=sqlite3.connect(_db_path(),timeout=10)
    try:
        con.execute("INSERT INTO v11170_challenger(captured_at,symbol,timeframe,side,production_pass,challenger_label,snapshot_fp,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(),str(getattr(signal,"symbol","")),str(getattr(signal,"timeframe","")),str(getattr(signal,"side","")),int(bool(production_pass)),str(label),str(snapshot_fp or ""),json.dumps(payload or {},sort_keys=True,separators=(",",":"))))
        # Bounded journal: preserve recent observations without unbounded Railway volume growth.
        row=con.execute("SELECT COUNT(*) FROM v11170_challenger").fetchone()
        if row and int(row[0] or 0)>MAX_ROWS:
            con.execute("DELETE FROM v11170_challenger WHERE id IN (SELECT id FROM v11170_challenger ORDER BY id ASC LIMIT ?)",(max(1000,int(row[0])-MAX_ROWS),))
        con.commit()
    finally: con.close()


def report_text(limit=10):
    try:
        init(); con=sqlite3.connect(_db_path(),timeout=10); con.row_factory=sqlite3.Row
        try:
            rows=con.execute("SELECT challenger_label,COUNT(*) n,SUM(production_pass) passed FROM v11170_challenger GROUP BY challenger_label ORDER BY n DESC LIMIT ?",(max(1,min(int(limit),30)),)).fetchall()
        finally: con.close()
        lines=["🧪 <b>CHALLENGER SHADOW · V11.17.1</b>","━━━━━━━━━━━━━━━━━━"]
        if not rows:return "\n".join(lines+["Пока нет shadow-снимков."])
        for r in rows: lines.append(f"• <b>{r['challenger_label']}</b> · {int(r['passed'] or 0)}/{int(r['n'] or 0)} production-equivalent PASS")
        lines+=["","<i>Challenger не может отправлять торговые сигналы.</i>"]
        return "\n".join(lines)
    except Exception:return "⚠️ Challenger report временно недоступен."
