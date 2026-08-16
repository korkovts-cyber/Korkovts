import json
import os
import sqlite3
from datetime import datetime, timezone

from .config import (
    APP_VERSION,
    DAILY_STOP_R,
    DATABASE_PATH,
    DEFAULT_RISK_PCT,
    MAX_OPEN_SIGNALS,
    MAX_PORTFOLIO_RISK_PCT,
    MAX_RISK_PCT,
    ROUND_TRIP_COST_PCT,
    STRATEGY_VERSION,
)
from .risk import conservative_plan

EXTRA_SIGNAL_COLUMNS={
    "closed_at":"TEXT", "result":"TEXT", "exit_price":"REAL", "pnl_r":"REAL",
    "last_checked_at":"TEXT", "max_favorable_r":"REAL DEFAULT 0",
    "max_adverse_r":"REAL DEFAULT 0", "strategy_version":"TEXT DEFAULT 'legacy'",
    "activated_at":"TEXT", "source_chat_id":"INTEGER", "setup_type":"TEXT",
    "feature_json":"TEXT", "market_regime":"TEXT", "adl_risk":"TEXT",
    "cluster_id":"INTEGER", "release_version":"TEXT",
    "is_shadow":"INTEGER NOT NULL DEFAULT 0", "shadow_reason":"TEXT",
    "delivery_state":"TEXT NOT NULL DEFAULT 'DELIVERED'", "delivered_at":"TEXT"
}

def init():
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".",exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        c.execute('''CREATE TABLE IF NOT EXISTS signals(
        id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        symbol TEXT,timeframe TEXT,side TEXT,score REAL,entry REAL,stop REAL,tp1 REAL,tp2 REAL,tp3 REAL,status TEXT DEFAULT "OPEN")''')
        c.execute('''CREATE TABLE IF NOT EXISTS subscribers(
        chat_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS risk_profiles(
        chat_id INTEGER PRIMARY KEY,balance REAL,risk_pct REAL NOT NULL DEFAULT 0.5,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS paper_accounts(
        chat_id INTEGER PRIMARY KEY,initial_balance REAL NOT NULL,balance REAL NOT NULL,
        peak_balance REAL NOT NULL,max_drawdown REAL NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS paper_trades(
        id INTEGER PRIMARY KEY,chat_id INTEGER NOT NULL,signal_id INTEGER NOT NULL,
        symbol TEXT,side TEXT,state TEXT DEFAULT 'WAITING',entry REAL,stop REAL,qty REAL,
        notional REAL,risk_budget REAL,activated_at TEXT,closed_at TEXT,result TEXT,
        exit_price REAL,realized_pnl REAL DEFAULT 0,UNIQUE(chat_id,signal_id))''')
        existing={r[1] for r in c.execute("PRAGMA table_info(signals)")}
        for name,definition in EXTRA_SIGNAL_COLUMNS.items():
            if name not in existing:
                c.execute(f"ALTER TABLE signals ADD COLUMN {name} {definition}")
        # Rows from releases before the durable outbox were already delivered.
        c.execute("""UPDATE signals SET delivery_state='DELIVERED'
            WHERE delivery_state IS NULL OR delivery_state=''""")
        c.execute("""UPDATE signals SET delivered_at=COALESCE(delivered_at,created_at)
            WHERE COALESCE(is_shadow,0)=0 AND delivery_state='DELIVERED'""")
        c.execute('''CREATE TABLE IF NOT EXISTS signal_deliveries(
        id INTEGER PRIMARY KEY,signal_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,
        payload TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,delivered_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,
        UNIQUE(signal_id,chat_id),FOREIGN KEY(signal_id) REFERENCES signals(id))''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_signal_deliveries_pending
            ON signal_deliveries(delivered_at,created_at)''')

def _insert_signal(s,chat_id=None,shadow_reason=None,status="SENT",
                   delivery_state="DELIVERED",delivered_at=None):
    feature_json=json.dumps(getattr(s,"feature_snapshot",{}) or {},ensure_ascii=False,
                            separators=(",",":"),default=str)
    regime=(getattr(s,"market_context",{}) or {}).get("bias")
    with sqlite3.connect(DATABASE_PATH) as c:
        cur=c.execute("""INSERT INTO signals(symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,
                      last_checked_at,strategy_version,status,source_chat_id,setup_type,feature_json,
                      market_regime,adl_risk,cluster_id,release_version,is_shadow,shadow_reason,
                      delivery_state,delivered_at)
                      VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (s.symbol,s.timeframe,s.side,s.score,s.entry_high if s.side=='LONG' else s.entry_low,
                       s.stop,s.tp1,s.tp2,s.tp3,STRATEGY_VERSION,status,
                       int(chat_id) if chat_id is not None else None,
                       getattr(s,"setup_type",None),feature_json,regime,getattr(s,"adl_risk","unknown"),
                       int(getattr(s,"cluster_id",0) or 0),APP_VERSION,1 if shadow_reason else 0,
                       shadow_reason,delivery_state,delivered_at))
        return cur.lastrowid

def save(s,chat_id=None,shadow_reason=None):
    delivered_at=datetime.now(timezone.utc).isoformat()
    return _insert_signal(s,chat_id,shadow_reason,"SENT","DELIVERED",delivered_at)

def save_pending(s):
    """Persist an automatic signal before sending without marking it delivered."""
    return _insert_signal(s,status="PENDING_DELIVERY",delivery_state="PENDING")

def save_shadow(s,reason):
    return _insert_signal(s,None,str(reason),"SENT","SHADOW")

def enqueue_delivery(signal_id,chat_id,payload):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''INSERT OR IGNORE INTO signal_deliveries(signal_id,chat_id,payload)
            VALUES(?,?,?)''',(int(signal_id),int(chat_id),str(payload)))
        row=c.execute("SELECT id FROM signal_deliveries WHERE signal_id=? AND chat_id=?",
                      (int(signal_id),int(chat_id))).fetchone()
    return int(row[0])

def pending_deliveries(limit=100):
    """Return recent undelivered notifications for currently enabled chats."""
    with sqlite3.connect(DATABASE_PATH) as c:
        return c.execute('''SELECT d.id,d.signal_id,d.chat_id,d.payload,d.attempts,sig.symbol
            FROM signal_deliveries d
            JOIN signals sig ON sig.id=d.signal_id
            JOIN subscribers sub ON sub.chat_id=d.chat_id AND sub.enabled=1
            WHERE d.delivered_at IS NULL
            AND sig.created_at>=datetime('now','-24 hours')
            AND COALESCE(sig.delivery_state,'DELIVERED') IN ('PENDING','DELIVERED')
            ORDER BY d.id LIMIT ?''',(int(limit),)).fetchall()

def mark_delivery_sent(delivery_id):
    delivered_at=datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DATABASE_PATH) as c:
        row=c.execute("SELECT signal_id,chat_id FROM signal_deliveries WHERE id=?",
                      (int(delivery_id),)).fetchone()
        if not row:
            return
        signal_id,chat_id=row
        c.execute('''UPDATE signal_deliveries SET delivered_at=?,attempts=attempts+1,
            last_error=NULL WHERE id=? AND delivered_at IS NULL''',(delivered_at,int(delivery_id)))
        c.execute('''UPDATE signals SET status=CASE WHEN status='PENDING_DELIVERY' THEN 'SENT' ELSE status END,
            delivery_state='DELIVERED',delivered_at=COALESCE(delivered_at,?),
            source_chat_id=COALESCE(source_chat_id,?) WHERE id=?''',
            (delivered_at,int(chat_id),int(signal_id)))

def mark_delivery_failed(delivery_id,error):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''UPDATE signal_deliveries SET attempts=attempts+1,last_error=?
            WHERE id=? AND delivered_at IS NULL''',(str(error)[:500],int(delivery_id)))

def expire_pending_deliveries(hours=24):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''UPDATE signals SET status='DELIVERY_FAILED',delivery_state='FAILED'
            WHERE status='PENDING_DELIVERY' AND created_at<datetime('now','-5 minutes')
            AND NOT EXISTS(SELECT 1 FROM signal_deliveries d WHERE d.signal_id=signals.id)''')
        c.execute('''UPDATE signal_deliveries SET last_error=COALESCE(last_error,'delivery expired')
            WHERE delivered_at IS NULL AND created_at<datetime('now',?)''',(f'-{int(hours)} hours',))
        c.execute('''UPDATE signals SET status='DELIVERY_FAILED',delivery_state='FAILED'
            WHERE status='PENDING_DELIVERY' AND created_at<datetime('now',?)
            AND NOT EXISTS(SELECT 1 FROM signal_deliveries d
                           WHERE d.signal_id=signals.id AND d.delivered_at IS NOT NULL)''',
            (f'-{int(hours)} hours',))

def delivery_stats():
    with sqlite3.connect(DATABASE_PATH) as c:
        pending=c.execute('''SELECT COUNT(*) FROM signal_deliveries
            WHERE delivered_at IS NULL AND created_at>=datetime('now','-24 hours')''').fetchone()[0]
        failed=c.execute('''SELECT COUNT(*) FROM signals WHERE delivery_state='FAILED'
            AND created_at>=datetime('now','-7 days')''').fetchone()[0]
    return {"pending":pending,"failed_7d":failed}

def recent(n=10):
    with sqlite3.connect(DATABASE_PATH) as c:
        return c.execute("""SELECT created_at,symbol,timeframe,side,score,status,result,pnl_r,setup_type
            FROM signals WHERE COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
            ORDER BY id DESC LIMIT ?""",(n,)).fetchall()

def open_signals(n=500):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute('''SELECT id,created_at,activated_at,last_checked_at,status,symbol,timeframe,side,entry,stop,tp1,tp2,tp3,
            max_favorable_r,max_adverse_r,source_chat_id,is_shadow,shadow_reason FROM signals
            WHERE status IN ('SENT','WAITING','ACTIVE','OPEN')
            ORDER BY COALESCE(is_shadow,0),id LIMIT ?''',(n,))]

def activate_signal(signal_id,activated_at):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute("UPDATE signals SET status='ACTIVE',activated_at=?,last_checked_at=? WHERE id=? AND status IN ('SENT','WAITING','OPEN')",
                  (activated_at,activated_at,int(signal_id)))

def checkpoint(signal_id,checked_at,max_favorable_r,max_adverse_r):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''UPDATE signals SET last_checked_at=?,max_favorable_r=max(max_favorable_r,?),
            max_adverse_r=max(max_adverse_r,?) WHERE id=? AND status IN ('SENT','WAITING','ACTIVE','OPEN') ''',
            (checked_at,float(max_favorable_r),float(max_adverse_r),int(signal_id)))

def close_signal(signal_id,result,exit_price,pnl_r,closed_at,max_favorable_r,max_adverse_r):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''UPDATE signals SET status='CLOSED',result=?,exit_price=?,pnl_r=?,closed_at=?,last_checked_at=?,
            max_favorable_r=max(max_favorable_r,?),max_adverse_r=max(max_adverse_r,?)
            WHERE id=? AND status IN ('SENT','WAITING','ACTIVE','OPEN') ''',(result,float(exit_price),float(pnl_r),closed_at,closed_at,
            float(max_favorable_r),float(max_adverse_r),int(signal_id)))

def quality_stats(days=30):
    with sqlite3.connect(DATABASE_PATH) as c:
        row=c.execute('''SELECT COUNT(*),SUM(result='TP1'),SUM(result='TP2'),SUM(result='TP3'),
            SUM(result='SL'),SUM(result='EXPIRED'),AVG(pnl_r),SUM(pnl_r),AVG(max_adverse_r)
            FROM signals WHERE status='CLOSED' AND COALESCE(is_shadow,0)=0
            AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
            AND created_at>=datetime('now',?)''',(f'-{int(days)} days',)).fetchone()
        by_side=c.execute('''SELECT side,COUNT(*),AVG(pnl_r),SUM(result IN ('TP1','TP2','TP3'))*100.0/COUNT(*)
            FROM signals WHERE status='CLOSED' AND COALESCE(is_shadow,0)=0
            AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
            AND created_at>=datetime('now',?) GROUP BY side''',(f'-{int(days)} days',)).fetchall()
        open_count=c.execute("""SELECT COUNT(*) FROM signals WHERE status IN ('WAITING','ACTIVE','OPEN')
            AND COALESCE(is_shadow,0)=0""").fetchone()[0]
    return row,by_side,open_count

def calibration_penalty(symbol,side,timeframe,min_samples=100):
    """Only tightens the filter after enough forward results; never loosens it."""
    with sqlite3.connect(DATABASE_PATH) as c:
        row=c.execute('''SELECT COUNT(*),AVG(pnl_r),SUM(result IN ('TP1','TP2','TP3'))*100.0/COUNT(*)
            FROM signals WHERE status='CLOSED' AND COALESCE(is_shadow,0)=0
            AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
            AND strategy_version=? AND symbol=? AND side=? AND timeframe=?''',
            (STRATEGY_VERSION,symbol,side,timeframe)).fetchone()
        if not row or row[0]<min_samples:
            row=c.execute('''SELECT COUNT(*),AVG(pnl_r),SUM(result IN ('TP1','TP2','TP3'))*100.0/COUNT(*)
                FROM signals WHERE status='CLOSED' AND COALESCE(is_shadow,0)=0
                AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                AND strategy_version=? AND side=? AND timeframe=?''',
                (STRATEGY_VERSION,side,timeframe)).fetchone()
    count,avg_r,winrate=row or (0,0,0)
    if count<min_samples: return 0
    if (avg_r or 0)<-0.15 or (winrate or 0)<35: return 8
    if (avg_r or 0)<0 or (winrate or 0)<45: return 4
    return 0

def set_risk_profile(chat_id,balance,risk_pct=DEFAULT_RISK_PCT):
    balance=float(balance); risk_pct=float(risk_pct)
    if balance<=0: raise ValueError("Баланс должен быть больше нуля")
    if not 0.1<=risk_pct<=MAX_RISK_PCT:
        raise ValueError(f"Риск должен быть от 0.1% до {MAX_RISK_PCT:.1f}%")
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''INSERT INTO risk_profiles(chat_id,balance,risk_pct,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET balance=excluded.balance,risk_pct=excluded.risk_pct,
        updated_at=CURRENT_TIMESTAMP''',(int(chat_id),balance,risk_pct))

def get_risk_profile(chat_id):
    with sqlite3.connect(DATABASE_PATH) as c:
        row=c.execute("SELECT balance,risk_pct FROM risk_profiles WHERE chat_id=?",(int(chat_id),)).fetchone()
    return {"balance":row[0],"risk_pct":row[1]} if row else None

def _ensure_paper_account(c,chat_id):
    profile=c.execute("SELECT balance FROM risk_profiles WHERE chat_id=?",(int(chat_id),)).fetchone()
    initial=float(profile[0]) if profile else 1000.0
    c.execute('''INSERT OR IGNORE INTO paper_accounts(chat_id,initial_balance,balance,peak_balance)
        VALUES(?,?,?,?)''',(int(chat_id),initial,initial,initial))

def portfolio_allowed(chat_id,signal):
    with sqlite3.connect(DATABASE_PATH) as c:
        _ensure_paper_account(c,chat_id)
        balance=float(c.execute("SELECT balance FROM paper_accounts WHERE chat_id=?",(int(chat_id),)).fetchone()[0])
        profile=c.execute("SELECT risk_pct FROM risk_profiles WHERE chat_id=?",(int(chat_id),)).fetchone()
        risk_pct=float(profile[0]) if profile else DEFAULT_RISK_PCT
        opened=c.execute("SELECT side,risk_budget FROM paper_trades WHERE chat_id=? AND state IN ('WAITING','ACTIVE')",(int(chat_id),)).fetchall()
    if len(opened)>=MAX_OPEN_SIGNALS: return False,"достигнут лимит открытых сделок"
    if any(side==signal.side for side,_ in opened): return False,f"уже есть позиция {signal.side}; коррелирующий риск заблокирован"
    plan=conservative_plan(signal,balance,risk_pct,ROUND_TRIP_COST_PCT)
    total=sum(float(r or 0) for _,r in opened)+plan["actual_risk"]
    if balance<=0 or total/balance*100>MAX_PORTFOLIO_RISK_PCT:
        return False,f"совокупный риск превысит {MAX_PORTFOLIO_RISK_PCT:.1f}%"
    return True,"разрешено"

def register_paper_trade(chat_id,signal_id,signal):
    with sqlite3.connect(DATABASE_PATH) as c:
        _ensure_paper_account(c,chat_id)
        balance=float(c.execute("SELECT balance FROM paper_accounts WHERE chat_id=?",(int(chat_id),)).fetchone()[0])
        profile=c.execute("SELECT risk_pct FROM risk_profiles WHERE chat_id=?",(int(chat_id),)).fetchone()
        risk_pct=float(profile[0]) if profile else DEFAULT_RISK_PCT
        plan=conservative_plan(signal,balance,risk_pct,ROUND_TRIP_COST_PCT)
        c.execute('''INSERT OR IGNORE INTO paper_trades(chat_id,signal_id,symbol,side,entry,stop,qty,notional,risk_budget)
            VALUES(?,?,?,?,?,?,?,?,?)''',(int(chat_id),int(signal_id),signal.symbol,signal.side,plan["entry"],signal.stop,
            plan["qty"],plan["notional"],plan["actual_risk"]))

def activate_paper_trades(signal_id,activated_at):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute("UPDATE paper_trades SET state='ACTIVE',activated_at=? WHERE signal_id=? AND state='WAITING'",
                  (activated_at,int(signal_id)))

def paper_trade_chats(signal_id):
    with sqlite3.connect(DATABASE_PATH) as c:
        return [r[0] for r in c.execute("SELECT chat_id FROM paper_trades WHERE signal_id=?",(int(signal_id),))]

def settle_paper_trades(signal_id):
    with sqlite3.connect(DATABASE_PATH) as c:
        signal=c.execute("SELECT result,exit_price,closed_at FROM signals WHERE id=?",(int(signal_id),)).fetchone()
        if not signal: return []
        result,exit_price,closed_at=signal; settled=[]
        trades=c.execute('''SELECT id,chat_id,side,entry,qty FROM paper_trades
            WHERE signal_id=? AND state IN ('WAITING','ACTIVE')''',(int(signal_id),)).fetchall()
        for trade_id,chat_id,side,entry,qty in trades:
            if result=='ENTRY_EXPIRED': pnl=0.0
            else:
                gross=(float(exit_price)-entry)*qty if side=='LONG' else (entry-float(exit_price))*qty
                pnl=gross-(entry*qty*ROUND_TRIP_COST_PCT/100)
            c.execute('''UPDATE paper_trades SET state='CLOSED',closed_at=?,result=?,exit_price=?,realized_pnl=?
                WHERE id=?''',(closed_at,result,exit_price,pnl,trade_id))
            account=c.execute("SELECT balance,peak_balance,max_drawdown FROM paper_accounts WHERE chat_id=?",(chat_id,)).fetchone()
            balance=account[0]+pnl; peak=max(account[1],balance); drawdown=max(account[2],peak-balance)
            c.execute('''UPDATE paper_accounts SET balance=?,peak_balance=?,max_drawdown=?,updated_at=CURRENT_TIMESTAMP
                WHERE chat_id=?''',(balance,peak,drawdown,chat_id))
            settled.append((chat_id,pnl,balance))
    return settled

def paper_stats(chat_id):
    with sqlite3.connect(DATABASE_PATH) as c:
        _ensure_paper_account(c,chat_id)
        account=c.execute("SELECT initial_balance,balance,peak_balance,max_drawdown FROM paper_accounts WHERE chat_id=?",(int(chat_id),)).fetchone()
        summary=c.execute('''SELECT COUNT(*),SUM(realized_pnl>0),SUM(realized_pnl),
            SUM(CASE WHEN realized_pnl>0 THEN realized_pnl ELSE 0 END),
            -SUM(CASE WHEN realized_pnl<0 THEN realized_pnl ELSE 0 END)
            FROM paper_trades WHERE chat_id=? AND state='CLOSED' AND result!='ENTRY_EXPIRED' ''',(int(chat_id),)).fetchone()
        opened=c.execute("SELECT COUNT(*) FROM paper_trades WHERE chat_id=? AND state IN ('WAITING','ACTIVE')",(int(chat_id),)).fetchone()[0]
    return account,summary,opened

def reset_paper_account(chat_id,balance):
    balance=float(balance)
    if balance<=0: raise ValueError("Баланс должен быть больше нуля")
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute("DELETE FROM paper_trades WHERE chat_id=?",(int(chat_id),))
        c.execute('''INSERT INTO paper_accounts(chat_id,initial_balance,balance,peak_balance,max_drawdown,updated_at)
            VALUES(?,?,?,?,0,CURRENT_TIMESTAMP) ON CONFLICT(chat_id) DO UPDATE SET
            initial_balance=excluded.initial_balance,balance=excluded.balance,peak_balance=excluded.peak_balance,
            max_drawdown=0,updated_at=CURRENT_TIMESTAMP''',(int(chat_id),balance,balance,balance))

def daily_risk_guard():
    with sqlite3.connect(DATABASE_PATH) as c:
        rows=c.execute('''SELECT result,COALESCE(pnl_r,0) FROM signals WHERE status='CLOSED'
            AND COALESCE(is_shadow,0)=0 AND closed_at>=datetime('now','-24 hours')
            ORDER BY closed_at DESC''').fetchall()
        opened=c.execute("""SELECT COUNT(*) FROM signals WHERE status IN ('WAITING','ACTIVE','OPEN')
            AND COALESCE(is_shadow,0)=0""").fetchone()[0]
    total_r=sum(r[1] for r in rows)
    consecutive_sl=len(rows)>=2 and rows[0][0]=='SL' and rows[1][0]=='SL'
    locked=total_r<=DAILY_STOP_R or consecutive_sl or opened>=MAX_OPEN_SIGNALS
    if opened>=MAX_OPEN_SIGNALS: reason=f"уже открыто {opened} сигнала"
    elif consecutive_sl: reason="две убыточные сделки подряд"
    else: reason=f"результат за 24ч {total_r:+.2f}R"
    return {"locked":locked,"total_r":total_r,"closed":len(rows),"open":opened,
            "reason":reason if locked else "лимит не достигнут"}

def subscribe(chat_id,enabled=True):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''INSERT INTO subscribers(chat_id,enabled,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP''',
        (int(chat_id),1 if enabled else 0))

def subscribers():
    with sqlite3.connect(DATABASE_PATH) as c:
        return [r[0] for r in c.execute("SELECT chat_id FROM subscribers WHERE enabled=1").fetchall()]

def was_sent_recently(symbol,side,hours=24,timeframe=None):
    with sqlite3.connect(DATABASE_PATH) as c:
        if timeframe:
            return c.execute('''SELECT 1 FROM signals WHERE symbol=? AND side=? AND timeframe=?
            AND COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED') IN ('DELIVERED','PENDING')
            AND created_at>=datetime('now',?) LIMIT 1''',
            (symbol,side,timeframe,f'-{int(hours)} hours')).fetchone() is not None
        return c.execute('''SELECT 1 FROM signals WHERE symbol=? AND side=?
            AND COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED') IN ('DELIVERED','PENDING')
            AND created_at>=datetime('now',?) LIMIT 1''',
            (symbol,side,f'-{int(hours)} hours')).fetchone() is not None

def was_shadowed_recently(symbol,side,timeframe,reason,hours=24):
    with sqlite3.connect(DATABASE_PATH) as c:
        return c.execute('''SELECT 1 FROM signals WHERE symbol=? AND side=? AND timeframe=?
            AND COALESCE(is_shadow,0)=1 AND shadow_reason=?
            AND created_at>=datetime('now',?) LIMIT 1''',
            (symbol,side,timeframe,str(reason),f'-{int(hours)} hours')).fetchone() is not None

def signal_memory_stats():
    """Counts used to verify that the 24-hour memory survived a restart."""
    with sqlite3.connect(DATABASE_PATH) as c:
        total=c.execute("""SELECT COUNT(*) FROM signals WHERE COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'""").fetchone()[0]
        last_24h=c.execute("""SELECT COUNT(*) FROM signals WHERE COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
            AND created_at>=datetime('now','-24 hours')""").fetchone()[0]
        previous_24h=c.execute("""SELECT COUNT(*) FROM signals
            WHERE COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
            AND created_at>=datetime('now','-48 hours')
            AND created_at<datetime('now','-24 hours')""").fetchone()[0]
        shadow_24h=c.execute("""SELECT COUNT(*) FROM signals WHERE COALESCE(is_shadow,0)=1
            AND created_at>=datetime('now','-24 hours')""").fetchone()[0]
    return {"total":total,"last_24h":last_24h,"previous_24h":previous_24h,"shadow_24h":shadow_24h}

def forward_test_stats():
    """Frozen-strategy cohort metrics; excludes setups that never became trades."""
    with sqlite3.connect(DATABASE_PATH) as c:
        issued=c.execute("""SELECT COUNT(*) FROM signals WHERE strategy_version=?
            AND COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'""",
            (STRATEGY_VERSION,)).fetchone()[0]
        pending=c.execute("""SELECT COUNT(*) FROM signals WHERE strategy_version=?
            AND COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
            AND status IN ('SENT','WAITING','ACTIVE','OPEN')""",
            (STRATEGY_VERSION,)).fetchone()[0]
        excluded=c.execute("""SELECT result,COUNT(*) FROM signals WHERE strategy_version=?
            AND COALESCE(is_shadow,0)=0 AND result IN ('ENTRY_EXPIRED','INVALIDATED')
            GROUP BY result""",(STRATEGY_VERSION,)).fetchall()
        rows=c.execute("""SELECT result,pnl_r FROM signals WHERE strategy_version=? AND status='CLOSED'
            AND COALESCE(is_shadow,0)=0 AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
            ORDER BY closed_at,id""",(STRATEGY_VERSION,)).fetchall()
    pnl=[float(r[1] or 0) for r in rows]
    gains=sum(x for x in pnl if x>0); losses=-sum(x for x in pnl if x<0)
    equity=peak=max_dd=0.0
    for value in pnl:
        equity+=value; peak=max(peak,equity); max_dd=max(max_dd,peak-equity)
    return {"issued":issued,"closed":len(rows),"pending":pending,"wins":sum(1 for x in pnl if x>0),
            "net_r":sum(pnl),"profit_factor":gains/losses if losses else (999.0 if gains else 0.0),
            "max_drawdown_r":max_dd,"excluded":dict(excluded)}

def research_stats():
    """Outcome cohorts for the frozen research release; never alters thresholds."""
    with sqlite3.connect(DATABASE_PATH) as c:
        feature_count=c.execute("""SELECT COUNT(*) FROM signals
            WHERE strategy_version=? AND COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
            AND feature_json IS NOT NULL AND feature_json!='{}'""",
            (STRATEGY_VERSION,)).fetchone()[0]
        adl_rows=c.execute("""SELECT COALESCE(adl_risk,'unknown'),COUNT(*) FROM signals
            WHERE strategy_version=? AND COALESCE(is_shadow,0)=0
            GROUP BY COALESCE(adl_risk,'unknown')""",
            (STRATEGY_VERSION,)).fetchall()
        groups=c.execute("""SELECT timeframe,COALESCE(setup_type,'?'),side,COUNT(*) FROM signals
            WHERE strategy_version=? AND COALESCE(is_shadow,0)=0
            AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
            GROUP BY timeframe,COALESCE(setup_type,'?'),side
            ORDER BY timeframe,setup_type,side""",(STRATEGY_VERSION,)).fetchall()
        cohorts=[]
        for timeframe,setup,side,issued in groups:
            outcomes=c.execute("""SELECT pnl_r FROM signals WHERE strategy_version=?
                AND COALESCE(is_shadow,0)=0 AND timeframe=? AND COALESCE(setup_type,'?')=?
                AND side=? AND status='CLOSED'
                AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED') ORDER BY closed_at,id""",
                (STRATEGY_VERSION,timeframe,setup,side)).fetchall()
            pnl=[float(row[0] or 0) for row in outcomes]
            gains=sum(value for value in pnl if value>0); losses=-sum(value for value in pnl if value<0)
            cohorts.append({"timeframe":timeframe,"setup":setup,"side":side,"issued":issued,
                            "closed":len(pnl),"net_r":sum(pnl),
                            "profit_factor":gains/losses if losses else (999.0 if gains else 0.0)})
        shadow_rows=c.execute("""SELECT shadow_reason,pnl_r,result FROM signals WHERE strategy_version=?
            AND COALESCE(is_shadow,0)=1 AND status='CLOSED'
            AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED') ORDER BY closed_at,id""",
            (STRATEGY_VERSION,)).fetchall()
        shadow_pending=c.execute("""SELECT COUNT(*) FROM signals WHERE strategy_version=?
            AND COALESCE(is_shadow,0)=1 AND status IN ('SENT','WAITING','ACTIVE','OPEN')""",
            (STRATEGY_VERSION,)).fetchone()[0]
        shadow_reasons=c.execute("""SELECT COALESCE(shadow_reason,'UNKNOWN'),COUNT(*)
            FROM signals WHERE strategy_version=? AND COALESCE(is_shadow,0)=1
            GROUP BY COALESCE(shadow_reason,'UNKNOWN') ORDER BY COUNT(*) DESC""",
            (STRATEGY_VERSION,)).fetchall()
        shadow_cohorts=[]
        for reason,issued in shadow_reasons:
            outcomes=c.execute("""SELECT pnl_r FROM signals WHERE strategy_version=?
                AND COALESCE(is_shadow,0)=1 AND COALESCE(shadow_reason,'UNKNOWN')=?
                AND status='CLOSED' AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                ORDER BY closed_at,id""",(STRATEGY_VERSION,reason)).fetchall()
            pnl=[float(row[0] or 0) for row in outcomes]
            gains=sum(value for value in pnl if value>0); losses=-sum(value for value in pnl if value<0)
            equity=peak=max_dd=0.0
            for value in pnl:
                equity+=value; peak=max(peak,equity); max_dd=max(max_dd,peak-equity)
            shadow_cohorts.append({"reason":reason,"issued":issued,"closed":len(pnl),
                                   "net_r":sum(pnl),"max_drawdown_r":max_dd,
                                   "profit_factor":gains/losses if losses else (999.0 if gains else 0.0)})
    shadow_pnl=[float(row[1] or 0) for row in shadow_rows]
    shadow_gains=sum(value for value in shadow_pnl if value>0)
    shadow_losses=-sum(value for value in shadow_pnl if value<0)
    shadow={"closed":len(shadow_pnl),"pending":shadow_pending,"net_r":sum(shadow_pnl),
            "profit_factor":shadow_gains/shadow_losses if shadow_losses else (999.0 if shadow_gains else 0.0)}
    return {"feature_snapshots":feature_count,"adl":dict(adl_rows),"cohorts":cohorts,
            "shadow":shadow,"shadow_cohorts":shadow_cohorts}
