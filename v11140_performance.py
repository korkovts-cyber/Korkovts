"""V11.14 live performance and precision diagnostics.

Read-only analytics over delivered, activated Futures signals. This module never
changes selection or creates trades. It exists to measure the production system
on realised forward outcomes and expose enough diagnostics to improve it safely.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from html import escape

from app.config import DATABASE_PATH
from v1171_sqlite import db_session

@dataclass(frozen=True)
class PerfWindow:
    closed:int=0
    wins:int=0
    losses:int=0
    breakeven:int=0
    net_r:float=0.0
    avg_r:float=0.0
    profit_factor:float=0.0
    max_drawdown_r:float=0.0
    win_rate:float=0.0
    avg_mfe_r:float|None=None
    avg_mae_r:float|None=None


def _columns(c):
    try:
        return {str(r[1]) for r in c.execute("PRAGMA table_info(signals)").fetchall()}
    except Exception:
        return set()


def _rows(limit=100):
    limit=max(1,min(int(limit),500))
    try:
        with db_session(DATABASE_PATH,row_factory=sqlite3.Row) as c:
            cols=_columns(c)
            extras=[]
            if "max_favorable_r" in cols: extras.append("max_favorable_r")
            if "max_adverse_r" in cols: extras.append("max_adverse_r")
            extra_sql=(","+",".join(extras)) if extras else ""
            q=f"""
                SELECT id,created_at,closed_at,symbol,timeframe,side,setup_type,
                       result,pnl_r{extra_sql}
                FROM signals
                WHERE status='CLOSED'
                  AND activated_at IS NOT NULL
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                  AND pnl_r IS NOT NULL
                ORDER BY COALESCE(closed_at,created_at) DESC,id DESC
                LIMIT ?
            """
            return [dict(r) for r in c.execute(q,(limit,)).fetchall()]
    except Exception:
        return []


def summarize(rows):
    vals=[]; mfe=[]; mae=[]
    for r in rows or ():
        try:
            v=float(r.get("pnl_r"))
        except Exception:
            continue
        if not math.isfinite(v): continue
        vals.append(v)
        for key,target in (("max_favorable_r",mfe),("max_adverse_r",mae)):
            try:
                x=float(r.get(key))
                if math.isfinite(x): target.append(x)
            except Exception:
                pass
    if not vals: return PerfWindow()
    wins=sum(v>0 for v in vals); losses=sum(v<0 for v in vals); be=len(vals)-wins-losses
    gains=sum(v for v in vals if v>0); neg=-sum(v for v in vals if v<0)
    pf=(gains/neg) if neg>0 else (999.0 if gains>0 else 0.0)
    equity=0.0; peak=0.0; dd=0.0
    # Rows arrive newest-first; reverse to reconstruct the chronological path.
    for v in reversed(vals):
        equity+=v; peak=max(peak,equity); dd=max(dd,peak-equity)
    return PerfWindow(
        closed=len(vals),wins=wins,losses=losses,breakeven=be,
        net_r=sum(vals),avg_r=sum(vals)/len(vals),profit_factor=pf,
        max_drawdown_r=dd,win_rate=wins/len(vals),
        avg_mfe_r=(sum(mfe)/len(mfe) if mfe else None),
        avg_mae_r=(sum(mae)/len(mae) if mae else None),
    )


def snapshot():
    rows=_rows(100)
    return {n:summarize(rows[:n]) for n in (20,50,100)} | {"rows":rows}


def _pf_text(v):
    return "∞" if v>=999 else f"{v:.2f}"


def report_text():
    s=snapshot(); lines=["📈 <b>LIVE PERFORMANCE · V11.14</b>","━━━━━━━━━━━━━━━━━━"]
    if not s[100].closed:
        lines += [
            "Пока нет закрытых активированных Production Futures-сигналов.",
            "Статистика начнёт заполняться автоматически после закрытия live-сигналов.",
        ]
        return "\n".join(lines)
    for n in (20,50,100):
        w=s[n]
        if not w.closed: continue
        lines.append(
            f"<b>Последние {w.closed}</b> · WR <b>{w.win_rate*100:.1f}%</b> · "
            f"Net <b>{w.net_r:+.2f}R</b> · Avg <b>{w.avg_r:+.2f}R</b> · "
            f"PF <b>{_pf_text(w.profit_factor)}</b> · DD <b>{w.max_drawdown_r:.2f}R</b>"
        )
    w=s[100]
    if w.avg_mfe_r is not None or w.avg_mae_r is not None:
        lines.append(
            f"MFE <b>{(w.avg_mfe_r or 0):+.2f}R</b> · MAE <b>{(w.avg_mae_r or 0):+.2f}R</b>"
        )
    rows=s["rows"]
    cohorts={}
    for r in rows:
        key=f"{r.get('timeframe') or '?'} · {r.get('setup_type') or 'OTHER'}"
        cohorts.setdefault(key,[]).append(r)
    ranked=[]
    for key,items in cohorts.items():
        p=summarize(items)
        if p.closed>=5: ranked.append((p.avg_r,p.closed,key,p))
    ranked.sort(reverse=True)
    if ranked:
        lines += ["","🏆 <b>ЛУЧШИЕ ЗРЕЛЫЕ КОГОРТЫ</b>"]
        for _,_,key,p in ranked[:3]:
            lines.append(f"• {escape(key)} · n {p.closed} · {p.avg_r:+.2f}R avg · PF {_pf_text(p.profit_factor)}")
    lines += ["","<i>Только доставленные и активированные сделки. Shadow/expired/ambiguous исключены.</i>"]
    return "\n".join(lines)


def annotate_decision_margin(chosen,all_candidates):
    """Attach rank-separation diagnostics without changing selection order."""
    pool=list(all_candidates or ())
    if not pool: return chosen
    def priority(s):
        return float(getattr(s,"decision_priority",getattr(s,"professional_rank",0)) or 0)
    ranked=sorted(pool,key=priority,reverse=True)
    p1=priority(ranked[0]); p2=priority(ranked[1]) if len(ranked)>1 else None
    margin=(p1-p2) if p2 is not None else None
    for idx,s in enumerate(chosen or (),1):
        own=priority(s)
        # Margin is relative to the next-best eligible candidate in the complete pool.
        competitors=[priority(x) for x in ranked if id(x)!=id(s)]
        m=(own-max(competitors)) if competitors else None
        label=("CLEAR_PRIME" if idx==1 and m is not None and m>=3.0
               else "PRIME" if idx==1 and m is None
               else "NEAR_TIE" if idx==1 and m is not None and m<1.0
               else "CLOSE_RACE" if idx==1 and m is not None and m<3.0
               else "SECONDARY")
        s.decision_margin=m
        s.decision_margin_label=label
        s.feature_snapshot.setdefault("decision_margin_v11140",{}).update({
            "rank":idx,"priority":own,"margin_to_best_competitor":m,
            "label":label,"pool_size":len(ranked),"top_margin":margin,
        })
    return chosen
