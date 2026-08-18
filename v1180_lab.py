"""V11.8 Champion/Challenger shadow lab + performance diagnostics.

Production decisions are never changed by this module. It records a stricter
shadow opinion at the same market moment, then joins forward outcomes later.
Promotion is advisory only and requires a meaningful forward sample.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
import random
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import DATABASE_PATH
from v1170_calibration import futures as futures_probability, spot as spot_probability, _spot_success_value

CHALLENGER_VERSION="11.8.1-strict-shadow"
MIN_CHALLENGER_RESOLVED=50
MIN_REJECTED_RESOLVED=15


@contextmanager
def _db():
    c=sqlite3.connect(DATABASE_PATH,timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    c.row_factory=sqlite3.Row
    try:
        yield c; c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def init():
    with _db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS v1180_compare(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT,
                timeframe TEXT,
                setup_type TEXT,
                regime TEXT,
                champion_accept INTEGER NOT NULL DEFAULT 1,
                challenger_accept INTEGER NOT NULL,
                champion_score REAL,
                challenger_score REAL,
                predicted_probability REAL,
                challenger_reason TEXT,
                feature_json TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                success INTEGER,
                outcome_value REAL,
                mfe REAL,
                mae REAL,
                UNIQUE(market,source_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_v1180_compare_resolved ON v1180_compare(market,resolved_at,challenger_accept)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_v1180_compare_segment ON v1180_compare(market,setup_type,regime,timeframe,side)")


def _evidence(signal):
    return dict((getattr(signal,"feature_snapshot",{}) or {}).get("evidence_v117") or {})


def _probability(market,signal):
    try:
        est=futures_probability(signal) if market=="FUTURES" else spot_probability(signal)
        return float(est.probability) if getattr(est,"available",False) and est.probability is not None else None
    except Exception:
        return None


def challenger_decision(market,signal):
    """Intentionally stricter shadow filter; never changes Production."""
    market=str(market).upper(); ev=_evidence(signal)
    support=int(ev.get("support",0) or 0); conflict=int(ev.get("conflict",0) or 0)
    hard=bool(ev.get("hard_conflicts"))
    score=float(getattr(signal,"professional_rank",getattr(signal,"score",0)) or 0)
    snap=dict(getattr(signal,"feature_snapshot",{}) or {})

    if hard or conflict>0 or support<7:
        return False,score,f"evidence {support} support / {conflict} conflict"

    if market=="FUTURES":
        readiness=float(getattr(signal,"entry_now_score",0) or 0)
        execution=float(getattr(signal,"execution_quality",0) or 0)
        health=float(getattr(signal,"data_health_score",0) or 0)
        cost=float(getattr(signal,"estimated_cost_r",0) or 0)
        strict_score=max(92.0,score)
        ok=(score>=92 and readiness>=88 and execution>=88 and health>=90 and cost<=.20)
        reason=(
            f"PRO {score:.0f} · ready {readiness:.0f} · exe {execution:.0f} · "
            f"health {health:.0f} · cost {cost:.2f}R"
        )
        return bool(ok),strict_score,reason

    required=float(snap.get("required_score",82) or 82)
    micro=dict(snap.get("micro") or getattr(signal,"micro",{}) or {})
    market_snap=dict(snap.get("market") or {})
    head=float(snap.get("headroom_r",0) or 0)
    orderbook=dict(snap.get("local_orderbook_v118") or {})
    strict=max(88.0,required+3)
    flow=float(micro.get("buy_share",.5) or .5)
    f15=float(micro.get("closed_buy_share_15m",.5) or .5)
    regime=str(market_snap.get("regime") or getattr(signal,"market_regime","")).upper()
    ob_ok=bool(orderbook.get("healthy")) and float(orderbook.get("stability_score",0) or 0)>=70
    ok=(
        float(getattr(signal,"score",0) or 0)>=strict and head>=1.0
        and flow>=.55 and f15>=.52 and regime in ("BULL","NEUTRAL") and ob_ok
    )
    reason=(
        f"Q {float(getattr(signal,'score',0) or 0):.0f}/{strict:.0f} · "
        f"flow {flow:.0%}/{f15:.0%} · head {head:.2f}R · "
        f"book {float(orderbook.get('stability_score',0) or 0):.0f}"
    )
    return bool(ok),max(strict,float(getattr(signal,"score",0) or 0)),reason


def record(market,source_id,signal):
    init(); market=str(market).upper()
    accepted,cscore,reason=challenger_decision(market,signal)
    snap=dict(getattr(signal,"feature_snapshot",{}) or {})
    if market=="FUTURES":
        score=float(getattr(signal,"professional_rank",getattr(signal,"score",0)) or 0)
        regime=str((getattr(signal,"market_context",{}) or {}).get("bias") or snap.get("market_regime") or "")
        side=str(getattr(signal,"side","") or "")
        timeframe=str(getattr(signal,"timeframe","") or "")
    else:
        score=float(getattr(signal,"score",0) or 0)
        regime=str(getattr(signal,"market_regime","") or (snap.get("market") or {}).get("regime") or "")
        side="BUY"; timeframe="3-10D"
    payload=json.dumps({
        "evidence":snap.get("evidence_v117"),
        "local_orderbook":snap.get("local_orderbook_v118"),
    },ensure_ascii=False,sort_keys=True,default=str)
    with _db() as c:
        c.execute("""
            INSERT INTO v1180_compare(
                market,source_id,symbol,side,timeframe,setup_type,regime,
                challenger_accept,champion_score,challenger_score,
                predicted_probability,challenger_reason,feature_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(market,source_id) DO UPDATE SET
                challenger_accept=excluded.challenger_accept,
                champion_score=excluded.champion_score,
                challenger_score=excluded.challenger_score,
                predicted_probability=excluded.predicted_probability,
                challenger_reason=excluded.challenger_reason,
                feature_json=excluded.feature_json
        """,(
            market,int(source_id),str(getattr(signal,"symbol","")).upper(),side,timeframe,
            str(getattr(signal,"setup_type","") or ""),regime,int(accepted),score,float(cscore),
            _probability(market,signal),reason,payload,datetime.now(timezone.utc).isoformat(),
        ))
    return accepted


def _sync_futures(c,row):
    s=c.execute("""
        SELECT status,result,pnl_r,max_favorable_r,max_adverse_r,closed_at,delivery_state
        FROM signals WHERE id=?
    """,(int(row["source_id"]),)).fetchone()
    if (
        not s or str(s["status"])!="CLOSED" or s["pnl_r"] is None
        or str(s["delivery_state"] or "DELIVERED") not in ("DELIVERED","UNCERTAIN")
    ):
        return False
    result=str(s["result"] or "")
    if result in ("ENTRY_EXPIRED","INVALIDATED") or result.startswith("AMBIGUOUS"):
        return False
    pnl=float(s["pnl_r"] or 0)
    c.execute("""
        UPDATE v1180_compare SET resolved_at=?,success=?,outcome_value=?,mfe=?,mae=? WHERE id=?
    """,(str(s["closed_at"] or datetime.now(timezone.utc).isoformat()),int(pnl>0),pnl,
         float(s["max_favorable_r"] or 0),float(s["max_adverse_r"] or 0),int(row["id"])))
    return True


def _sync_spot(c,row):
    s=c.execute("""
        SELECT delivered_at,return_7d,tp1_hit,invalidated,result,delivery_uncertain,
               first_tp1_at,first_invalidation_at,max_favorable_pct,max_adverse_pct,closed_at
        FROM spot_signals WHERE id=?
    """,(int(row["source_id"]),)).fetchone()
    if not s or s["return_7d"] is None or s["delivered_at"] is None or int(s["delivery_uncertain"] or 0):
        return False
    val=_spot_success_value(dict(s))
    if val is None:
        return False
    c.execute("""
        UPDATE v1180_compare SET resolved_at=?,success=?,outcome_value=?,mfe=?,mae=? WHERE id=?
    """,(str(s["closed_at"] or datetime.now(timezone.utc).isoformat()),int(val>0),
         float(s["return_7d"] or 0),float(s["max_favorable_pct"] or 0),
         float(s["max_adverse_pct"] or 0),int(row["id"])))
    return True


def _prune_nonproduction(c):
    """Remove shadow-comparison rows whose production signal never actually existed for the user."""
    before=c.total_changes
    c.execute("""
        DELETE FROM v1180_compare
        WHERE market='FUTURES' AND resolved_at IS NULL
          AND EXISTS(
              SELECT 1 FROM signals s WHERE s.id=v1180_compare.source_id
                AND COALESCE(s.delivery_state,'')='FAILED'
          )
    """)
    c.execute("""
        DELETE FROM v1180_compare
        WHERE market='SPOT' AND resolved_at IS NULL
          AND EXISTS(
              SELECT 1 FROM spot_signals s WHERE s.id=v1180_compare.source_id
                AND s.delivered_at IS NULL AND s.state='CLOSED'
                AND COALESCE(s.result,'')='DELIVERY_EXPIRED'
          )
    """)
    return int(c.total_changes-before)


def sync_outcomes(limit=500):
    init(); updated=0
    with _db() as c:
        _prune_nonproduction(c)
        rows=c.execute("""
            SELECT * FROM v1180_compare WHERE resolved_at IS NULL ORDER BY id LIMIT ?
        """,(int(limit),)).fetchall()
        for row in rows:
            try:
                updated+=int(_sync_futures(c,row) if row["market"]=="FUTURES" else _sync_spot(c,row))
            except sqlite3.OperationalError:
                continue
    return updated


def _max_drawdown(values):
    equity=0.0; peak=0.0; maxdd=0.0
    for v in values:
        equity+=float(v); peak=max(peak,equity); maxdd=max(maxdd,peak-equity)
    return maxdd


def _metrics(rows):
    if not rows:
        return {"n":0,"wins":0,"win_rate":0.0,"avg":0.0,"median":0.0,"pf":0.0,"maxdd":0.0,"brier":None}
    vals=[float(r["outcome_value"] or 0) for r in rows]
    wins=sum(int(r["success"] or 0) for r in rows)
    gains=sum(v for v in vals if v>0); losses=-sum(v for v in vals if v<0)
    preds=[(float(r["predicted_probability"]),int(r["success"])) for r in rows if r["predicted_probability"] is not None]
    brier=sum((p-y)**2 for p,y in preds)/len(preds) if preds else None
    return {
        "n":len(rows),"wins":wins,"win_rate":wins/len(rows),
        "avg":sum(vals)/len(vals),"median":statistics.median(vals),
        "pf":gains/losses if losses>0 else (999.0 if gains>0 else 0.0),
        "maxdd":_max_drawdown(vals),"brier":brier,
    }


def _selection_gap(rows,iterations=1200):
    accepted=[float(r["outcome_value"] or 0) for r in rows if int(r["challenger_accept"] or 0)]
    rejected=[float(r["outcome_value"] or 0) for r in rows if not int(r["challenger_accept"] or 0)]
    if len(accepted)<2 or len(rejected)<2:
        return {"accepted_n":len(accepted),"rejected_n":len(rejected),
                "gap":None,"lower90":None,"upper90":None}
    gap=(sum(accepted)/len(accepted))-(sum(rejected)/len(rejected))
    rng=random.Random(1181)
    sims=[]
    for _ in range(max(200,int(iterations))):
        a=sum(rng.choice(accepted) for _ in accepted)/len(accepted)
        r=sum(rng.choice(rejected) for _ in rejected)/len(rejected)
        sims.append(a-r)
    sims.sort()
    lo=sims[int(.05*(len(sims)-1))]; hi=sims[int(.95*(len(sims)-1))]
    return {"accepted_n":len(accepted),"rejected_n":len(rejected),
            "gap":gap,"lower90":lo,"upper90":hi}


def summary(market):
    init(); market=str(market).upper(); sync_outcomes()
    with _db() as c:
        rows=c.execute("""
            SELECT * FROM v1180_compare
            WHERE market=? AND resolved_at IS NOT NULL ORDER BY id
        """,(market,)).fetchall()
    champion=_metrics(rows)
    accepted_rows=[r for r in rows if int(r["challenger_accept"] or 0)]
    rejected_rows=[r for r in rows if not int(r["challenger_accept"] or 0)]
    challenger=_metrics(accepted_rows)
    rejected=_metrics(rejected_rows)
    selection=_selection_gap(rows)
    promotion=False
    enough=(
        challenger["n"]>=MIN_CHALLENGER_RESOLVED
        and rejected["n"]>=MIN_REJECTED_RESOLVED
        and selection.get("lower90") is not None
    )
    if enough:
        if market=="FUTURES":
            promotion=(
                float(selection["lower90"])>0.05
                and challenger["avg"]>=champion["avg"]+.08
                and challenger["pf"]>=champion["pf"]
                and challenger["maxdd"]<=champion["maxdd"]
            )
        else:
            promotion=(
                float(selection["lower90"])>0.0
                and challenger["win_rate"]>=champion["win_rate"]+.05
                and challenger["avg"]>=champion["avg"]
                and challenger["maxdd"]<=champion["maxdd"]
            )
    return {
        "market":market,"champion":champion,"challenger":challenger,
        "rejected":rejected,"selection":selection,
        "promotion_candidate":bool(promotion)
    }


def segment_metrics(market,min_n=5,limit=8):
    init(); market=str(market).upper(); sync_outcomes()
    with _db() as c:
        rows=c.execute("""
            SELECT setup_type,regime,timeframe,side,COUNT(*) n,
                   SUM(success) wins,AVG(outcome_value) avg_outcome,
                   AVG(mfe) avg_mfe,AVG(mae) avg_mae
            FROM v1180_compare
            WHERE market=? AND resolved_at IS NOT NULL
            GROUP BY setup_type,regime,timeframe,side
            HAVING COUNT(*)>=?
            ORDER BY avg_outcome DESC, n DESC LIMIT ?
        """,(market,int(min_n),int(limit))).fetchall()
    return [dict(r) for r in rows]


def text():
    f=summary("FUTURES"); s=summary("SPOT")
    def line(x):
        c=x["champion"]; h=x["challenger"]
        return (
            f"{x['market']}: Champion n={c['n']} win {c['win_rate']*100:.0f}% avg {c['avg']:+.2f} · "
            f"Challenger n={h['n']} win {h['win_rate']*100:.0f}% avg {h['avg']:+.2f} · "
            f"{'🟢 PROMOTION CANDIDATE' if x['promotion_candidate'] else '🧪 keep shadow'}"
        )
    lines=[
        "🧪 <b>CHAMPION vs CHALLENGER · V11.8</b>","━━━━━━━━━━━━━━━━━━",
        line(f),line(s),"",
        "Challenger = более строгий shadow-фильтр. Он не меняет Production автоматически.",
        f"Минимум: <b>{MIN_CHALLENGER_RESOLVED}</b> accepted + "
        f"<b>{MIN_REJECTED_RESOLVED}</b> rejected forward outcomes.",
    ]
    for x in (f,s):
        sel=x.get("selection") or {}
        if sel.get("gap") is not None:
            lines.append(
                f"{x['market']} selection gap: <b>{float(sel['gap']):+.2f}</b> · "
                f"90% bootstrap CI {float(sel['lower90']):+.2f}…{float(sel['upper90']):+.2f}"
            )
    for market in ("FUTURES","SPOT"):
        seg=segment_metrics(market,5,4)
        if seg:
            lines += ["",f"<b>{market} · лучшие forward-сегменты:</b>"]
            for r in seg:
                lines.append(
                    f"• {r['setup_type'] or '—'} · {r['regime'] or '—'} · {r['timeframe'] or '—'} · "
                    f"n={r['n']} · win {int(r['wins'] or 0)/int(r['n'])*100:.0f}% · avg {float(r['avg_outcome'] or 0):+.2f}"
                )
    return "\n".join(lines)
