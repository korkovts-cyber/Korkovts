"""V11.7.1 ENTRY NOW state machine for Binance USD-M Futures.

A high-quality setup is not automatically a command to enter. This module keeps
qualified candidates in a persistent ARMED journal and promotes them to
ENTER_NOW only when price location, 1m/3m micro-trend, taker flow and live
spread agree persistently.

It does not place exchange orders.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from app.config import DATABASE_PATH
from app.market import get_klines
from v11_live import price as live_price, book as live_book, flow as live_flow
from v11130_geometry import material_geometry_change

log=logging.getLogger(__name__)
FUTURES_RELEASE_KEY="11.7.1-futures-evidence"


@dataclass(frozen=True)
class TriggerAssessment:
    state:str
    score:float
    reason:str
    price:float
    bid:float
    ask:float
    spread_bps:float
    distance_r:float
    one_min_ok:bool
    three_min_ok:bool
    flow_ok:bool
    flow_share:float
    flow_source:str
    candle_ok:bool
    volume_ratio:float
    checked_at:float


def _connect():
    c=sqlite3.connect(DATABASE_PATH,timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    c.row_factory=sqlite3.Row
    return c


@contextmanager
def _db():
    c=_connect()
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
            CREATE TABLE IF NOT EXISTS v1142_armed(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                side TEXT NOT NULL,
                setup_type TEXT,
                source TEXT,
                source_pro REAL,
                entry_low REAL NOT NULL,
                entry_high REAL NOT NULL,
                stop REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                tp3 REAL NOT NULL,
                source_json TEXT,
                armed_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                last_state TEXT,
                last_score REAL,
                last_reason TEXT,
                last_price REAL,
                last_check_at REAL,
                confirm_streak INTEGER NOT NULL DEFAULT 0,
                triggered_at REAL,
                triggered_signal_id INTEGER,
                release_key TEXT
            )
        """)
        cols={r[1] for r in c.execute("PRAGMA table_info(v1142_armed)")}
        if "release_key" not in cols:
            c.execute("ALTER TABLE v1142_armed ADD COLUMN release_key TEXT")
        # A materially changed Futures decision layer must never inherit an
        # ARMED/PENDING setup created by an older release.
        c.execute("""
            UPDATE v1142_armed
            SET status='CANCELLED',last_state='CANCEL',
                last_reason='release changed before entry'
            WHERE status IN ('ACTIVE','PENDING_DELIVERY')
              AND COALESCE(release_key,'')<>?
        """,(FUTURES_RELEASE_KEY,))
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_v1142_armed_active
            ON v1142_armed(status,expires_at)
        """)
        c.execute("DROP INDEX IF EXISTS idx_v1142_one_active")
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_v1171_one_live_arm
            ON v1142_armed(symbol,timeframe,side)
            WHERE status IN ('ACTIVE','PENDING_DELIVERY')
        """)


def _ttl_seconds(timeframe):
    return 45*60 if str(timeframe).upper()=="15M" else 3*3600


def _signal_snapshot(signal):
    return {
        "symbol":str(signal.symbol),
        "timeframe":str(signal.timeframe),
        "side":str(signal.side),
        "setup_type":str(getattr(signal,"setup_type","") or ""),
        "source_pro":float(getattr(signal,"professional_rank",0) or 0),
        "entry_low":float(signal.entry_low),
        "entry_high":float(signal.entry_high),
        "stop":float(signal.stop),
        "tp1":float(signal.tp1),
        "tp2":float(signal.tp2),
        "tp3":float(signal.tp3),
        "features":getattr(signal,"feature_snapshot",{}) or {},
    }


def arm(signal,source="auto_scan"):
    """Arm/update a setup without sliding its original expiry forward."""
    init()
    now=time.time()
    symbol=str(signal.symbol).upper()
    timeframe=str(signal.timeframe).upper()
    side=str(signal.side).upper()
    snap=_signal_snapshot(signal)
    expiry=now+_ttl_seconds(timeframe)
    payload=json.dumps(snap,ensure_ascii=False,sort_keys=True,default=str)

    with _db() as c:
        # Clean the logically expired row first. A later scan may create a
        # genuinely new setup, but repeated scans cannot extend one occurrence forever.
        c.execute("""
            UPDATE v1142_armed
            SET status='EXPIRED',last_state='EXPIRED',
                last_reason='entry trigger TTL expired',last_check_at=?
            WHERE status='ACTIVE' AND expires_at<=?
        """,(now,now))
        row=c.execute("""
            SELECT id,armed_at,expires_at,entry_low,entry_high,stop,tp1,tp2,tp3,
                   last_state,last_score,last_reason,last_price,last_check_at,confirm_streak
            FROM v1142_armed
            WHERE symbol=? AND timeframe=? AND side=? AND status='ACTIVE'
              AND release_key=?
            ORDER BY id DESC LIMIT 1
        """,(symbol,timeframe,side,FUTURES_RELEASE_KEY)).fetchone()
        if row:
            changed,change_reason=material_geometry_change(dict(row),{**snap,"side":side})
            if changed:
                # Confirmation streak checks must never belong to one geometry while later checks
                # belongs to another. A material Entry/Stop/TP refresh starts a
                # fresh micro-confirmation streak without extending the setup TTL.
                c.execute("""
                    UPDATE v1142_armed
                    SET setup_type=?,source=?,source_pro=?,entry_low=?,entry_high=?,
                        stop=?,tp1=?,tp2=?,tp3=?,source_json=?,release_key=?,
                        last_state=NULL,last_score=NULL,last_reason=?,last_price=NULL,
                        last_check_at=NULL,confirm_streak=0
                    WHERE id=?
                """,(
                    snap["setup_type"],source,snap["source_pro"],snap["entry_low"],
                    snap["entry_high"],snap["stop"],snap["tp1"],snap["tp2"],
                    snap["tp3"],payload,FUTURES_RELEASE_KEY,
                    "setup geometry changed; confirmation reset: "+change_reason,int(row["id"])
                ))
            else:
                c.execute("""
                    UPDATE v1142_armed
                    SET setup_type=?,source=?,source_pro=?,entry_low=?,entry_high=?,
                        stop=?,tp1=?,tp2=?,tp3=?,source_json=?,release_key=?
                    WHERE id=?
                """,(
                    snap["setup_type"],source,snap["source_pro"],snap["entry_low"],
                    snap["entry_high"],snap["stop"],snap["tp1"],snap["tp2"],
                    snap["tp3"],payload,FUTURES_RELEASE_KEY,int(row["id"])
                ))
            return int(row["id"])

        cur=c.execute("""
            INSERT INTO v1142_armed(
                symbol,timeframe,side,setup_type,source,source_pro,
                entry_low,entry_high,stop,tp1,tp2,tp3,source_json,
                armed_at,expires_at,status,release_key
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?)
        """,(
            symbol,timeframe,side,snap["setup_type"],source,snap["source_pro"],
            snap["entry_low"],snap["entry_high"],snap["stop"],snap["tp1"],
            snap["tp2"],snap["tp3"],payload,now,expiry,FUTURES_RELEASE_KEY
        ))
        return int(cur.lastrowid)


def expire_due():
    init()
    now=time.time()
    with _db() as c:
        return c.execute("""
            UPDATE v1142_armed
            SET status='EXPIRED',last_state='EXPIRED',
                last_reason='entry trigger TTL expired',last_check_at=?
            WHERE status='ACTIVE' AND release_key=? AND expires_at<=?
        """,(now,FUTURES_RELEASE_KEY,now)).rowcount




def get_row(arm_id):
    init()
    with _db() as c:
        row=c.execute("SELECT * FROM v1142_armed WHERE id=?",(int(arm_id),)).fetchone()
    return dict(row) if row else None


def active_rows(limit=30):
    init(); expire_due()
    with _db() as c:
        rows=c.execute("""
            SELECT * FROM v1142_armed
            WHERE status='ACTIVE' AND release_key=?
            ORDER BY source_pro DESC,armed_at ASC LIMIT ?
        """,(FUTURES_RELEASE_KEY,int(limit))).fetchall()
    return [dict(r) for r in rows]


def active_symbols(limit=19):
    """Symbols needing live WS data, including Telegram-pending ENTRY NOW rows."""
    init(); expire_due()
    with _db() as c:
        rows=c.execute("""
            SELECT symbol,source_pro,armed_at FROM v1142_armed
            WHERE status IN ('ACTIVE','PENDING_DELIVERY') AND release_key=?
            ORDER BY CASE WHEN status='PENDING_DELIVERY' THEN 0 ELSE 1 END,
                     source_pro DESC,armed_at ASC
            LIMIT ?
        """,(FUTURES_RELEASE_KEY,int(limit)*2)).fetchall()
    seen=[]
    for row in rows:
        symbol=str(row["symbol"]).upper()
        if symbol not in seen:
            seen.append(symbol)
        if len(seen)>=limit:
            break
    return tuple(seen)


def row_from_signal(signal):
    return {
        "symbol":str(signal.symbol).upper(),
        "timeframe":str(signal.timeframe).upper(),
        "side":str(signal.side).upper(),
        "setup_type":str(getattr(signal,"setup_type","") or ""),
        "entry_low":float(signal.entry_low),
        "entry_high":float(signal.entry_high),
        "stop":float(signal.stop),
        "tp1":float(signal.tp1),
        "tp2":float(signal.tp2),
        "tp3":float(signal.tp3),
        "source_pro":float(getattr(signal,"professional_rank",0) or 0),
    }


def _frame_features(df):
    if df is None or len(df)<25:
        return None
    x=df.copy()
    for col in ("open","high","low","close","volume","taker_buy_base"):
        x[col]=pd.to_numeric(x[col],errors="coerce")
    close=x["close"]
    ema5=close.ewm(span=5,adjust=False,min_periods=5).mean()
    ema13=close.ewm(span=13,adjust=False,min_periods=13).mean()
    a=x.iloc[-1]; p=x.iloc[-2]
    rng=max(1e-12,float(a.high)-float(a.low))
    close_loc=(float(a.close)-float(a.low))/rng
    body=(float(a.close)-float(a.open))/rng
    recent=x.iloc[-3:]
    vol=float(recent["volume"].mean())
    base=x.iloc[-23:-3]["volume"]
    baseline=float(base.mean()) if len(base) else 0.0
    volume_ratio=vol/baseline if baseline>0 else 0.0
    total_vol=float(recent["volume"].sum())
    taker_share=(
        float(recent["taker_buy_base"].sum())/total_vol if total_vol>0 else .5
    )
    return {
        "close":float(a.close),"prev_close":float(p.close),
        "ema5":float(ema5.iloc[-1]),"ema13":float(ema13.iloc[-1]),
        "prev_ema5":float(ema5.iloc[-2]),"prev_ema13":float(ema13.iloc[-2]),
        "close_loc":close_loc,"body":body,
        "volume_ratio":volume_ratio,"taker_share":taker_share,
    }


def _cancel_assessment(reason,px,bid,ask,spread,distance):
    return TriggerAssessment(
        "CANCEL",0.0,reason,px,bid,ask,spread,distance,
        False,False,False,.5,"none",False,0.0,time.time()
    )


def evaluate(row,frame1,frame3,px=None,bk=None,flow_row=None):
    """Pure-ish entry readiness evaluation. Score is readiness, not win probability."""
    side=str(row["side"]).upper()
    tf=str(row["timeframe"]).upper()
    low=min(float(row["entry_low"]),float(row["entry_high"]))
    high=max(float(row["entry_low"]),float(row["entry_high"]))
    stop=float(row["stop"]); tp1=float(row["tp1"])
    reference=high if side=="LONG" else low
    risk=abs(reference-stop)
    if risk<=0:
        return _cancel_assessment("invalid entry/stop geometry",0,0,0,999,0)

    if bk:
        bid=float(bk.get("bid") or 0); ask=float(bk.get("ask") or 0)
    else:
        bid=ask=0.0
    if px is None:
        px=(bid+ask)/2 if bid>0 and ask>0 else 0.0
    px=float(px or 0)
    if px<=0:
        return TriggerAssessment(
            "WAIT",0.0,"live price unavailable",0,bid,ask,999,0,
            False,False,False,.5,"none",False,0.0,time.time()
        )
    if bid<=0 or ask<=0 or ask<bid:
        return TriggerAssessment(
            "WAIT",0.0,"fresh best bid/ask unavailable",px,bid,ask,999,0,
            False,False,False,.5,"none",False,0.0,time.time()
        )

    mid=(bid+ask)/2
    spread=(ask-bid)/mid*10000 if mid else 999
    execution_px=ask if side=="LONG" else bid
    distance=(execution_px-reference)/risk if side=="LONG" else (reference-execution_px)/risk

    if side=="LONG":
        if bid<=stop:
            return _cancel_assessment("LONG invalidated before entry",px,bid,ask,spread,distance)
        if ask>=tp1:
            return _cancel_assessment("move already reached TP1; do not chase",px,bid,ask,spread,distance)
        if ask>high+.25*risk:
            return _cancel_assessment("move escaped entry by >0.25R",px,bid,ask,spread,distance)
        if ask<low-.25*risk:
            return _cancel_assessment("setup degraded >0.25R below entry zone",px,bid,ask,spread,distance)
        price_ok=(low-.05*risk)<=ask<=(high+.10*risk)
    else:
        if ask>=stop:
            return _cancel_assessment("SHORT invalidated before entry",px,bid,ask,spread,distance)
        if bid<=tp1:
            return _cancel_assessment("move already reached TP1; do not chase",px,bid,ask,spread,distance)
        if bid<low-.25*risk:
            return _cancel_assessment("move escaped entry by >0.25R",px,bid,ask,spread,distance)
        if bid>high+.25*risk:
            return _cancel_assessment("setup degraded >0.25R above entry zone",px,bid,ask,spread,distance)
        price_ok=(low-.10*risk)<=bid<=(high+.05*risk)

    max_spread=2.5 if tf=="15M" else 3.5
    spread_ok=spread<=max_spread

    f1=_frame_features(frame1)
    f3=_frame_features(frame3)
    if not f1 or not f3:
        return TriggerAssessment(
            "WAIT",0.0,"1m/3m closed-candle confirmation unavailable",
            px,bid,ask,spread,distance,False,False,False,.5,
            "none",False,0.0,time.time()
        )

    if side=="LONG":
        one_ok=(
            f1["close"]>f1["ema5"]>=f1["ema13"]
            and f1["close"]>=f1["prev_close"]
        )
        three_ok=f3["close"]>f3["ema5"]>=f3["ema13"]
        candle_ok=f1["body"]>0 and f1["close_loc"]>=.58
        candle_flow_ok=f1["taker_share"]>=.52
    else:
        one_ok=(
            f1["close"]<f1["ema5"]<=f1["ema13"]
            and f1["close"]<=f1["prev_close"]
        )
        three_ok=f3["close"]<f3["ema5"]<=f3["ema13"]
        candle_ok=f1["body"]<0 and f1["close_loc"]<=.42
        candle_flow_ok=f1["taker_share"]<=.48

    flow_age=(flow_row or {}).get("age_sec")
    live_valid=bool(
        flow_row
        and float(flow_row.get("total_notional",0) or 0)>=5000
        and int(flow_row.get("trades",0) or 0)>=5
        and flow_age is not None
        and 0.0<=float(flow_age)<=20.0
    )
    if live_valid:
        flow_share=float(flow_row["buy_share"])
        flow_source="live_aggTrade_60s"
        flow_ok=flow_share>=.52 if side=="LONG" else flow_share<=.48
    else:
        # Closed 1m taker share is useful context, but an exact "enter now"
        # command requires genuinely live aggressive flow. When WS is warming
        # up/degraded the setup stays ARMED instead of pretending the old candle
        # is a real-time trigger.
        flow_share=float(f1["taker_share"])
        flow_source="closed_1m_context_only"
        flow_ok=False

    setup=str(row.get("setup_type") or "").upper()
    breakout=("ПРОБОЙ" in setup or "BREAKOUT" in setup)
    min_volume=1.0 if breakout else .70
    volume_ok=float(f1["volume_ratio"])>=min_volume

    score=0.0
    score+=30 if price_ok else 0
    score+=10 if spread_ok else 0
    score+=20 if one_ok else 0
    score+=15 if three_ok else 0
    score+=15 if flow_ok else 0
    score+=5 if candle_ok else 0
    score+=5 if volume_ok else 0

    mandatory=price_ok and spread_ok and one_ok and three_ok and flow_ok
    state="READY" if mandatory and score>=80 else "WAIT"
    reasons=[]
    if not price_ok: reasons.append("price outside exact entry corridor")
    if not spread_ok: reasons.append(f"spread {spread:.1f}bps > {max_spread:.1f}")
    if not one_ok: reasons.append("1m micro-trend not confirmed")
    if not three_ok: reasons.append("3m direction not confirmed")
    if not flow_ok: reasons.append("aggressive taker flow not confirmed")
    if not candle_ok: reasons.append("last 1m candle quality weak")
    if not volume_ok: reasons.append("micro volume below setup requirement")
    if not reasons:
        reasons=["persistent micro confirmation ready"]

    return TriggerAssessment(
        state,float(score),"; ".join(reasons[:3]),px,bid,ask,spread,distance,
        bool(one_ok),bool(three_ok),bool(flow_ok),float(flow_share),flow_source,
        bool(candle_ok),float(f1["volume_ratio"]),time.time()
    )


async def assess_row(row):
    symbol=str(row["symbol"]).upper()
    f1,f3=await asyncio.gather(
        get_klines(symbol,"1m",80),
        get_klines(symbol,"3m",80),
    )
    return evaluate(
        row,f1,f3,
        px=live_price(symbol,20),
        bk=live_book(symbol,20),
        flow_row=live_flow(symbol,60,20),
    )


async def assess_signal(signal):
    return await assess_row(row_from_signal(signal))


def record_check(arm_id,assessment,min_confirm_gap_sec=20):
    """Persist state and return the new confirmation streak."""
    init(); now=float(assessment.checked_at or time.time())
    with _db() as c:
        row=c.execute("""
            SELECT status,last_state,last_check_at,confirm_streak
            FROM v1142_armed WHERE id=?
        """,(int(arm_id),)).fetchone()
        if not row or row["status"]!="ACTIVE":
            return 0
        streak=int(row["confirm_streak"] or 0)
        last_check=float(row["last_check_at"] or 0)
        if assessment.state=="READY":
            if row["last_state"]=="READY":
                if now-last_check>=float(min_confirm_gap_sec):
                    streak+=1
            else:
                streak=1
        else:
            streak=0

        status="CANCELLED" if assessment.state=="CANCEL" else "ACTIVE"
        c.execute("""
            UPDATE v1142_armed
            SET status=?,last_state=?,last_score=?,last_reason=?,last_price=?,
                last_check_at=?,confirm_streak=?
            WHERE id=?
        """,(
            status,assessment.state,float(assessment.score),assessment.reason,
            float(assessment.price),now,streak,int(arm_id)
        ))
        return streak


def mark_pending_delivery(arm_id,signal_id):
    init(); now=time.time()
    with _db() as c:
        c.execute("""
            UPDATE v1142_armed
            SET status='PENDING_DELIVERY',last_state='ENTRY_PENDING_DELIVERY',
                triggered_signal_id=?,last_check_at=?,
                last_reason='ENTRY NOW confirmed; Telegram delivery pending'
            WHERE id=? AND status='ACTIVE' AND release_key=?
        """,(int(signal_id),now,int(arm_id),FUTURES_RELEASE_KEY))




def mark_delivery_uncertain(arm_id,signal_id,reason="Telegram delivery outcome uncertain"):
    init(); now=time.time()
    with _db() as c:
        c.execute("""
            UPDATE v1142_armed
            SET status='TRIGGERED',last_state='ENTRY_DELIVERY_UNCERTAIN',
                last_reason=?,triggered_at=?,triggered_signal_id=?,last_check_at=?
            WHERE id=? AND status IN ('ACTIVE','PENDING_DELIVERY')
              AND release_key=?
        """,(
            str(reason)[:300],now,int(signal_id),now,int(arm_id),FUTURES_RELEASE_KEY
        ))


def mark_triggered(arm_id,signal_id=None):
    init(); now=time.time()
    with _db() as c:
        c.execute("""
            UPDATE v1142_armed
            SET status='TRIGGERED',last_state='ENTER_NOW',
                triggered_at=?,triggered_signal_id=?,last_check_at=?
            WHERE id=? AND status IN ('ACTIVE','PENDING_DELIVERY')
              AND release_key=?
        """,(now,int(signal_id) if signal_id is not None else None,now,int(arm_id),FUTURES_RELEASE_KEY))




def mark_shadowed(arm_id,shadow_id=None,reason="safety shadow"):
    init(); now=time.time()
    with _db() as c:
        c.execute("""
            UPDATE v1142_armed
            SET status='SHADOWED',last_state='SHADOW',
                last_reason=?,triggered_at=?,
                triggered_signal_id=?,last_check_at=?
            WHERE id=? AND status='ACTIVE'
        """,(
            str(reason),now,
            int(shadow_id) if shadow_id is not None else None,
            now,int(arm_id)
        ))


def cancel(arm_id,reason="cancelled"):
    init(); now=time.time()
    with _db() as c:
        c.execute("""
            UPDATE v1142_armed
            SET status='CANCELLED',last_state='CANCEL',
                last_reason=?,last_check_at=?
            WHERE id=? AND status IN ('ACTIVE','PENDING_DELIVERY')
        """,(str(reason),now,int(arm_id)))


def recent_status(limit=8):
    init()
    with _db() as c:
        rows=c.execute("""
            SELECT symbol,timeframe,side,setup_type,source_pro,status,last_state,
                   last_score,last_reason,last_price,confirm_streak,armed_at,expires_at
            FROM v1142_armed
            ORDER BY id DESC LIMIT ?
        """,(int(limit),)).fetchall()
    return [dict(r) for r in rows]


def status_text():
    rows=recent_status(8)
    if not rows:
        return "🚨 <b>ENTRY NOW</b>\n━━━━━━━━━━━━━━━━━━\nАктивных/недавних кандидатов пока нет."
    lines=["🚨 <b>ENTRY NOW · FUTURES</b>","━━━━━━━━━━━━━━━━━━"]
    now=time.time()
    for row in rows:
        state=str(row.get("last_state") or row.get("status") or "ARMED")
        icon=(
            "🚨" if state=="ENTER_NOW" else
            ("🧪" if state=="SHADOW" else
             ("🟡" if row["status"]=="ACTIVE" else "⚪"))
        )
        ttl=max(0,int((float(row["expires_at"])-now)/60)) if row["status"]=="ACTIVE" else 0
        lines.append(
            f"{icon} <b>{row['symbol']} {row['side']} {row['timeframe']}</b> · "
            f"{state} · readiness {float(row.get('last_score') or 0):.0f}/100"
            + (f" · streak {int(row.get('confirm_streak') or 0)}/2+ · TTL {ttl}m"
               if row["status"]=="ACTIVE" else "")
        )
        if row.get("last_reason"):
            lines.append(f"└ {str(row['last_reason'])[:150]}")
    lines += [
        "",
        "Авто-сигнал отправляется после <b>минимум 2 последовательных READY-проверок</b>; "
        "защитные режимы V11.18 требуют 3, затем выполняется полный Production revalidation.",
        "Readiness — не вероятность прибыли.",
    ]
    return "\n".join(lines)
