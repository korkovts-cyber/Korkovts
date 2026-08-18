"""V11.10 negative-only adaptive protection matrix.

The goal is similar to pair/stoploss/low-profit protections in mature trading
frameworks: repeated recent failure temporarily reduces risk instead of asking
the scanner to try the same idea with unchanged confidence.

Important safety rules:
- protections NEVER add rank;
- no small positive sample can promote a signal;
- hard locks require repeated, recent realised losses;
- rows that never became real delivered trades, shadows and ambiguous outcomes
  are excluded;
- the guard can only demote/reject an already-qualified candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
import sqlite3
from typing import Any

from app.config import DATABASE_PATH
from v1171_sqlite import db_session

BASE_RELEASE="11.7.1-futures-evidence"
LOOKBACK_DAYS=45
PAIR_LOCK_HOURS=24.0
SETUP_QUARANTINE_HOURS=12.0


@dataclass(frozen=True)
class ProtectionDecision:
    eligible:bool
    penalty:float
    label:str
    reasons:tuple[str,...]
    stats:dict

    def as_dict(self):
        return {
            "eligible":self.eligible,"penalty":self.penalty,"label":self.label,
            "reasons":list(self.reasons),"stats":self.stats,
        }


def _utc_epoch(value)->float|None:
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _pf(values):
    vals=[float(x) for x in values if x is not None and math.isfinite(float(x))]
    gains=sum(x for x in vals if x>0)
    losses=-sum(x for x in vals if x<0)
    return gains/losses if losses>0 else (999.0 if gains>0 else 0.0)


def _summary(rows):
    vals=[float(r.get("pnl_r") or 0) for r in rows]
    return {
        "n":len(vals),
        "mean_r":(sum(vals)/len(vals) if vals else 0.0),
        "net_r":sum(vals),
        "pf":_pf(vals),
        "losses":sum(1 for x in vals if x<0),
        "wins":sum(1 for x in vals if x>0),
    }


def _history(limit:int=1200):
    try:
        with db_session(DATABASE_PATH,row_factory=sqlite3.Row) as c:
            rows=c.execute("""
                SELECT symbol,timeframe,side,setup_type,pnl_r,
                       COALESCE(closed_at,created_at) event_time
                FROM signals
                WHERE status='CLOSED'
                  AND activated_at IS NOT NULL
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(delivery_state,'DELIVERED')='DELIVERED'
                  AND COALESCE(release_version,'')=?
                  AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                  AND pnl_r IS NOT NULL
                  AND COALESCE(closed_at,created_at)>=datetime('now',?)
                ORDER BY COALESCE(closed_at,created_at) DESC,id DESC
                LIMIT ?
            """,(BASE_RELEASE,f"-{int(LOOKBACK_DAYS)} days",int(limit))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def assess(signal:Any,rows=None,now_epoch:float|None=None)->ProtectionDecision:
    history=list(_history() if rows is None else rows)
    now=float(datetime.now(timezone.utc).timestamp() if now_epoch is None else now_epoch)
    symbol=str(getattr(signal,"symbol","") or "").upper()
    side=str(getattr(signal,"side","") or "").upper()
    timeframe=str(getattr(signal,"timeframe","") or "").upper()
    setup=str(getattr(signal,"setup_type","") or "")

    # Keep only finite realised outcomes and preserve newest-first ordering.
    clean=[]
    for row in history:
        try:
            pnl=float(row.get("pnl_r"))
        except Exception:
            continue
        if not math.isfinite(pnl):
            continue
        item=dict(row); item["pnl_r"]=pnl
        clean.append(item)

    reasons=[]; penalty=0.0; hard=False; stats={}

    pair=[r for r in clean if str(r.get("symbol") or "").upper()==symbol
          and str(r.get("side") or "").upper()==side]
    pair3=pair[:3]
    pair_summary=_summary(pair3)
    stats["pair_side_recent3"]=pair_summary
    if len(pair3)>=3 and all(float(r["pnl_r"])<0 for r in pair3) and pair_summary["net_r"]<=-1.50:
        latest=_utc_epoch(pair3[0].get("event_time"))
        age_h=(now-latest)/3600.0 if latest is not None else 9999.0
        stats["pair_side_recent3"]["latest_age_hours"]=age_h
        if 0<=age_h<=PAIR_LOCK_HOURS:
            hard=True
            reasons.append(f"{symbol} {side}: 3 recent losses ({pair_summary['net_r']:+.2f}R)")
    elif len(pair)>=2 and all(float(r["pnl_r"])<0 for r in pair[:2]):
        penalty=max(penalty,1.0)
        reasons.append(f"{symbol} {side}: two recent losses")

    cohort=[r for r in clean if str(r.get("setup_type") or "")==setup
            and str(r.get("timeframe") or "").upper()==timeframe
            and str(r.get("side") or "").upper()==side][:20]
    csum=_summary(cohort)
    recent5=_summary(cohort[:5])
    stats["setup_tf_side"]=dict(csum,recent5_net_r=recent5["net_r"])
    if csum["n"]>=12 and csum["mean_r"]<0 and csum["pf"]<.90 and recent5["net_r"]<0:
        sev=1.0
        if csum["mean_r"]<=-.10 and csum["pf"]<.80: sev=2.0
        if csum["mean_r"]<=-.15 and csum["pf"]<.70 and recent5["net_r"]<=-.75: sev=3.0
        penalty=max(penalty,sev)
        reasons.append(
            f"cohort {setup}/{timeframe}/{side}: mean {csum['mean_r']:+.2f}R, PF {csum['pf']:.2f}"
        )
        if csum["n"]>=20 and csum["mean_r"]<=-.20 and csum["pf"]<.60 and recent5["net_r"]<=-1.0:
            latest=_utc_epoch(cohort[0].get("event_time")) if cohort else None
            age_h=(now-latest)/3600.0 if latest is not None else 9999.0
            stats["setup_tf_side"]["latest_age_hours"]=age_h
            if 0<=age_h<=SETUP_QUARANTINE_HOURS:
                hard=True
                reasons.append("mature cohort temporarily quarantined")

    broad=[r for r in clean if str(r.get("timeframe") or "").upper()==timeframe
           and str(r.get("side") or "").upper()==side][:24]
    bsum=_summary(broad)
    stats["timeframe_side"]=bsum
    if bsum["n"]>=20 and bsum["mean_r"]<=-.10 and bsum["pf"]<.80:
        penalty=max(penalty,1.5)
        reasons.append(f"{timeframe}/{side} recent tape weak: {bsum['mean_r']:+.2f}R, PF {bsum['pf']:.2f}")

    penalty=min(4.0,max(0.0,float(penalty)))
    label="LOCK" if hard else ("DE-RISK" if penalty>0 else "CLEAR")
    return ProtectionDecision(not hard,penalty,label,tuple(dict.fromkeys(reasons)),stats)


def apply(signal:Any,rows=None,now_epoch:float|None=None):
    decision=assess(signal,rows,now_epoch)
    try:
        signal.protection_penalty=float(decision.penalty)
        signal.protection_label=str(decision.label)
        signal.feature_snapshot.setdefault("protections_v11100",{}).update(decision.as_dict())
    except Exception:
        pass
    return signal,decision


def apply_many(signals):
    rows=_history()
    output=[]
    for signal in list(signals or []):
        output.append(apply(signal,rows=rows))
    return output
