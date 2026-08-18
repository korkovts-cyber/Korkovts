"""V11.8 advisory Active Manager + failure attribution.

The manager never places/cancels exchange orders and never rewrites the original
signal geometry. It emits lifecycle guidance from immutable Entry/Stop/TP data.
Failure labels are heuristic diagnostics, not causal certainty.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape

from app.config import DATABASE_PATH
from app.db import open_signals
from v11_live import price as futures_price, flow as futures_flow
from spot_db import active_signals as active_spot_signals
from spot_market import book_ticker as spot_book_ticker
from spot_orderbook import snapshot as spot_local_book, symbol_health as spot_book_health

log=logging.getLogger(__name__)

_STATES={"HOLD":0,"PROTECT":1,"RISK_WARNING":2,"EXIT":3,"CLOSED":4}


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
            CREATE TABLE IF NOT EXISTS v1180_manager(
                market TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                last_reason TEXT,
                last_price REAL,
                last_metric REAL,
                highest_state TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(market,source_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS v1180_failures(
                market TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                category TEXT NOT NULL,
                detail TEXT,
                release_version TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(market,source_id)
            )
        """)
        cols={r[1] for r in c.execute("PRAGMA table_info(v1180_failures)")}
        if "release_version" not in cols:
            c.execute("ALTER TABLE v1180_failures ADD COLUMN release_version TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_v1180_failure_category ON v1180_failures(market,category)")


def _previous(market,source_id):
    init()
    with _db() as c:
        r=c.execute("SELECT * FROM v1180_manager WHERE market=? AND source_id=?",(market,int(source_id))).fetchone()
    return dict(r) if r else None


def _upsert(market,source_id,state,reason,price=None,metric=None):
    prev=_previous(market,source_id)
    old_high=(prev or {}).get("highest_state") or "HOLD"
    high=state if _STATES.get(state,0)>=_STATES.get(old_high,0) else old_high
    now=datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute("""
            INSERT INTO v1180_manager(
                market,source_id,state,last_reason,last_price,last_metric,highest_state,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(market,source_id) DO UPDATE SET
                state=excluded.state,last_reason=excluded.last_reason,
                last_price=excluded.last_price,last_metric=excluded.last_metric,
                highest_state=excluded.highest_state,updated_at=excluded.updated_at
        """,(market,int(source_id),state,str(reason),price,metric,high,now))
    return prev


def _futures_r(row,px):
    entry=float(row["entry"]); stop=float(row["stop"]); risk=abs(entry-stop)
    if risk<=0:return 0.0
    return (px-entry)/risk if str(row["side"]).upper()=="LONG" else (entry-px)/risk


def _futures_state(row,px):
    side=str(row["side"]).upper(); stop=float(row["stop"]); tp1=float(row["tp1"])
    r=_futures_r(row,px); f=futures_flow(row["symbol"],60,20) or {}
    share=float(f.get("buy_share",.5) or .5)
    stop_cross=(px<=stop) if side=="LONG" else (px>=stop)
    tp1_cross=((px>=tp1) if side=="LONG" else (px<=tp1)) or float(row.get("max_favorable_r") or 0)>=1.0
    opposite=(share<=.42) if side=="LONG" else (share>=.58)
    structure_warned=False
    try:
        with _db() as c:
            x=c.execute("SELECT structure_warned,last_event FROM v11_lifecycle WHERE signal_id=?",(int(row["id"]),)).fetchone()
        structure_warned=bool(x and int(x["structure_warned"] or 0))
    except sqlite3.OperationalError:
        pass
    if stop_cross:
        return "EXIT","original STOP crossed — полный выход по исходному плану",r
    if tp1_cross or r>=1.0:
        return "PROTECT","TP1/+1R достигнут — допускается partial; исходный риск больше не расширять",r
    if structure_warned and opposite:
        return "RISK_WARNING",f"structure weak + live flow opposite ({share:.0%} buyer share)",r
    if r<=-.50 and opposite:
        return "RISK_WARNING",f"trade {r:+.2f}R and live flow reversed ({share:.0%} buyer share)",r
    return "HOLD",f"план пока цел · live {r:+.2f}R",r


def _spot_imbalance(book,mid,bps=20):
    if not book or mid<=0:return 0.0
    lo=mid*(1-bps/10000); hi=mid*(1+bps/10000)
    b=sum(float(p)*float(q) for p,q in book.get("bids",[]) if float(p)>=lo)
    a=sum(float(p)*float(q) for p,q in book.get("asks",[]) if float(p)<=hi)
    return (b-a)/(b+a) if b+a else 0.0


async def _spot_state(row):
    symbol=str(row["symbol"]).upper(); local=spot_local_book(symbol,3.0,100)
    health=spot_book_health(symbol,3.0)
    if local:
        bid=float(local["bids"][0][0]); ask=float(local["asks"][0][0]); px=(bid+ask)/2
        imbalance=_spot_imbalance(local,px)
    else:
        q=await asyncio.wait_for(spot_book_ticker(symbol),timeout=12)
        bid=float(q.get("bid",0) or 0); ask=float(q.get("ask",0) or 0); px=(bid+ask)/2 if bid and ask else float(row["entry_price"])
        imbalance=0.0
    invalid=float(row["invalidation"]); tp1=float(row["tp1"]); entry=float(row["entry_price"])
    risk=max(entry-invalid,1e-12); r=(px-entry)/risk
    if px<=invalid:
        return "EXIT","original invalidation crossed — полный выход по исходному плану",px,r
    if px>=tp1 or bool(int(row.get("tp1_hit") or 0)):
        return "PROTECT","TP1 достигнут — допускается partial; invalidation не расширять",px,r
    if health.get("healthy") and px<entry and imbalance<=-.35:
        return "RISK_WARNING",f"price below entry + local-book imbalance {imbalance:+.2f}",px,r
    if not health.get("healthy"):
        return "HOLD",f"план ценой не сломан; order-book data degraded: {health.get('reason','unknown')}",px,r
    return "HOLD",f"план пока цел · {r:+.2f}R · book imbalance {imbalance:+.2f}",px,r


def _recipient_chats(market,source_id,row=None):
    with _db() as c:
        if market=="FUTURES":
            rows=c.execute("SELECT DISTINCT chat_id FROM signal_deliveries WHERE signal_id=? AND delivered_at IS NOT NULL",(int(source_id),)).fetchall()
        else:
            rows=c.execute("SELECT DISTINCT chat_id FROM spot_deliveries WHERE spot_signal_id=? AND delivered_at IS NOT NULL",(int(source_id),)).fetchall()
    chats=[int(r[0]) for r in rows]
    if not chats and market=="FUTURES" and row and row.get("source_chat_id") is not None:
        chats=[int(row["source_chat_id"])]
    return chats


def _notify_text(market,row,state,reason,price,metric):
    icon={"HOLD":"🟢","PROTECT":"🛡","RISK_WARNING":"⚠️","EXIT":"🛑"}.get(state,"ℹ️")
    label={"HOLD":"HOLD","PROTECT":"PROTECT / PARTIAL","RISK_WARNING":"RISK REVIEW","EXIT":"EXIT"}.get(state,state)
    plan=(
        "Исходные Entry/STOP/TP не меняются задним числом."
        if market=="FUTURES" else
        "Исходные BUY/invalidation/TP не меняются задним числом."
    )
    return (
        f"{icon} <b>{market} MANAGER · {escape(str(row['symbol']))} · {label}</b>\n"
        f"Live <b>{float(price):.8g}</b> · около <b>{float(metric):+.2f}R</b>\n"
        f"{escape(str(reason))}\n{plan}"
    )


def reconcile_closed():
    """Close manager rows once their immutable source trade is no longer open."""
    init(); now=datetime.now(timezone.utc).isoformat(); closed=0
    with _db() as c:
        rows=c.execute(
            "SELECT market,source_id FROM v1180_manager WHERE state<>'CLOSED'"
        ).fetchall()
        for r in rows:
            market=str(r["market"]); source_id=int(r["source_id"])
            if market=="FUTURES":
                live=c.execute(
                    """SELECT 1 FROM signals WHERE id=? AND COALESCE(is_shadow,0)=0
                       AND status IN ('SENT','WAITING','ACTIVE','OPEN','PENDING_DELIVERY')
                       AND COALESCE(delivery_state,'DELIVERED') IN ('PENDING','DELIVERED','UNCERTAIN')""",
                    (source_id,)
                ).fetchone()
            else:
                live=c.execute(
                    """SELECT 1 FROM spot_signals WHERE id=? AND signal_status='BUY'
                       AND delivered_at IS NOT NULL AND COALESCE(delivery_uncertain,0)=0
                       AND state='OPEN'""",
                    (source_id,)
                ).fetchone()
            if not live:
                c.execute(
                    """UPDATE v1180_manager SET state='CLOSED',highest_state=CASE
                           WHEN highest_state='EXIT' THEN highest_state ELSE 'CLOSED' END,
                           last_reason='source trade closed',updated_at=?
                       WHERE market=? AND source_id=?""",
                    (now,market,source_id)
                ); closed+=1
    return closed


async def observe(bot=None,notify=True):
    init(); reconcile_closed(); events=[]
    futures=[r for r in open_signals() if str(r.get("status"))=="ACTIVE" and not int(r.get("is_shadow") or 0)]
    for row in futures:
        px=futures_price(row["symbol"],20)
        if px is None:continue
        state,reason,r=_futures_state(row,float(px))
        old=_previous("FUTURES",row["id"])
        if old and str(old.get("state"))=="EXIT":
            state="EXIT"; reason=str(old.get("last_reason") or "EXIT already issued")
        prev=_upsert("FUTURES",row["id"],state,reason,float(px),r)
        if prev is None or str(prev.get("state"))!=state:
            events.append(("FUTURES",row,state,reason,float(px),r))

    for row in active_spot_signals(10):
        try:
            state,reason,px,r=await _spot_state(row)
        except Exception as exc:
            log.debug("V11.8 Spot manager unavailable %s: %s",row.get("symbol"),exc)
            continue
        old=_previous("SPOT",row["id"])
        if old and str(old.get("state"))=="EXIT":
            state="EXIT"; reason=str(old.get("last_reason") or "EXIT already issued")
        prev=_upsert("SPOT",row["id"],state,reason,px,r)
        if prev is None or str(prev.get("state"))!=state:
            events.append(("SPOT",row,state,reason,px,r))

    if notify and bot:
        for market,row,state,reason,px,r in events:
            # Initial HOLD is useful in state storage but noisy in Telegram.
            if state=="HOLD":continue
            text=_notify_text(market,row,state,reason,px,r)
            for chat_id in _recipient_chats(market,row["id"],row):
                try:
                    await bot.send_message(chat_id,text,parse_mode="HTML")
                except Exception:
                    log.exception("V11.8 manager notification failed")
    return events


def _json(raw):
    try:return json.loads(raw or "{}")
    except Exception:return {}


def _failure_upsert(market,source_id,symbol,category,detail,release_version=None):
    with _db() as c:
        c.execute("""
            INSERT OR IGNORE INTO v1180_failures(
                market,source_id,symbol,category,detail,release_version,created_at
            ) VALUES(?,?,?,?,?,?,?)
        """,(
            market,int(source_id),str(symbol).upper(),category,str(detail)[:700],
            str(release_version or ""),datetime.now(timezone.utc).isoformat()
        ))


def _classify_futures(row,manager,life):
    feat=_json(row.get("feature_json")); news=dict(feat.get("news") or {}); der=dict(feat.get("derivatives") or {})
    entry=dict(feat.get("entry_now_v1142") or {})
    reason=str((manager or {}).get("last_reason") or "").lower()
    if news.get("breaking") or float(news.get("event_risk",0) or 0)>=.67:
        return "NEWS_RISK_SIGNATURE","entry snapshot contained elevated/breaking news risk"
    if "flow" in reason:
        return "FLOW_REVERSAL",reason
    if life and int(life.get("structure_warned") or 0):
        return "STRUCTURE_FAILURE",str(life.get("last_event") or "structure warned")
    if abs(float(der.get("funding",0) or 0))>=.001 or float(der.get("top_position_ls",1) or 1)>=1.8:
        return "OI_CROWDING","entry derivatives showed elevated crowding"
    if abs(float(entry.get("distance_r",0) or 0))>=.18:
        return "LATE_ENTRY",f"entry distance {float(entry.get('distance_r',0)):+.2f}R"
    return "FALSE_BREAKOUT","no stronger post-entry failure signature found"


def _classify_spot(row,manager):
    feat=_json(row.get("feature_json")); news=dict(feat.get("news") or {}); micro=dict(feat.get("micro") or {})
    reason=str((manager or {}).get("last_reason") or "").lower(); head=float(feat.get("headroom_r",999) or 999)
    if news.get("recent_negative") or news.get("block"):
        return "NEWS_RISK_SIGNATURE","negative/news-risk signature"
    if "imbalance" in reason or "flow" in reason:
        return "FLOW_REVERSAL",reason
    if head<1.0:
        return "BAD_HEADROOM",f"headroom only {head:.2f}R"
    if float(micro.get("buy_share",.5) or .5)<.52:
        return "WEAK_ACCUMULATION","entry flow was marginal"
    regime=str(row.get("market_regime") or "").upper()
    if regime!="BULL":
        return "REGIME_WEAKNESS",f"entry regime {regime or 'unknown'}"
    return "FALSE_BREAKOUT","planned TP1 was not achieved before invalidation"


def sync_failures(limit=500):
    init(); added=0
    with _db() as c:
        futures=c.execute("""
            SELECT s.* FROM signals s
            LEFT JOIN v1180_failures f ON f.market='FUTURES' AND f.source_id=s.id
            WHERE f.source_id IS NULL AND s.status='CLOSED' AND COALESCE(s.is_shadow,0)=0
              AND s.pnl_r<0 AND COALESCE(s.delivery_state,'DELIVERED') IN ('DELIVERED','UNCERTAIN')
              AND COALESCE(s.release_version,'')='11.7.1-futures-evidence'
            ORDER BY s.id DESC LIMIT ?
        """,(int(limit),)).fetchall()
        for raw in futures:
            row=dict(raw)
            m=c.execute("SELECT * FROM v1180_manager WHERE market='FUTURES' AND source_id=?",(row["id"],)).fetchone()
            try:life=c.execute("SELECT * FROM v11_lifecycle WHERE signal_id=?",(row["id"],)).fetchone()
            except sqlite3.OperationalError:life=None
            category,detail=_classify_futures(row,dict(m) if m else {},dict(life) if life else {})
            _failure_upsert(
                "FUTURES",row["id"],row["symbol"],category,detail,row.get("release_version")
            ); added+=1

        spot=c.execute("""
            SELECT s.* FROM spot_signals s
            LEFT JOIN v1180_failures f ON f.market='SPOT' AND f.source_id=s.id
            WHERE f.source_id IS NULL AND s.signal_status='BUY' AND s.delivered_at IS NOT NULL
              AND COALESCE(s.delivery_uncertain,0)=0 AND s.invalidated=1
              AND COALESCE(s.release_version,'')='11.8.1-market-intelligence'
            ORDER BY s.id DESC LIMIT ?
        """,(int(limit),)).fetchall()
        for raw in spot:
            row=dict(raw)
            # If TP1 clearly happened first, it was not a failed first target.
            try:
                from v1170_calibration import _spot_success_value
                outcome=_spot_success_value(row)
            except Exception:outcome=None
            if outcome is None or outcome>0:continue
            m=c.execute("SELECT * FROM v1180_manager WHERE market='SPOT' AND source_id=?",(row["id"],)).fetchone()
            category,detail=_classify_spot(row,dict(m) if m else {})
            _failure_upsert(
                "SPOT",row["id"],row["symbol"],category,detail,row.get("release_version")
            ); added+=1
    return added


def active_rows():
    init(); reconcile_closed()
    with _db() as c:
        rows=c.execute("SELECT * FROM v1180_manager WHERE state<>'CLOSED' ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [dict(r) for r in rows]


def failure_counts():
    init(); sync_failures()
    with _db() as c:
        rows=c.execute("SELECT market,category,COUNT(*) n FROM v1180_failures GROUP BY market,category ORDER BY n DESC").fetchall()
    return [dict(r) for r in rows]


def text():
    init(); sync_failures(); rows=active_rows(); failures=failure_counts()
    lines=["🧭 <b>ACTIVE MANAGER · V11.8</b>","━━━━━━━━━━━━━━━━━━"]
    if not rows:
        lines.append("Нет сохранённых активных manager-состояний.")
    else:
        for r in rows[:10]:
            icon={"HOLD":"🟢","PROTECT":"🛡","RISK_WARNING":"⚠️","EXIT":"🛑"}.get(r["state"],"ℹ️")
            lines.append(
                f"{icon} {r['market']} #{r['source_id']} · <b>{r['state']}</b> · "
                f"{escape(str(r['last_reason'] or ''))}"
            )
    if failures:
        lines += ["","<b>Вероятные причины прошлых минусов:</b>"]
        for r in failures[:10]:
            lines.append(f"• {r['market']} · {r['category']} — {r['n']}")
        lines.append("Категории диагностические: это не доказательство причинности.")
    return "\n".join(lines)
