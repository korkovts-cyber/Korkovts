"""V11.16 immutable ENTRY NOW decision replay journal."""
from __future__ import annotations
import hashlib,json,math,sqlite3
from datetime import datetime,timezone
SCHEMA='11.16-entry-replay-v1'


def _safe(v,depth=0):
    if depth>10:return '<max-depth>'
    if v is None or isinstance(v,(str,bool,int)):return v
    if isinstance(v,float):return v if math.isfinite(v) else str(v)
    if isinstance(v,dict):
        out={}
        for k,x in v.items():
            key=str(k)
            if any(t in key.lower() for t in ('token','secret','api_key','password','authorization')):continue
            out[key]=_safe(x,depth+1)
        return out
    if isinstance(v,(list,tuple,set)):return [_safe(x,depth+1) for x in list(v)[:200]]
    try:return float(v)
    except Exception:return str(v)[:500]


def fingerprint(payload):
    raw=json.dumps(_safe(payload),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _db_path():
    from app.config import DATABASE_PATH
    return DATABASE_PATH


def init():
    con=sqlite3.connect(_db_path(),timeout=10)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS v11160_entry_replay(
            id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER NOT NULL UNIQUE,
            captured_at TEXT NOT NULL, symbol TEXT NOT NULL,timeframe TEXT,side TEXT,
            payload_json TEXT NOT NULL,fingerprint TEXT NOT NULL UNIQUE)""")
        con.execute('CREATE INDEX IF NOT EXISTS idx_v11160_replay_symbol ON v11160_entry_replay(symbol,captured_at)')
        con.commit()
    finally:con.close()


def record(signal_id,signal,assessment=None,extra=None):
    init()
    fs=dict(getattr(signal,'feature_snapshot',{}) or {})
    payload={
        'schema':SCHEMA,'signal_id':int(signal_id),'symbol':str(getattr(signal,'symbol','')),
        'timeframe':str(getattr(signal,'timeframe','')),'side':str(getattr(signal,'side','')),
        'professional_rank':getattr(signal,'professional_rank',None),'decision_priority':getattr(signal,'decision_priority',None),
        'entry_low':getattr(signal,'entry_low',None),'entry_high':getattr(signal,'entry_high',None),
        'stop':getattr(signal,'stop',None),'tp1':getattr(signal,'tp1',None),'tp2':getattr(signal,'tp2',None),'tp3':getattr(signal,'tp3',None),
        'strong':fs.get('strong_signal_v11150') or fs.get('strong_consensus_v11150'),
        'indicator_edge':fs.get('indicator_edge_v11151'),'adaptive_edge':fs.get('adaptive_edge_v11160'),
        'evidence':fs.get('evidence_v117'),'execution':fs.get('execution_revalidation'),
        'coherence':fs.get('data_coherence_v11100'),'news':fs.get('news'),
        'market_snapshot':fs.get('market_snapshot_v11170'),
        'execution_reality':fs.get('execution_reality_v11170'),
        'final_risk':fs.get('final_risk_gateway_v11170'),
        'validation':fs.get('validation_v11170'),
        'entry_assessment':_safe(getattr(assessment,'__dict__',assessment)),'extra':_safe(extra or {}),
    }
    payload=_safe(payload); fp=fingerprint(payload); now=datetime.now(timezone.utc).isoformat()
    con=sqlite3.connect(_db_path(),timeout=10)
    try:
        con.execute("""INSERT OR IGNORE INTO v11160_entry_replay
            (signal_id,captured_at,symbol,timeframe,side,payload_json,fingerprint) VALUES(?,?,?,?,?,?,?)""",
            (int(signal_id),now,payload['symbol'],payload['timeframe'],payload['side'],json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')),fp))
        con.commit()
    finally:con.close()
    return fp


def recent_text(limit=8):
    try:
        init(); con=sqlite3.connect(_db_path(),timeout=10); con.row_factory=sqlite3.Row
        try:rows=con.execute('SELECT signal_id,captured_at,symbol,timeframe,side,fingerprint FROM v11160_entry_replay ORDER BY id DESC LIMIT ?',(max(1,min(int(limit),20)),)).fetchall()
        finally:con.close()
        lines=['🎞 <b>ENTRY REPLAY · V11.16</b>','━━━━━━━━━━━━━━━━━━']
        if not rows:return '\n'.join(lines+['Пока нет зафиксированных ENTRY NOW.'])
        for r in rows:lines.append(f"• #{r['signal_id']} <b>{r['symbol']}</b> {r['side']} {r['timeframe']} · {str(r['fingerprint'])[:10]}")
        lines+=['','<i>Снимок immutable: контекст сохраняется до Telegram-доставки.</i>']
        return '\n'.join(lines)
    except Exception:return '⚠️ Replay journal временно недоступен.'
