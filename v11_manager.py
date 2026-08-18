"""V11.4 advisory lifecycle manager.

No exchange orders are placed. app.tracker remains the conservative forward-test
source of truth; this module only records/announces live milestones.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from app.config import DATABASE_PATH
from app.db import open_signals
from app.indicators import enrich
from app.market import get_klines

from v11_live import price as live_price

log=logging.getLogger(__name__)


def _connect():
    c=sqlite3.connect(DATABASE_PATH,timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    return c


def init():
    with _connect() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS v11_lifecycle(
                signal_id INTEGER PRIMARY KEY,
                last_price REAL,
                max_r REAL DEFAULT 0,
                tp1_seen INTEGER DEFAULT 0,
                tp2_seen INTEGER DEFAULT 0,
                structure_warned INTEGER DEFAULT 0,
                last_event TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _row(signal_id):
    init()
    with _connect() as c:
        c.row_factory=sqlite3.Row
        r=c.execute("SELECT * FROM v11_lifecycle WHERE signal_id=?",(int(signal_id),)).fetchone()
    return dict(r) if r else {}


def _upsert(signal_id, **fields):
    init()
    allowed={"last_price","max_r","tp1_seen","tp2_seen","structure_warned","last_event"}
    fields={k:v for k,v in fields.items() if k in allowed}
    with _connect() as c:
        c.execute("INSERT OR IGNORE INTO v11_lifecycle(signal_id) VALUES(?)",(int(signal_id),))
        if fields:
            sets=",".join(f"{k}=?" for k in fields)
            values=list(fields.values())+[int(signal_id)]
            c.execute(f"UPDATE v11_lifecycle SET {sets},updated_at=CURRENT_TIMESTAMP WHERE signal_id=?",values)


def _r_multiple(row, px):
    entry=float(row["entry"]); stop=float(row["stop"])
    risk=abs(entry-stop)
    if risk<=0: return 0.0
    return (px-entry)/risk if row["side"]=="LONG" else (entry-px)/risk


def target_flags(row,px):
    """Use advertised TP prices, not assumed 1R/2R geometry."""
    px=float(px)
    if row["side"]=="LONG":
        return px>=float(row["tp1"]),px>=float(row["tp2"])
    return px<=float(row["tp1"]),px<=float(row["tp2"])


def _recipient_chats(signal_id,row=None):
    """Exact recipients: auto outbox chats, else the manual source chat."""
    chats=[]
    try:
        with _connect() as c:
            chats=[int(r[0]) for r in c.execute("""
                SELECT DISTINCT d.chat_id
                FROM signal_deliveries d
                JOIN subscribers s ON s.chat_id=d.chat_id AND s.enabled=1
                WHERE d.signal_id=? AND d.delivered_at IS NOT NULL
            """,(int(signal_id),)).fetchall()]
    except Exception:
        chats=[]
    source=(row or {}).get("source_chat_id")
    if not chats and source is not None:
        chats=[int(source)]
    return chats


async def observe(bot=None, notify=True):
    rows=[r for r in open_signals() if not int(r.get("is_shadow") or 0)]
    events=[]
    for row in rows:
        if str(row.get("status"))!="ACTIVE":
            continue
        px=live_price(row["symbol"],20)
        if px is None:
            continue
        state=_row(row["id"])
        r=_r_multiple(row,px)
        max_r=max(float(state.get("max_r") or 0),r)
        tp1_hit,tp2_hit=target_flags(row,px)
        changes={"last_price":px,"max_r":max_r}
        event=None
        if tp2_hit and not int(state.get("tp2_seen") or 0):
            changes.update(tp1_seen=1,tp2_seen=1,last_event="TP2_LIVE")
            event=("TP2",row,px,r)
        elif tp1_hit and not int(state.get("tp1_seen") or 0):
            changes.update(tp1_seen=1,last_event="TP1_LIVE")
            event=("TP1",row,px,r)
        _upsert(row["id"],**changes)
        if event:
            events.append(event)

    if notify and bot:
        for kind,row,px,r in events:
            text=(
                f"{'✅' if kind=='TP1' else '🏁'} <b>{row['symbol']} · {kind} ЗОНА ДОСТИГНУТА</b>\n"
                f"Live цена: <b>{px:.8g}</b> · около <b>{r:.2f}R</b>\n"
                "Это live-наблюдение; консервативный forward-test подтверждается отдельно по закрытым 1m данным."
            )
            for chat_id in _recipient_chats(row["id"],row):
                try:
                    await bot.send_message(chat_id,text,parse_mode="HTML")
                except Exception:
                    log.exception("V11 lifecycle notification failed")
    return events


def _structure_broken(row,frame):
    if frame is None or len(frame)<220:
        return False
    a=enrich(frame).iloc[-1]
    if row["side"]=="LONG":
        tests=(a.ema20<a.ema50,a.close<a.ema50,a.macd_hist<0 and a.minus_di>a.plus_di,a.supertrend_dir<0)
    else:
        tests=(a.ema20>a.ema50,a.close>a.ema50,a.macd_hist>0 and a.plus_di>a.minus_di,a.supertrend_dir>0)
    return sum(bool(x) for x in tests)>=3


async def structure_watch(bot=None,notify=True):
    rows=[r for r in open_signals() if str(r.get("status"))=="ACTIVE" and not int(r.get("is_shadow") or 0)]
    if not rows: return []
    sem=asyncio.Semaphore(4)

    async def one(row):
        state=_row(row["id"])
        if int(state.get("structure_warned") or 0):
            return None
        interval="15m" if row.get("timeframe")=="15M" else "1h"
        try:
            async with sem:
                frame=await asyncio.wait_for(get_klines(row["symbol"],interval,260),timeout=20)
            if _structure_broken(row,frame):
                _upsert(row["id"],structure_warned=1,last_event="STRUCTURE_WEAK")
                return row
        except Exception:
            log.exception("V11 structure watch failed for %s",row.get("symbol"))
        return None

    events=[x for x in await asyncio.gather(*(one(r) for r in rows)) if x]
    if notify and bot:
        for row in events:
            text=(
                f"⚠️ <b>{row['symbol']} · СТРУКТУРА ОСЛАБЛА</b>\n"
                "После активации появились признаки разворота на рабочем таймфрейме.\n"
                "Это предупреждение для пересмотра риска, не автоматическая команда закрыть позицию."
            )
            for chat_id in _recipient_chats(row["id"],row):
                try:
                    await bot.send_message(chat_id,text,parse_mode="HTML")
                except Exception:
                    log.exception("V11 structure alert failed")
    return events


def _status_query(where,args):
    init()
    with _connect() as c:
        c.row_factory=sqlite3.Row
        sig=c.execute(f"""
            SELECT id,created_at,activated_at,closed_at,status,result,pnl_r,symbol,timeframe,side,
                   entry,stop,tp1,tp2,tp3
            FROM signals WHERE {where} AND COALESCE(is_shadow,0)=0
            ORDER BY id DESC LIMIT 1
        """,args).fetchone()
        if not sig: return None
        life=c.execute("SELECT * FROM v11_lifecycle WHERE signal_id=?",(sig["id"],)).fetchone()
    return {"signal":dict(sig),"live":dict(life) if life else {}}


def lifecycle_status(symbol,timeframe=None):
    if timeframe:
        return _status_query("symbol=? AND timeframe=?",(str(symbol).upper(),str(timeframe).upper()))
    return _status_query("symbol=?",(str(symbol).upper(),))


def lifecycle_status_by_id(signal_id):
    return _status_query("id=?",(int(signal_id),))
