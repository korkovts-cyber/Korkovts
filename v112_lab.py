"""Forward-only factor lab for V11.2.

No factor is allowed to gain extra weight until enough CLOSED, ACTIVATED,
non-shadow signals exist. The lab never inverts a factor and never weakens
negative safety penalties. It can only:
- keep a positive factor at baseline weight 1.0
- increase a positive factor modestly when forward outcomes support it
- reduce a positive factor when forward outcomes contradict it

This is deliberately conservative to reduce overfitting.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from dataclasses import dataclass

from app.config import DATABASE_PATH

FEATURES=("fresh","momentum","ofi","squeeze","residual","quarter")
_cache={"ts":0.0,"weights":None,"cards":None}


@dataclass(frozen=True)
class FeatureCard:
    name:str
    positive_n:int
    control_n:int
    positive_exp:float
    control_exp:float
    effect_r:float
    z:float
    weight:float
    status:str


def _mean(values):
    return sum(values)/len(values) if values else 0.0


def _variance(values):
    return statistics.variance(values) if len(values)>=2 else 0.0


def weight_from_groups(positive,control,min_each=20):
    """Return (weight,effect,z,status), capped to avoid aggressive adaptation."""
    positive=[float(x) for x in positive]
    control=[float(x) for x in control]
    effect=_mean(positive)-_mean(control)
    if len(positive)<min_each or len(control)<min_each:
        return 1.0,effect,0.0,"LEARNING"

    se=math.sqrt(
        (_variance(positive)/len(positive) if positive else 0)
        +(_variance(control)/len(control) if control else 0)
    )
    z=effect/se if se>1e-9 else 0.0

    # 90%-ish normal threshold plus meaningful R difference.
    if z>=1.64 and effect>=.10:
        # Modest boost only; evidence must be stronger for the full 1.25.
        weight=min(1.25,1.0+min(.25,effect*.35))
        return weight,effect,z,"SUPPORTED"
    if z<=-1.64 and effect<=-.10:
        # Do not invert the factor. Just reduce future positive bonuses.
        weight=max(.50,1.0-max(.25,min(.50,abs(effect)*.50)))
        return weight,effect,z,"WEAK"
    return 1.0,effect,z,"NEUTRAL"


def _alpha_from_feature_json(feature_json):
    try:
        root=json.loads(feature_json or "{}")
    except Exception:
        return {}
    return root.get("alpha_v112") or root.get("alpha_v111") or {}


def _positive(feature,alpha,side):
    side=str(side or "")
    if feature=="fresh":
        return float(alpha.get("fresh_score",50) or 50)>=80
    if feature=="momentum":
        return float(alpha.get("momentum_percentile",50) or 50)>=70
    if feature=="ofi":
        direction=1 if side=="LONG" else -1
        return direction*float(alpha.get("ofi_5m",0) or 0)>=.05
    if feature=="squeeze":
        return bool(alpha.get("squeeze_release",False))
    if feature=="residual":
        return float(alpha.get("residual_6h_pct",0) or 0)>=.50
    if feature=="quarter":
        direction=1 if side=="LONG" else -1
        return bool(alpha.get("quarter_hour",False)) and direction*float(alpha.get("ofi_5m",0) or 0)>=.05
    return False


def scorecards(force=False):
    now=time.time()
    if not force and _cache["cards"] is not None and now-_cache["ts"]<6*3600:
        return _cache["cards"]
    rows=[]
    try:
        with sqlite3.connect(DATABASE_PATH) as c:
            rows=c.execute("""
                SELECT side,pnl_r,feature_json
                FROM signals
                WHERE status='CLOSED'
                  AND activated_at IS NOT NULL
                  AND COALESCE(is_shadow,0)=0
                  AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                  AND pnl_r IS NOT NULL
                  AND feature_json IS NOT NULL
                ORDER BY closed_at DESC
                LIMIT 1200
            """).fetchall()
    except Exception:
        rows=[]

    cards={}
    for feature in FEATURES:
        pos=[]; ctrl=[]
        for side,pnl,feature_json in rows:
            alpha=_alpha_from_feature_json(feature_json)
            if not alpha:
                continue
            (pos if _positive(feature,alpha,side) else ctrl).append(float(pnl))
        w,e,z,status=weight_from_groups(pos,ctrl)
        cards[feature]=FeatureCard(
            feature,len(pos),len(ctrl),_mean(pos),_mean(ctrl),e,z,w,status
        )
    _cache.update(ts=now,cards=cards,weights={k:v.weight for k,v in cards.items()})
    return cards


def weights(force=False):
    cards=scorecards(force)
    return {name:cards[name].weight for name in FEATURES}


def weighted_adjustment(signal):
    components=dict(getattr(signal,"alpha_components",{}) or {})
    w=weights()
    total=0.0
    applied={}
    for feature in FEATURES:
        value=float(components.get(feature,0) or 0)
        # Learned weights apply only to positive bonuses. Safety/contradiction
        # penalties remain full-strength even if a factor has weak historical edge.
        factor=w.get(feature,1.0) if value>0 else 1.0
        contribution=value*factor
        total+=contribution
        applied[feature]={"raw":value,"weight":factor,"weighted":contribution}
    total=max(-10.0,min(7.0,total))
    signal.alpha_adjustment=total
    signal.alpha_weighted_adjustment=total
    signal.alpha_factor_weights=w
    signal.feature_snapshot.setdefault("alpha_v112",{}).update({
        "weighted_adjustment":total,
        "factor_weights":w,
        "applied_components":applied,
    })
    return total


def lab_text():
    cards=scorecards()
    lines=[
        "🧬 <b>V11.2 FACTOR LAB</b>",
        "━━━━━━━━━━━━━━━━━━",
        "Вес >1.00 разрешён только после достаточной forward-выборки.",
        "Отрицательные risk-пенальти никогда не ослабляются.",
        "",
    ]
    labels={
        "fresh":"Fresh trigger",
        "momentum":"Relative momentum",
        "ofi":"Order flow",
        "squeeze":"Squeeze release",
        "residual":"BTC residual",
        "quarter":"Quarter-hour",
    }
    for name in FEATURES:
        c=cards[name]
        lines.append(
            f"{labels[name]}: <b>w={c.weight:.2f}</b> · {c.status} · "
            f"n+={c.positive_n}/n0={c.control_n} · "
            f"Δ=<b>{c.effect_r:+.2f}R</b>"
        )
    lines += [
        "",
        "LEARNING = данных пока недостаточно. В этом режиме вес остаётся 1.00.",
        "Это исследовательская калибровка, а не доказательство причинности.",
    ]
    return "\n".join(lines)
