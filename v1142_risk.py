"""Futures safety governor for V11.7.1.

Why this exists:
- a sequence of losses must not be answered by "try again with the same confidence";
- a materially new ENTRY NOW trigger should prove itself in forward shadow data
  before real Production delivery;
- once a signal is delivered as ENTRY NOW, it becomes an immutable tracked trade.

This module never places exchange orders.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import DATABASE_PATH
from v11_live import price as live_price

CANARY_REASON="V1151_CANARY_SHADOW"
CIRCUIT_REASON="V1151_CIRCUIT_SHADOW"
MIN_RECOVERY_CLOSED=5
MIN_RECOVERY_WINS=3
MIN_RECOVERY_NET_R=0.0
MIN_RECOVERY_PF=1.20
MAX_CONSECUTIVE_LIVE_LOSSES=3
MAX_CONCURRENT_LIVE=1
V11180_MAX_CONCURRENT_LIVE=2
ROLLING_DRAWDOWN_WINDOW=5
ROLLING_DRAWDOWN_MIN_TRADES=4
ROLLING_DRAWDOWN_PAUSE_R=-2.0
MIN_PROBE_DISTINCT_SYMBOLS=3
MIN_PROBE_SPAN_HOURS=2.0


@dataclass(frozen=True)
class SafetyStatus:
    mode:str
    allow_live:bool
    reason:str
    consecutive_losses:int
    live_closed:int
    active_live:int
    probe_reason:str
    probe_closed:int
    probe_wins:int
    probe_net_r:float
    probe_pf:float
    paused_at:str|None
    baseline_signal_id:int
    probe_distinct_symbols:int=0
    probe_span_hours:float=0.0
    rolling_net_r:float=0.0


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
            CREATE TABLE IF NOT EXISTS v1142_safety(
                id INTEGER PRIMARY KEY CHECK(id=1),
                canary_passed INTEGER NOT NULL DEFAULT 0,
                paused_at TEXT,
                pause_reason TEXT,
                baseline_signal_id INTEGER NOT NULL DEFAULT 0,
                probe_baseline_id INTEGER NOT NULL DEFAULT 0,
                release_key TEXT,
                delivery_bootstrap_key TEXT,
                resumed_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cols={r[1] for r in c.execute("PRAGMA table_info(v1142_safety)")}
        had_release_key="release_key" in cols
        had_bootstrap_key="delivery_bootstrap_key" in cols
        if "probe_baseline_id" not in cols:
            c.execute(
                "ALTER TABLE v1142_safety ADD COLUMN probe_baseline_id INTEGER NOT NULL DEFAULT 0"
            )
        if "release_key" not in cols:
            c.execute("ALTER TABLE v1142_safety ADD COLUMN release_key TEXT")
        if "delivery_bootstrap_key" not in cols:
            c.execute("ALTER TABLE v1142_safety ADD COLUMN delivery_bootstrap_key TEXT")
        c.execute("""
            INSERT OR IGNORE INTO v1142_safety(
                id,canary_passed,baseline_signal_id,probe_baseline_id,
                release_key,delivery_bootstrap_key
            ) VALUES(1,0,0,0,NULL,NULL)
        """)
        baseline_release="11.7.1"
        bootstrap_release="11.18.1-signal-delivery"
        state=c.execute(
            "SELECT release_key,delivery_bootstrap_key FROM v1142_safety WHERE id=1"
        ).fetchone()
        release=str(state[0] or "") if state else ""
        bootstrap=str(state[1] or "") if state else ""
        max_signal=int(c.execute("SELECT COALESCE(MAX(id),0) FROM signals").fetchone()[0] or 0)
        max_prod=int(c.execute("""
            SELECT COALESCE(MAX(id),0) FROM signals
            WHERE COALESCE(is_shadow,0)=0
              AND COALESCE(delivery_state,'DELIVERED') IN ('DELIVERED','UNCERTAIN')
        """).fetchone()[0] or 0)

        # Compatibility migration:
        # - keep the original V11.7.1 release_key contract intact;
        # - grant the Telegram signal product a one-time V11.18 delivery bootstrap;
        # - preserve the old-schema regression contract (old schemas still reset to CANARY).
        if release==bootstrap_release:
            # Transitional value written by the first V11.18 signal-flow hotfix.
            c.execute("""
                UPDATE v1142_safety
                SET canary_passed=1,paused_at=NULL,pause_reason=NULL,
                    baseline_signal_id=?,probe_baseline_id=?,release_key=?,
                    delivery_bootstrap_key=?,resumed_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=1
            """,(max_prod,max_signal,baseline_release,bootstrap_release))
        elif release!=baseline_release:
            is_brand_new=(not release and had_release_key)
            c.execute("""
                UPDATE v1142_safety
                SET canary_passed=?,paused_at=NULL,pause_reason=NULL,
                    baseline_signal_id=?,probe_baseline_id=?,release_key=?,
                    delivery_bootstrap_key=?,resumed_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=1
            """,(
                1 if is_brand_new else 0,
                max_prod,max_signal,baseline_release,bootstrap_release,
            ))
        elif bootstrap!=bootstrap_release:
            # Existing V11.7.1 deployment: unblock initial hidden CANARY once.
            c.execute("""
                UPDATE v1142_safety
                SET canary_passed=1,paused_at=NULL,pause_reason=NULL,
                    baseline_signal_id=?,probe_baseline_id=?,
                    delivery_bootstrap_key=?,resumed_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=1
            """,(max_prod,max_signal,bootstrap_release))

def _state():
    init()
    with _db() as c:
        row=c.execute("SELECT * FROM v1142_safety WHERE id=1").fetchone()
    return dict(row)


def _production_rows(after_id=0,limit=50):
    with _db() as c:
        rows=c.execute("""
            SELECT id,closed_at,result,pnl_r,release_version
            FROM signals
            WHERE id>?
              AND status='CLOSED'
              AND activated_at IS NOT NULL
              AND COALESCE(is_shadow,0)=0
              AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
              AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
              AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
              AND pnl_r IS NOT NULL
            ORDER BY COALESCE(closed_at,created_at) DESC,id DESC
            LIMIT ?
        """,(int(after_id),int(limit))).fetchall()
    return [dict(r) for r in rows]




def _max_signal_id():
    with _db() as c:
        row=c.execute("SELECT COALESCE(MAX(id),0) FROM signals").fetchone()
    return int(row[0] or 0)


def _max_production_id():
    with _db() as c:
        row=c.execute("""
            SELECT COALESCE(MAX(id),0) FROM signals
            WHERE COALESCE(is_shadow,0)=0
              AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
        """).fetchone()
    return int(row[0] or 0)


def _live_closed_v1142():
    with _db() as c:
        row=c.execute("""
            SELECT COUNT(*) FROM signals
            WHERE status='CLOSED'
              AND activated_at IS NOT NULL
              AND COALESCE(is_shadow,0)=0
              AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
              AND COALESCE(release_version,'') LIKE '11.7.1%'
              AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
              AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
              AND pnl_r IS NOT NULL
        """).fetchone()
    return int(row[0] or 0)


def active_live_count():
    with _db() as c:
        row=c.execute("""
            SELECT COUNT(*) FROM signals
            WHERE COALESCE(is_shadow,0)=0
              AND (
                    COALESCE(delivery_state,'DELIVERED') IN ('DELIVERED','UNCERTAIN')
                    OR (
                        COALESCE(delivery_state,'')='PENDING'
                        AND COALESCE(release_version,'') LIKE '11.7.1%'
                    )
              )
              AND status IN ('PENDING_DELIVERY','SENT','WAITING','ACTIVE','OPEN')
        """).fetchone()
    return int(row[0] or 0)


def _loss_streak(rows):
    streak=0
    for row in rows:
        pnl=float(row.get("pnl_r") or 0)
        if pnl<0:
            streak+=1
        else:
            break
    return streak


def _profit_factor(values):
    gains=sum(x for x in values if x>0)
    losses=-sum(x for x in values if x<0)
    if losses>0:
        return gains/losses
    return 999.0 if gains>0 else 0.0


def probe_stats(reason,after_id=0,latest=5):
    where=[
        "COALESCE(is_shadow,0)=1",
        "shadow_reason=?",
        "status='CLOSED'",
        "activated_at IS NOT NULL",
        "result NOT IN ('ENTRY_EXPIRED','INVALIDATED')",
        "COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'",
        "pnl_r IS NOT NULL",
    ]
    args=[str(reason)]
    if int(after_id or 0)>0:
        where.append("id>?")
        args.append(int(after_id))
    args.append(max(int(latest),MIN_RECOVERY_CLOSED))
    with _db() as c:
        rows=c.execute(f"""
            SELECT id,pnl_r,closed_at,created_at,symbol
            FROM signals
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(closed_at,created_at) DESC,id DESC
            LIMIT ?
        """,args).fetchall()
    vals=[float(r["pnl_r"] or 0) for r in rows]
    symbols={str(r["symbol"] or "").upper() for r in rows if r["symbol"]}
    times=[]
    for r in rows:
        raw=r["created_at"]
        if raw:
            try:
                times.append(datetime.fromisoformat(str(raw).replace("Z","+00:00")).timestamp())
            except Exception:
                pass
    span_hours=((max(times)-min(times))/3600.0) if len(times)>=2 else 0.0
    return {
        "closed":len(vals),
        "wins":sum(1 for x in vals if x>0),
        "net_r":sum(vals),
        "pf":_profit_factor(vals),
        "values":vals,
        "distinct_symbols":len(symbols),
        "span_hours":span_hours,
    }


def _probe_passed(stats):
    return (
        int(stats["closed"])>=MIN_RECOVERY_CLOSED
        and int(stats["wins"])>=MIN_RECOVERY_WINS
        and float(stats["net_r"])>MIN_RECOVERY_NET_R
        and float(stats["pf"])>=MIN_RECOVERY_PF
        and int(stats.get("distinct_symbols",0))>=MIN_PROBE_DISTINCT_SYMBOLS
        and float(stats.get("span_hours",0))>=MIN_PROBE_SPAN_HOURS
        and not (
            len(stats["values"])>=2
            and stats["values"][0]<0
            and stats["values"][1]<0
        )
    )


def pause(reason,baseline_signal_id=None):
    init()
    now=datetime.now(timezone.utc).isoformat()
    baseline=_max_production_id() if baseline_signal_id is None else int(baseline_signal_id)
    probe_baseline=_max_signal_id()
    with _db() as c:
        c.execute("""
            UPDATE v1142_safety
            SET paused_at=COALESCE(paused_at,?),
                pause_reason=?,
                baseline_signal_id=max(baseline_signal_id,?),
                probe_baseline_id=CASE
                    WHEN paused_at IS NULL THEN ?
                    ELSE probe_baseline_id
                END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=1
        """,(now,str(reason),baseline,probe_baseline))


def _pass_canary():
    baseline=_max_production_id()
    with _db() as c:
        c.execute("""
            UPDATE v1142_safety
            SET canary_passed=1,baseline_signal_id=?,
                probe_baseline_id=?,
                resumed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
        """,(baseline,_max_signal_id()))


def _resume_from_pause():
    baseline=_max_production_id()
    with _db() as c:
        c.execute("""
            UPDATE v1142_safety
            SET paused_at=NULL,pause_reason=NULL,
                baseline_signal_id=?,probe_baseline_id=?,
                resumed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
        """,(baseline,_max_signal_id()))



def effective_max_concurrent_live():
    """V11.7 baseline stays at one; V11.18 signal delivery may track two live ideas."""
    return int(V11180_MAX_CONCURRENT_LIVE)

def status():
    state=_state()

    if not int(state.get("canary_passed") or 0):
        probe=probe_stats(CANARY_REASON,int(state.get("probe_baseline_id") or 0),MIN_RECOVERY_CLOSED)
        if _probe_passed(probe):
            _pass_canary()
            state=_state()
        else:
            return SafetyStatus(
                "CANARY",False,
                "new ENTRY NOW engine is proving itself on forward shadow signals",
                0,_live_closed_v1142(),active_live_count(),
                CANARY_REASON,int(probe["closed"]),int(probe["wins"]),
                float(probe["net_r"]),float(probe["pf"]),
                None,int(state.get("baseline_signal_id") or 0),
                int(probe.get("distinct_symbols",0)),float(probe.get("span_hours",0)),
            )

    paused_at=state.get("paused_at")
    baseline=int(state.get("baseline_signal_id") or 0)
    rows=_production_rows(baseline,50)
    streak=_loss_streak(rows)
    rolling_values=[float(r.get("pnl_r") or 0) for r in rows[:ROLLING_DRAWDOWN_WINDOW]]
    rolling_net_r=sum(rolling_values)

    if paused_at:
        probe=probe_stats(
            CIRCUIT_REASON,int(state.get("probe_baseline_id") or 0),
            MIN_RECOVERY_CLOSED
        )
        if _probe_passed(probe):
            _resume_from_pause()
            state=_state()
            baseline=int(state.get("baseline_signal_id") or 0)
            rows=_production_rows(baseline,50)
            streak=_loss_streak(rows)
            rolling_values=[float(r.get("pnl_r") or 0) for r in rows[:ROLLING_DRAWDOWN_WINDOW]]
            rolling_net_r=sum(rolling_values)
        else:
            return SafetyStatus(
                "CIRCUIT_PAUSE",False,
                str(state.get("pause_reason") or "loss circuit breaker active"),
                streak,_live_closed_v1142(),active_live_count(),
                CIRCUIT_REASON,int(probe["closed"]),int(probe["wins"]),
                float(probe["net_r"]),float(probe["pf"]),
                str(paused_at),baseline,
                int(probe.get("distinct_symbols",0)),float(probe.get("span_hours",0)),
                float(rolling_net_r),
            )

    if streak>=MAX_CONSECUTIVE_LIVE_LOSSES:
        pause(
            f"{streak} consecutive delivered Production losses",
            baseline_signal_id=baseline,
        )
        return status()
    if (
        len(rolling_values)>=ROLLING_DRAWDOWN_MIN_TRADES
        and rolling_net_r<=ROLLING_DRAWDOWN_PAUSE_R
    ):
        pause(
            f"rolling {len(rolling_values)}-trade net {rolling_net_r:+.2f}R <= "
            f"{ROLLING_DRAWDOWN_PAUSE_R:+.2f}R",
            baseline_signal_id=baseline,
        )
        return status()

    active=active_live_count()
    if active>=effective_max_concurrent_live():
        return SafetyStatus(
            "POSITION_BUSY",False,
            f"{active} live Futures trade already active; no stacking",
            streak,_live_closed_v1142(),active,
            "",0,0,0.0,0.0,None,baseline,0,0.0,float(rolling_net_r),
        )

    return SafetyStatus(
        "LIVE",True,"live entry permitted",
        streak,_live_closed_v1142(),active,
        "",0,0,0.0,0.0,None,baseline,0,0.0,float(rolling_net_r),
    )


def shadow_reason_for(status_row):
    if status_row.mode=="CANARY":
        return CANARY_REASON
    if status_row.mode=="CIRCUIT_PAUSE":
        return CIRCUIT_REASON
    return None




def active_trades_text():
    """Immutable view of every delivered/current Futures trade, including legacy releases."""
    with _db() as c:
        try:
            rows=c.execute("""
                SELECT s.id,s.symbol,s.timeframe,s.side,s.entry,s.stop,s.tp1,s.tp2,s.tp3,
                       s.status,s.activated_at,s.delivered_at,s.created_at,s.delivery_state,
                       l.last_event,l.max_r,l.structure_warned
                FROM signals s
                LEFT JOIN v11_lifecycle l ON l.signal_id=s.id
                WHERE COALESCE(s.is_shadow,0)=0
                  AND COALESCE(s.delivery_state,'DELIVERED') IN ('DELIVERED','UNCERTAIN')
                  AND s.status IN ('SENT','WAITING','ACTIVE','OPEN')
                ORDER BY s.id DESC
            """).fetchall()
        except sqlite3.OperationalError:
            rows=c.execute("""
                SELECT id,symbol,timeframe,side,entry,stop,tp1,tp2,tp3,
                       status,activated_at,delivered_at,created_at,delivery_state,
                       NULL AS last_event,NULL AS max_r,0 AS structure_warned
                FROM signals
                WHERE COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND status IN ('SENT','WAITING','ACTIVE','OPEN')
                ORDER BY id DESC
            """).fetchall()

    if not rows:
        return (
            "📍 <b>ACTIVE FUTURES</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Сейчас нет активной Futures Production-сделки."
        )

    lines=[
        "📍 <b>ACTIVE FUTURES · IMMUTABLE</b>",
        "━━━━━━━━━━━━━━━━━━",
        "После 🚨 ENTRY NOW сделка остаётся здесь до явного закрывающего события.",
    ]
    for r in rows:
        row=dict(r)
        px=live_price(row["symbol"],30)
        entry=float(row["entry"]); stop=float(row["stop"])
        risk=abs(entry-stop)
        r_now=None
        if px is not None and risk>0:
            r_now=((px-entry) if row["side"]=="LONG" else (entry-px))/risk
        warned=bool(int(row.get("structure_warned") or 0))
        uncertain=str(row.get("delivery_state") or "").upper()=="UNCERTAIN"
        manager=(
            "⚠️ DELIVERY UNCERTAIN · ASSUME POTENTIALLY RECEIVED"
            if uncertain else
            ("⚠️ RISK REVIEW"
             if warned else
             ("🟢 HOLD / PLAN INTACT" if row["status"]=="ACTIVE" else "🟡 DELIVERY/ACTIVATION SYNC"))
        )
        lines += [
            "",
            f"<b>#{row['id']} · {row['symbol']} {row['side']} {row['timeframe']}</b>",
            f"State: <b>{row['status']}</b> · Manager: <b>{manager}</b>",
            f"ENTRY <b>{entry:.8g}</b> · SL <b>{float(row['stop']):.8g}</b>",
            f"TP1 <b>{float(row['tp1']):.8g}</b> · TP2 <b>{float(row['tp2']):.8g}</b> · "
            f"TP3 <b>{float(row['tp3']):.8g}</b>",
        ]
        if px is not None:
            lines.append(
                f"Live <b>{float(px):.8g}</b>"
                + (f" · <b>{r_now:+.2f}R</b>" if r_now is not None else "")
            )
        if uncertain:
            lines.append(
                "⚠️ Telegram подтвердить доставку не удалось. Повтор не отправляется; "
                "бот консервативно считает, что вход мог быть получен, и держит риск-слот."
            )
        if row.get("last_event"):
            lines.append(f"Last lifecycle event: <b>{row['last_event']}</b>")
    lines += [
        "",
        "Уровни исходного сигнала не переписываются задним числом. "
        "Новый скан не может удалить уже активированную сделку — она не исчезает.",
    ]
    return "\n".join(lines)


def text():
    s=status()
    icon={
        "LIVE":"🟢","CANARY":"🧪","CIRCUIT_PAUSE":"🛑","POSITION_BUSY":"🟡"
    }.get(s.mode,"⚪")
    lines=[
        f"{icon} <b>FUTURES SAFETY · {s.mode}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"New live entry: <b>{'ALLOWED' if s.allow_live else 'BLOCKED'}</b>",
        f"Reason: {s.reason}",
        f"Consecutive delivered losses: <b>{s.consecutive_losses}</b> / "
        f"{MAX_CONSECUTIVE_LIVE_LOSSES}",
        f"Active live trades: <b>{s.active_live}</b> / {effective_max_concurrent_live()}",
        f"Rolling live PnL: <b>{s.rolling_net_r:+.2f}R</b> / pause at {ROLLING_DRAWDOWN_PAUSE_R:+.2f}R",
    ]
    if s.probe_reason:
        lines += [
            "",
            f"Shadow recovery: <b>{s.probe_closed}/{MIN_RECOVERY_CLOSED}</b> closed · "
            f"wins <b>{s.probe_wins}</b>",
            f"Net <b>{s.probe_net_r:+.2f}R</b> · PF <b>{s.probe_pf:.2f}</b>",
            f"Diversity <b>{s.probe_distinct_symbols}/{MIN_PROBE_DISTINCT_SYMBOLS}</b> symbols · "
            f"span <b>{s.probe_span_hours:.1f}/{MIN_PROBE_SPAN_HOURS:.0f}h</b>",
            "Promotion requires ≥3/5 wins, positive net R, PF ≥1.20, "
            "≥3 symbols, ≥2h span and no two newest probes both losing.",
        ]
    lines += [
        "",
        "После 🚨 ENTRY NOW уже отправленная сделка не исчезает: она остаётся "
        "в ACTIVE до явного TP/SL/expiry lifecycle event.",
    ]
    return "\n".join(lines)
