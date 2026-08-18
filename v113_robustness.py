"""Block-bootstrap robustness report for V11.7.1.

Signals issued on the same market day are correlated. Resampling individual
trades pretends they are independent and can make uncertainty look too small.
V11.7.1 therefore resamples whole UTC-day blocks.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

import numpy as np

from app.config import DATABASE_PATH
from v1171_sqlite import db_session


def _day_blocks(timeframe,limit=700):
    try:
        with db_session(timeout=10) as c:
            rows=c.execute("""
                SELECT substr(COALESCE(closed_at,created_at),1,10),COALESCE(pnl_r,0)
                FROM signals
                WHERE status='CLOSED'
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                  AND timeframe=?
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND COALESCE(release_version,'') LIKE '11.7.1%'
                ORDER BY closed_at DESC LIMIT ?
            """,(str(timeframe).upper(),int(limit))).fetchall()
    except Exception:
        rows=[]

    grouped=defaultdict(list)
    for day,pnl in rows:
        grouped[str(day)].append(float(pnl or 0))
    return list(grouped.values())


def bootstrap(timeframe,simulations=1800):
    blocks=_day_blocks(timeframe)
    n=sum(len(b) for b in blocks)
    days=len(blocks)
    flat=np.array([x for b in blocks for x in b],dtype=float)
    mean=float(flat.mean()) if n else 0.0
    if n<30 or days<10:
        return {"status":"LEARNING","n":n,"days":days,"mean":mean,
                "p05":0.0,"p95":0.0,"positive_probability":0.0}

    rng=np.random.default_rng(114 if str(timeframe).upper()=="1H" else 1514)
    means=[]
    for _ in range(int(simulations)):
        selected=rng.integers(0,days,size=days)
        values=[x for idx in selected for x in blocks[int(idx)]]
        means.append(float(np.mean(values)) if values else 0.0)
    means=np.asarray(means,dtype=float)
    p05=float(np.quantile(means,.05)); p95=float(np.quantile(means,.95))
    return {
        "status":"ROBUST" if p05>0 else "UNCERTAIN",
        "n":n,"days":days,"mean":mean,"p05":p05,"p95":p95,
        "positive_probability":float((means>0).mean()),
    }


def text():
    lines=[
        "🧪 <b>V11.7.1 BLOCK ROBUSTNESS</b>",
        "━━━━━━━━━━━━━━━━━━",
        "Bootstrap идёт блоками по UTC-дням, поэтому сигналы одного рыночного движения не считаются полностью независимыми.",
    ]
    for tf in ("1H","15M"):
        r=bootstrap(tf)
        lines += [
            "",
            f"<b>{tf}</b> · {r['status']} · trades <b>{r['n']}</b> · days <b>{r['days']}</b>",
            f"Mean <b>{r['mean']:+.2f}R</b> · 90% block-bootstrap <b>{r['p05']:+.2f}…{r['p95']:+.2f}R</b>",
            f"P(mean&gt;0) <b>{r['positive_probability']*100:.0f}%</b>",
        ]
    return "\n".join(lines)
