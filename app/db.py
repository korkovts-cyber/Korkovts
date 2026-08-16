import os,sqlite3
from .config import (DATABASE_PATH,STRATEGY_VERSION,DEFAULT_RISK_PCT,MAX_RISK_PCT,
    DAILY_STOP_R,MAX_OPEN_SIGNALS,ROUND_TRIP_COST_PCT,MAX_PORTFOLIO_RISK_PCT)
from .risk import conservative_plan

EXTRA_SIGNAL_COLUMNS={
    "closed_at":"TEXT", "result":"TEXT", "exit_price":"REAL", "pnl_r":"REAL",
    "last_checked_at":"TEXT", "max_favorable_r":"REAL DEFAULT 0",
    "max_adverse_r":"REAL DEFAULT 0", "strategy_version":"TEXT DEFAULT 'legacy'",
    "activated_at":"TEXT", "source_chat_id":"INTEGER"
}

def init():
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".",exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as c:
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

def save(s,chat_id=None):
    with sqlite3.connect(DATABASE_PATH) as c:
        cur=c.execute("INSERT INTO signals(symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,last_checked_at,strategy_version,status,source_chat_id) VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?,'SENT',?)",
                      (s.symbol,s.timeframe,s.side,s.score,s.entry_high if s.side=='LONG' else s.entry_low,
                       s.stop,s.tp1,s.tp2,s.tp3,STRATEGY_VERSION,int(chat_id) if chat_id is not None else None))
        return cur.lastrowid

def recent(n=10):
    with sqlite3.connect(DATABASE_PATH) as c:
        return c.execute("SELECT created_at,symbol,timeframe,side,score,status,result,pnl_r FROM signals ORDER BY id DESC LIMIT ?",(n,)).fetchall()

def open_signals(n=100):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute('''SELECT id,created_at,activated_at,last_checked_at,status,symbol,timeframe,side,entry,stop,tp1,tp2,tp3,
            max_favorable_r,max_adverse_r,source_chat_id FROM signals WHERE status IN ('SENT','WAITING','ACTIVE','OPEN') ORDER BY id LIMIT ?''',(n,))]

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
            FROM signals WHERE status='CLOSED' AND result!='ENTRY_EXPIRED'
            AND created_at>=datetime('now',?)''',(f'-{int(days)} days',)).fetchone()
        by_side=c.execute('''SELECT side,COUNT(*),AVG(pnl_r),SUM(result IN ('TP1','TP2','TP3'))*100.0/COUNT(*)
            FROM signals WHERE status='CLOSED' AND result!='ENTRY_EXPIRED'
            AND created_at>=datetime('now',?) GROUP BY side''',(f'-{int(days)} days',)).fetchall()
        open_count=c.execute("SELECT COUNT(*) FROM signals WHERE status IN ('WAITING','ACTIVE','OPEN')").fetchone()[0]
    return row,by_side,open_count

def calibration_penalty(symbol,side,timeframe,min_samples=20):
    """Only tightens the filter after enough forward results; never loosens it."""
    with sqlite3.connect(DATABASE_PATH) as c:
        row=c.execute('''SELECT COUNT(*),AVG(pnl_r),SUM(result IN ('TP1','TP2','TP3'))*100.0/COUNT(*)
            FROM signals WHERE status='CLOSED' AND result!='ENTRY_EXPIRED'
            AND strategy_version=? AND symbol=? AND side=? AND timeframe=?''',
            (STRATEGY_VERSION,symbol,side,timeframe)).fetchone()
        if not row or row[0]<min_samples:
            row=c.execute('''SELECT COUNT(*),AVG(pnl_r),SUM(result IN ('TP1','TP2','TP3'))*100.0/COUNT(*)
                FROM signals WHERE status='CLOSED' AND result!='ENTRY_EXPIRED'
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
            AND closed_at>=datetime('now','-24 hours') ORDER BY closed_at DESC''').fetchall()
        opened=c.execute("SELECT COUNT(*) FROM signals WHERE status IN ('WAITING','ACTIVE','OPEN')").fetchone()[0]
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

def was_sent_recently(symbol,side,hours=6,timeframe=None):
    with sqlite3.connect(DATABASE_PATH) as c:
        if timeframe:
            return c.execute('''SELECT 1 FROM signals WHERE symbol=? AND side=? AND timeframe=?
            AND created_at>=datetime('now',?) LIMIT 1''',(symbol,side,timeframe,f'-{int(hours)} hours')).fetchone() is not None
        return c.execute('''SELECT 1 FROM signals WHERE symbol=? AND side=?
            AND created_at>=datetime('now',?) LIMIT 1''',(symbol,side,f'-{int(hours)} hours')).fetchone() is not None
