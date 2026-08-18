"""Entry-conversion analytics for V11.4.1.

This is observational. It does not change thresholds yet.
It separates:
- whether the advertised Entry was actually reached before expiry;
- what happened after activation.

That prevents a setup with a good directional idea but impractical entry from
looking better than it is.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.config import DATABASE_PATH


@dataclass(frozen=True)
class EntryStats:
    timeframe:str
    issued:int
    resolved:int
    pending_entry:int
    ambiguous:int
    activated:int
    expired:int
    invalidated:int
    closed_trades:int
    wins:int
    losses:int
    activation_rate:float
    tp2_given_active:float
    avg_r_given_active:float


def activation_rate(activated,expired,invalidated):
    resolved=int(activated)+int(expired)+int(invalidated)
    return float(activated)/resolved if resolved else 0.0


def stats(timeframe,release_like="11.4.1%"):
    tf=str(timeframe).upper()
    try:
        with sqlite3.connect(DATABASE_PATH,timeout=10) as c:
            rows=c.execute("""
                SELECT activated_at,status,result,COALESCE(pnl_r,0)
                FROM signals
                WHERE timeframe=?
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND COALESCE(release_version,'') LIKE ?
                ORDER BY id
            """,(tf,release_like)).fetchall()
    except Exception:
        rows=[]

    issued=len(rows)
    usable=[row for row in rows if not str(row[2] or "").startswith("AMBIGUOUS")]
    activated=sum(1 for activated,_,result,_ in usable
                  if activated is not None or result not in (None,"ENTRY_EXPIRED","INVALIDATED"))
    expired=sum(1 for _,_,r,_ in usable if r=="ENTRY_EXPIRED")
    invalidated=sum(1 for _,_,r,_ in usable if r=="INVALIDATED")
    resolved=activated+expired+invalidated
    ambiguous=sum(1 for _,_,r,_ in rows if str(r or "").startswith("AMBIGUOUS"))
    pending_entry=max(0,issued-resolved-ambiguous)
    trades=[(r,float(p or 0)) for a,s,r,p in usable
            if r not in (None,"ENTRY_EXPIRED","INVALIDATED")]
    wins=sum(1 for r,p in trades if p>0)
    losses=sum(1 for r,p in trades if p<0)
    return EntryStats(
        tf,issued,resolved,pending_entry,ambiguous,activated,expired,invalidated,len(trades),wins,losses,
        activation_rate(activated,expired,invalidated),
        sum(1 for r,_ in trades if r=="TP2")/len(trades) if trades else 0.0,
        sum(p for _,p in trades)/len(trades) if trades else 0.0,
    )


def breakdown(timeframe,release_like="11.4.1%",min_issued=10):
    tf=str(timeframe).upper()
    try:
        with sqlite3.connect(DATABASE_PATH,timeout=10) as c:
            groups=c.execute("""
                SELECT COALESCE(setup_type,'?'),side,
                       COUNT(*),
                       SUM(CASE WHEN activated_at IS NOT NULL
                                 OR result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                                THEN 1 ELSE 0 END),
                       SUM(result='ENTRY_EXPIRED'),
                       SUM(result='INVALIDATED'),
                       SUM(CASE WHEN activated_at IS NOT NULL
                                 OR result IS NOT NULL
                                THEN 1 ELSE 0 END)
                FROM signals
                WHERE timeframe=?
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND COALESCE(release_version,'') LIKE ?
                GROUP BY COALESCE(setup_type,'?'),side
                HAVING COUNT(*)>=?
                ORDER BY COUNT(*) DESC
                LIMIT 4
            """,(tf,release_like,int(min_issued))).fetchall()
    except Exception:
        groups=[]
    return [
        {
            "setup":str(setup),"side":str(side),"issued":int(issued),
            "activation_rate":float(activated or 0)/max(1,int(resolved or 0)),
            "resolved":int(resolved or 0),
            "expired":int(expired or 0),"invalidated":int(invalidated or 0),
        }
        for setup,side,issued,activated,expired,invalidated,resolved in groups
    ]



_group_cache={}


def group_activation(signal,min_resolved=30):
    """Return activation statistics for the same setup/timeframe/side."""
    import time
    key=(str(getattr(signal,"timeframe","")).upper(),
         str(getattr(signal,"side","")),
         str(getattr(signal,"setup_type","") or "?"))
    cached=_group_cache.get(key)
    if cached and time.time()-cached[0]<6*3600:
        return dict(cached[1])
    try:
        with sqlite3.connect(DATABASE_PATH,timeout=10) as c:
            row=c.execute("""
                SELECT
                  SUM(CASE WHEN activated_at IS NOT NULL
                            OR result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                           THEN 1 ELSE 0 END),
                  SUM(result='ENTRY_EXPIRED'),
                  SUM(result='INVALIDATED')
                FROM signals
                WHERE timeframe=? AND side=? AND COALESCE(setup_type,'?')=?
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND COALESCE(release_version,'') LIKE '11.4.1%'
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
            """,key).fetchone()
        activated=int((row or [0,0,0])[0] or 0)
        expired=int((row or [0,0,0])[1] or 0)
        invalidated=int((row or [0,0,0])[2] or 0)
    except Exception:
        activated=expired=invalidated=0
    resolved=activated+expired+invalidated
    rate=activation_rate(activated,expired,invalidated)
    result={"resolved":resolved,"activation_rate":rate,
            "activated":activated,"expired":expired,"invalidated":invalidated,
            "ready":resolved>=int(min_resolved)}
    _group_cache[key]=(time.time(),dict(result))
    return result


def negative_penalty(signal,min_resolved=30):
    """Activation quality can only demote; never promote.

    Thresholds are deliberately mild until much larger samples exist:
    <35% activation after >=30 resolved -> -2.0 PRO
    <50% activation after >=30 resolved -> -1.0 PRO
    """
    row=group_activation(signal,min_resolved)
    if not row["ready"]:
        return 0.0,row
    rate=float(row["activation_rate"])
    if rate<.35:
        return -2.0,row
    if rate<.50:
        return -1.0,row
    return 0.0,row


def _reference_stats(timeframe):
    # V11.3.1 has the corrected delivery-aware entry clock and can be shown as
    # a reference baseline, but it does NOT train V11.4.1 adaptive filters.
    return stats(timeframe,"11.3.1%")


def text():
    lines=[
        "🎯 <b>ENTRY QUALITY · V11.4.1</b>",
        "━━━━━━━━━━━━━━━━━━",
        "Отдельно измеряем реализуемость Entry и результат после активации.",
        "После ≥30 resolved наблюдений плохая реализуемость Entry может только понизить PRO; бонусов за высокий activation rate нет.",
    ]
    for tf in ("1H","15M"):
        cur=stats(tf,"11.4.1%")
        ref=_reference_stats(tf)
        lines += [
            "",
            f"<b>{tf}</b> · V11.4.1 issued <b>{cur.issued}</b>",
            f"Entry activation <b>{cur.activation_rate*100:.0f}%</b> по resolved <b>{cur.resolved}</b> · "
            f"waiting <b>{cur.pending_entry}</b> · ambiguous <b>{cur.ambiguous}</b> · "
            f"expired <b>{cur.expired}</b> · invalidated <b>{cur.invalidated}</b>",
            f"После входа: n=<b>{cur.closed_trades}</b> · "
            f"TP2 <b>{cur.tp2_given_active*100:.0f}%</b> · "
            f"avg <b>{cur.avg_r_given_active:+.2f}R</b>",
            f"Reference V11.3.1: activation <b>{ref.activation_rate*100:.0f}%</b> "
            f"(issued {ref.issued})",
        ]
        for row in breakdown(tf):
            lines.append(
                f"↳ {row['side']} · {row['setup']}: activation "
                f"<b>{row['activation_rate']*100:.0f}%</b> · resolved={row['resolved']} · issued={row['issued']}"
            )
    return "\n".join(lines)
