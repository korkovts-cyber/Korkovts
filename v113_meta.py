"""Walk-forward meta-label precision gate for V11.4.1.

The meta model answers a narrow question:
"Given a signal that already passed the trading strategy, should this candidate
be allowed through?"

Safety design:
- no sklearn / no opaque dependency; small L2-regularized logistic model in numpy;
- separate model per timeframe;
- chronological walk-forward predictions only;
- threshold selected on earlier OOS predictions and verified on later OOS data;
- the gate remains LEARNING until sufficient forward observations prove that it
  improves precision without degrading Brier score;
- before READY, the meta model cannot reject or promote a production signal.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass

import numpy as np

from app.config import DATABASE_PATH

FEATURE_NAMES=(
    "core_score","data_health","execution_quality","regime_quality",
    "alpha","fresh","rel_momentum","signed_ofi5","residual",
    "spread_bps","impact_1k","impact_5k",
    "l2_signed_imbalance","l2_signed_microprice","l2_depth_ratio",
    "entry_distance_r",
)
_cache={}


@dataclass(frozen=True)
class ModelReport:
    timeframe:str
    status:str
    n:int
    positives:int
    oos_n:int
    accepted_n:int
    threshold:float
    precision:float
    baseline_precision:float
    precision_lower:float
    coverage:float
    brier:float
    baseline_brier:float
    reason:str


@dataclass(frozen=True)
class MetaDecision:
    ready:bool
    eligible:bool
    score:float
    threshold:float
    adjustment:float
    report:ModelReport
    ood:bool=False
    ood_rms:float=0.0
    ood_max_z:float=0.0


def _sigmoid(z):
    z=np.clip(z,-35,35)
    return 1.0/(1.0+np.exp(-z))


def _safe_feature(text):
    try:
        return json.loads(text or "{}")
    except Exception:
        return {}


def _num(v,default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _training_row_allowed(feature,is_shadow,reason):
    """Meta learns only from candidates that actually reached the Meta gate."""
    if (feature.get("delivery_meta") or {}).get("source")=="manual_symbol":
        return False
    if not (feature.get("meta_v113") or {}):
        return False
    if int(is_shadow or 0):
        return str(reason or "") in ("V1141_META_REJECT","V1141_PORTFOLIO")
    return True


def _vector(score,side,feature):
    v11=feature.get("v11") or {}
    alpha=feature.get("alpha_v112") or {}
    d=feature.get("derivatives") or {}
    ex=feature.get("execution_v113") or feature.get("execution_v1121") or {}
    rv=feature.get("execution_revalidation") or {}
    sign=1.0 if str(side)=="LONG" else -1.0
    return np.array([
        _num(score),
        _num(v11.get("data_health")),
        _num(v11.get("execution_quality")),
        _num(v11.get("regime_quality")),
        _num(alpha.get("weighted_adjustment",alpha.get("raw_adjustment",0))),
        _num(alpha.get("fresh_score"),50),
        _num(alpha.get("momentum_percentile"),50),
        sign*_num(alpha.get("ofi_5m")),
        _num(alpha.get("residual_pct",alpha.get("residual_6h_pct",0))),
        _num(d.get("spread_bps",ex.get("spread_bps",5)),5),
        _num(ex.get("impact_1k_bps",v11.get("impact_1k_bps",5)),5),
        _num(ex.get("impact_5k_bps",v11.get("impact_5k_bps",10)),10),
        _num(ex.get("signed_imbalance_10bps")),
        _num(ex.get("signed_microprice_bias_bps")),
        _num(ex.get("depth_ratio_vs_previous"),1),
        _num(rv.get("distance_r")),
    ],dtype=float)


def _rows(timeframe,limit=1200):
    tf=str(timeframe).upper()
    try:
        with sqlite3.connect(DATABASE_PATH,timeout=10) as c:
            rows=c.execute("""
                SELECT created_at,side,score,result,COALESCE(pnl_r,0),feature_json,
                       COALESCE(is_shadow,0),COALESCE(shadow_reason,'')
                FROM signals
                WHERE status='CLOSED'
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                  AND timeframe=?
                  AND feature_json IS NOT NULL
                  AND COALESCE(release_version,'') LIKE '11.4.1%'
                  AND (
                    COALESCE(is_shadow,0)=0
                    OR COALESCE(shadow_reason,'') IN (
                        'V1141_META_REJECT','V1141_PORTFOLIO'
                    )
                  )
                ORDER BY id DESC
                LIMIT ?
            """,(tf,int(limit))).fetchall()
    except Exception:
        return []

    rows=list(reversed(rows))
    out=[]
    for created,side,score,result,pnl,feature_json,is_shadow,reason in rows:
        feature=_safe_feature(feature_json)
        if not _training_row_allowed(feature,is_shadow,reason):
            continue
        x=_vector(score,side,feature)
        if not np.all(np.isfinite(x)):
            continue
        # We optimize "useful candidate after costs", not cosmetic win rate.
        # Entry-expired / invalidated rows are 0R and therefore negative.
        y=1.0 if float(pnl or 0)>.25 else 0.0
        out.append((str(created),x,y))
    return out


def _fit(X,y,iterations=280,lr=.08,l2=.08):
    X=np.asarray(X,dtype=float); y=np.asarray(y,dtype=float)
    mean=X.mean(axis=0)
    std=X.std(axis=0)
    std=np.where(std<1e-6,1.0,std)
    Z=(X-mean)/std
    Z=np.column_stack([np.ones(len(Z)),Z])
    w=np.zeros(Z.shape[1],dtype=float)
    for _ in range(int(iterations)):
        p=_sigmoid(Z@w)
        grad=(Z.T@(p-y))/len(y)
        grad[1:]+=float(l2)*w[1:]/len(y)
        w-=float(lr)*grad
    return mean,std,w


def _predict(model,X):
    mean,std,w=model
    X=np.asarray(X,dtype=float)
    one=X.ndim==1
    if one:
        X=X.reshape(1,-1)
    Z=(X-mean)/std
    Z=np.column_stack([np.ones(len(Z)),Z])
    p=_sigmoid(Z@w)
    return float(p[0]) if one else p


def _brier(y,p):
    y=np.asarray(y,dtype=float); p=np.asarray(p,dtype=float)
    return float(np.mean((p-y)**2)) if len(y) else 1.0


def _wilson_lower(successes,n,z=1.96):
    if n<=0:
        return 0.0
    p=float(successes)/n
    denom=1+z*z/n
    centre=p+z*z/(2*n)
    radius=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)
    return max(0.0,(centre-radius)/denom)


def _threshold(cal_p,cal_y):
    cal_p=np.asarray(cal_p); cal_y=np.asarray(cal_y)
    best=None
    for threshold in np.arange(.50,.76,.025):
        mask=cal_p>=threshold
        n=int(mask.sum())
        coverage=n/len(cal_p) if len(cal_p) else 0
        if n<25 or coverage<.20:
            continue
        precision=float(cal_y[mask].mean())
        # Precision first; then coverage; then lower threshold.
        candidate=(precision,coverage,-float(threshold),float(threshold))
        if best is None or candidate>best:
            best=candidate
    return best[3] if best else .60


def _walk_forward(rows):
    n=len(rows)
    positives=sum(int(r[2]) for r in rows)
    empty=ModelReport("", "LEARNING",n,positives,0,0,.60,0,0,0,0,1,1,"not enough data")
    if n<240 or positives<60 or (n-positives)<60:
        return empty,None

    X=np.vstack([r[1] for r in rows])
    y=np.array([r[2] for r in rows],dtype=float)

    min_train=180
    chunk=60
    oos_p=[]; oos_y=[]; oos_base=[]
    cursor=min_train
    while cursor<n:
        end=min(n,cursor+chunk)
        train_X=X[:cursor]; train_y=y[:cursor]
        # Both classes are mandatory.
        if train_y.sum()<25 or (len(train_y)-train_y.sum())<25:
            cursor=end
            continue
        model=_fit(train_X,train_y)
        p=_predict(model,X[cursor:end])
        base=np.full(end-cursor,float(train_y.mean()))
        oos_p.extend(map(float,p))
        oos_y.extend(map(float,y[cursor:end]))
        oos_base.extend(map(float,base))
        cursor=end

    if len(oos_y)<100:
        report=ModelReport("", "LEARNING",n,positives,len(oos_y),0,.60,0,0,0,0,1,1,
                           "insufficient walk-forward observations")
        return report,None

    oos_p=np.array(oos_p); oos_y=np.array(oos_y); oos_base=np.array(oos_base)
    split=max(50,int(len(oos_y)*.60))
    if len(oos_y)-split<35:
        split=len(oos_y)//2

    threshold=_threshold(oos_p[:split],oos_y[:split])
    test_p=oos_p[split:]; test_y=oos_y[split:]; test_base=oos_base[split:]
    mask=test_p>=threshold
    accepted=int(mask.sum())
    coverage=accepted/len(test_y) if len(test_y) else 0.0
    precision=float(test_y[mask].mean()) if accepted else 0.0
    baseline_precision=float(test_y.mean()) if len(test_y) else 0.0
    lower=_wilson_lower(int(test_y[mask].sum()) if accepted else 0,accepted)
    brier=_brier(test_y,test_p)
    baseline_brier=_brier(test_y,test_base)

    ready=(
        accepted>=25
        and coverage>=.20
        and precision>=baseline_precision+.05
        and lower>=baseline_precision+.01
        and brier<=baseline_brier*.98
    )
    status="READY" if ready else "LEARNING"
    reason=(
        "walk-forward precision and calibration gate passed"
        if ready else
        "walk-forward evidence is not strong enough yet"
    )
    report=ModelReport(
        "",status,n,positives,len(oos_y),accepted,float(threshold),
        precision,baseline_precision,lower,coverage,brier,baseline_brier,reason
    )
    final_model=_fit(X,y) if (n>=120 and positives>=25 and n-positives>=25) else None
    return report,final_model


def model(timeframe,force=False):
    tf=str(timeframe).upper()
    key=("meta",tf)
    cached=_cache.get(key)
    if not force and cached and time.time()-cached[0]<24*3600:
        return cached[1],cached[2]

    rows=_rows(tf)
    report,fitted=_walk_forward(rows)
    if report.timeframe=="":
        report=ModelReport(
            tf,report.status,report.n,report.positives,report.oos_n,
            report.accepted_n,report.threshold,report.precision,
            report.baseline_precision,report.precision_lower,report.coverage,
            report.brier,report.baseline_brier,report.reason
        )
    _cache[key]=(time.time(),report,fitted)
    return report,fitted


def _ood_metrics(model,x):
    """Diagonal standardized distance.

    This is intentionally conservative. OOD never rejects a trade by itself;
    it disables the Meta veto because extrapolating a learned classifier outside
    its training distribution is less trustworthy than falling back to the
    deterministic Production stack.
    """
    mean,std,_=model
    z=np.abs((np.asarray(x,dtype=float)-mean)/std)
    rms=float(np.sqrt(np.mean(np.square(np.clip(z,0,10)))))
    max_z=float(np.max(z)) if len(z) else 0.0
    ood=bool(rms>=3.0 or max_z>=6.0)
    return ood,rms,max_z


def decide(signal):
    tf=str(getattr(signal,"timeframe","")).upper()
    report,fitted=model(tf)
    score=.50
    ood=False; ood_rms=0.0; ood_max_z=0.0
    if fitted is not None:
        feature=getattr(signal,"feature_snapshot",{}) or {}
        x=_vector(getattr(signal,"score",0),getattr(signal,"side",""),feature)
        score=float(_predict(fitted,x))
        ood,ood_rms,ood_max_z=_ood_metrics(fitted,x)

    # If current conditions are outside the learned distribution, Meta abstains.
    # The deterministic Production stack remains in control.
    ready=report.status=="READY" and fitted is not None and not ood
    eligible=(score>=report.threshold) if ready else True
    adjustment=0.0

    decision=MetaDecision(
        ready,eligible,score,report.threshold,adjustment,report,
        ood,ood_rms,ood_max_z
    )
    signal.meta_ready=ready
    signal.meta_eligible=eligible
    signal.meta_score=score
    signal.meta_threshold=report.threshold
    signal.meta_adjustment=adjustment
    signal.meta_status=("ABSTAIN_OOD" if ood else report.status)
    signal.meta_ood=ood
    signal.feature_snapshot.setdefault("meta_v113",{}).update({
        "ready":ready,
        "eligible":eligible,
        "score":score,
        "threshold":report.threshold,
        "adjustment":adjustment,
        "ood":ood,
        "ood_rms":ood_rms,
        "ood_max_z":ood_max_z,
        "status":signal.meta_status,
        "report":report.__dict__,
        "feature_names":list(FEATURE_NAMES),
    })
    return signal,decision


def report_text():
    lines=[
        "🎯 <b>V11.4.1 META PRECISION</b>",
        "━━━━━━━━━━━━━━━━━━",
        "Meta-модель не предсказывает рынок с нуля — она фильтрует только уже подтверждённые сделки.",
        "До прохождения walk-forward gate она работает в LEARNING. Если рынок OOD — Meta делает ABSTAIN и передаёт решение детерминированным Production-фильтрам.",
    ]
    for tf in ("1H","15M"):
        r,_=model(tf)
        lines += [
            "",
            f"<b>{tf}</b> · <b>{r.status}</b>",
            f"Samples <b>{r.n}</b> · positives <b>{r.positives}</b> · OOS <b>{r.oos_n}</b>",
            f"Threshold <b>{r.threshold:.2f}</b> · coverage <b>{r.coverage*100:.0f}%</b>",
            f"Precision <b>{r.precision*100:.0f}%</b> vs baseline <b>{r.baseline_precision*100:.0f}%</b>",
            f"Brier <b>{r.brier:.3f}</b> vs baseline <b>{r.baseline_brier:.3f}</b>",
            f"{r.reason}",
        ]
    lines += ["","MetaScore — модельный фильтр, не обещание вероятности прибыли."]
    return "\n".join(lines)
