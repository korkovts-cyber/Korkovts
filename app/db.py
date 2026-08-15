import os,sqlite3
from .config import DATABASE_PATH

def init():
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".",exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS signals(
        id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        symbol TEXT,timeframe TEXT,side TEXT,score REAL,entry REAL,stop REAL,tp1 REAL,tp2 REAL,tp3 REAL,status TEXT DEFAULT "OPEN")''')

def save(s):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.execute("INSERT INTO signals(symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3) VALUES(?,?,?,?,?,?,?,?,?)",
                  (s.symbol,s.timeframe,s.side,s.score,(s.entry_low+s.entry_high)/2,s.stop,s.tp1,s.tp2,s.tp3))

def recent(n=10):
    with sqlite3.connect(DATABASE_PATH) as c:
        return c.execute("SELECT created_at,symbol,timeframe,side,score,status FROM signals ORDER BY id DESC LIMIT ?",(n,)).fetchall()
