"""V11.16 Adaptive Edge: mature, negative-only cohort decay guard.

This layer never promotes a signal and never increases professional_rank. It only
blocks AUTO/ENTRY when a sufficiently mature production cohort shows persistent
forward deterioration. Small samples stay LEARNING to avoid overfitting.
"""
from __future__ import annotations
import math, sqlite3, time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

SCHEMA='11.16-adaptive-edge-v1'
MIN_N=20
QUARANTINE_N=30
MIN_DAYS=10
ROW_CACHE_TTL_SEC=30.0
_ROW_CACHE={}

@dataclass(frozen=True)
class AdaptiveDecision:
    label:str
    eligible:bool
    sample_n:int
    days:int
    avg_r:float
    profit_factor:float
    recent_avg_r:float
    prior_avg_r:float|None
    decay_r:float|None
    reason:str


def _finite(v,d=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else float(d)
    except Exception:return float(d)


def assess_stats(values,days:int):
    vals=[_finite(v,float('nan')) for v in values or ()]
    vals=[v for v in vals if math.isfinite(v)]
    n=len(vals); days=max(0,int(days or 0))
    if n<MIN_N or days<MIN_DAYS:
        return AdaptiveDecision('LEARNING',True,n,days,_finite(sum(vals)/n if n else 0),0.0,
                                _finite(sum(vals[-20:])/min(20,n) if n else 0),None,None,
                                f'learning n={n}/{MIN_N}, days={days}/{MIN_DAYS}')
    gains=sum(v for v in vals if v>0); losses=-sum(v for v in vals if v<0)
    pf=gains/losses if losses>0 else (999.0 if gains>0 else 0.0)
    avg=sum(vals)/n
    recent=vals[-20:] if n>=20 else vals
    recent_avg=sum(recent)/len(recent)
    prior=None; decay=None
    if n>=40:
        prev=vals[-40:-20]
        prior=sum(prev)/len(prev)
        decay=recent_avg-prior
    severe=(n>=QUARANTINE_N and avg<=-.12 and pf<.80 and recent_avg<=-.10)
    persistent=(avg<=-.05 and pf<.90 and recent_avg<0)
    decaying=(decay is not None and decay<=-.25 and recent_avg<=-.05)
    if severe:
        return AdaptiveDecision('QUARANTINE',False,n,days,avg,pf,recent_avg,prior,decay,
                                'mature cohort has persistently negative expectancy')
    if persistent or decaying:
        return AdaptiveDecision('DEGRADED',False,n,days,avg,pf,recent_avg,prior,decay,
                                'mature cohort forward edge deteriorated')
    return AdaptiveDecision('CLEAR',True,n,days,avg,pf,recent_avg,prior,decay,'mature cohort remains acceptable')


def _db_path():
    from app.config import DATABASE_PATH
    return DATABASE_PATH


def _rows_for(signal,limit=80):
    setup=str(getattr(signal,'setup_type','') or '')
    tf=str(getattr(signal,'timeframe','') or '')
    side=str(getattr(signal,'side','') or '')
    path=str(_db_path()); bounded=max(20,min(int(limit),200))
    key=(path,tf,side,setup,bounded)
    now=time.monotonic(); cached=_ROW_CACHE.get(key)
    if cached and now-cached[0]<=ROW_CACHE_TTL_SEC:
        return [dict(r) for r in cached[1]]
    try:
        # Adaptive Edge runs on the Telegram event loop in several production
        # paths. Never wait 10s on a SQLite writer lock; fail-open as LEARNING
        # and retry on the next cycle instead of freezing button processing.
        con=sqlite3.connect(path,timeout=.25)
        con.execute('PRAGMA busy_timeout=250')
        con.row_factory=sqlite3.Row
        try:
            rows=con.execute("""
                SELECT pnl_r,COALESCE(closed_at,created_at) AS ts
                FROM signals
                WHERE status='CLOSED' AND activated_at IS NOT NULL
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND pnl_r IS NOT NULL
                  AND timeframe=? AND side=? AND setup_type=?
                  AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                ORDER BY COALESCE(closed_at,created_at) DESC,id DESC
                LIMIT ?
            """,(tf,side,setup,bounded)).fetchall()
            out=[dict(r) for r in reversed(rows)]
            _ROW_CACHE[key]=(now,out)
            # Keep cache bounded across many setup/timeframe cohorts.
            if len(_ROW_CACHE)>256:
                oldest=sorted(_ROW_CACHE.items(),key=lambda kv:kv[1][0])[:64]
                for old_key,_ in oldest:_ROW_CACHE.pop(old_key,None)
            return [dict(r) for r in out]
        finally: con.close()
    except Exception:return []


def assess_signal(signal):
    rows=_rows_for(signal)
    vals=[r.get('pnl_r') for r in rows]
    days=0
    if rows:
        try:
            parsed=[datetime.fromisoformat(str(r.get('ts')).replace('Z','+00:00')) for r in rows if r.get('ts')]
            if parsed: days=max(1,(max(parsed)-min(parsed)).days+1)
        except Exception: days=0
    d=assess_stats(vals,days)
    fs=getattr(signal,'feature_snapshot',None)
    if isinstance(fs,dict):
        fs.setdefault('adaptive_edge_v11160',{}).update(asdict(d)|{'schema':SCHEMA,'negative_only':True,'professional_rank_changed':False})
    setattr(signal,'adaptive_edge_label',d.label)
    setattr(signal,'adaptive_edge_eligible',d.eligible)
    return d


def report_text(limit=12):
    """Compact production cohort health report."""
    try:
        con=sqlite3.connect(_db_path(),timeout=10); con.row_factory=sqlite3.Row
        try:
            groups=con.execute("""
                SELECT timeframe,side,setup_type,COUNT(*) n,MIN(COALESCE(closed_at,created_at)) first_ts,
                       MAX(COALESCE(closed_at,created_at)) last_ts
                FROM signals WHERE status='CLOSED' AND activated_at IS NOT NULL
                  AND COALESCE(is_shadow,0)=0 AND pnl_r IS NOT NULL
                GROUP BY timeframe,side,setup_type ORDER BY n DESC LIMIT ?
            """,(max(1,min(int(limit),30)),)).fetchall()
            lines=['🧠 <b>ADAPTIVE EDGE · V11.16</b>','━━━━━━━━━━━━━━━━━━']
            if not groups:return '\n'.join(lines+['Пока нет зрелых Production-когорт.'])
            for g in groups:
                fake=type('S',(),{'timeframe':g['timeframe'],'side':g['side'],'setup_type':g['setup_type'],'feature_snapshot':{}})()
                d=assess_signal(fake)
                icon='✅' if d.label=='CLEAR' else ('🟡' if d.label=='LEARNING' else '🛑')
                lines.append(f"{icon} <b>{g['timeframe']} {g['side']}</b> · {g['setup_type'] or 'OTHER'} · {d.label} · n {d.sample_n} · avg {d.avg_r:+.2f}R")
            lines+=['','<i>Только отрицательный guard: хорошая история не повышает рейтинг.</i>']
            return '\n'.join(lines)
        finally: con.close()
    except Exception:return '⚠️ Adaptive Edge report временно недоступен.'
