"""Forward-only, counterfactual factor lab for V11.4.1."""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from dataclasses import dataclass

from app.config import DATABASE_PATH

FEATURES=("fresh","momentum","ofi","squeeze","residual","quarter")
SHADOW_REASONS=("V1123_ALPHA_REJECT","V1123_PORTFOLIO")
_cache={}


@dataclass(frozen=True)
class FeatureCard:
    name:str
    timeframe:str
    positive_n:int
    control_n:int
    negative_n:int
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


def weight_from_groups(positive,control,min_each=40):
    """Conservative adaptive weight with time-stability protection.

    Values arrive newest-first from SQLite. A positive boost requires:
    - enough observations in both groups,
    - a meaningful overall effect,
    - strong overall separation,
    - and the effect not disappearing in either recent or older halves.
    """
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

    p_mid=len(positive)//2
    c_mid=len(control)//2
    recent_effect=_mean(positive[:p_mid])-_mean(control[:c_mid])
    older_effect=_mean(positive[p_mid:])-_mean(control[c_mid:])

    if z>=2.20 and effect>=.12 and recent_effect>=.05 and older_effect>=0:
        # Small evidence-based nudge only. Ranking remains dominated by core.
        weight=min(1.10,1.0+min(.10,effect*.20))
        return weight,effect,z,"SUPPORTED"
    if z<=-2.20 and effect<=-.12 and recent_effect<=0 and older_effect<=0:
        # Never invert. Only reduce future positive bonuses.
        weight=max(.70,1.0-max(.15,min(.30,abs(effect)*.30)))
        return weight,effect,z,"WEAK"
    return 1.0,effect,z,"NEUTRAL"


def _alpha_from_feature_json(feature_json):
    try:
        root=json.loads(feature_json or "{}")
    except Exception:
        return {}
    return root.get("alpha_v112") or {}


def _component(feature,alpha):
    return float((alpha.get("components") or {}).get(feature,0) or 0)


def _two_sided_p(z):
    return math.erfc(abs(float(z))/math.sqrt(2.0))


def _bh_approved(pvalues,q=.10):
    """Benjamini-Hochberg FDR control across the factor family."""
    items=sorted((float(p),name) for name,p in pvalues.items())
    m=len(items)
    cutoff=None
    for rank,(p,name) in enumerate(items,1):
        if p<=float(q)*rank/max(1,m):
            cutoff=p
    if cutoff is None:
        return set()
    return {name for name,p in pvalues.items() if float(p)<=cutoff}


def scorecards(timeframe,force=False):
    tf=str(timeframe).upper()
    key=("cards",tf)
    cached=_cache.get(key)
    if not force and cached and time.time()-cached[0]<6*3600:
        return cached[1]

    rows=[]
    try:
        with sqlite3.connect(DATABASE_PATH,timeout=10) as c:
            rows=c.execute("""
                SELECT timeframe,side,COALESCE(pnl_r,0),feature_json,is_shadow,shadow_reason
                FROM signals
                WHERE status='CLOSED'
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                  AND feature_json IS NOT NULL
                  AND COALESCE(release_version,'') LIKE '11.4.1%'
                  AND timeframe=?
                  AND (
                    COALESCE(is_shadow,0)=0
                    OR shadow_reason IN ('V1141_ALPHA_REJECT','V1141_EXECUTION_REJECT','V1141_META_REJECT','V1141_PORTFOLIO','V1141_ENTRY_REJECT')
                  )
                ORDER BY closed_at DESC
                LIMIT 1600
            """,(tf,)).fetchall()
    except Exception:
        rows=[]

    cards={}
    for feature in FEATURES:
        pos=[]; ctrl=[]; neg=[]
        for _,side,pnl,feature_json,is_shadow,shadow_reason in rows:
            try:
                root=json.loads(feature_json or "{}")
            except Exception:
                root={}
            if (root.get("delivery_meta") or {}).get("source")=="manual_symbol":
                continue
            alpha=root.get("alpha_v112") or {}
            if not alpha:
                continue
            component=_component(feature,alpha)
            if component>0:
                pos.append(float(pnl))
            elif component<0:
                neg.append(float(pnl))
            else:
                ctrl.append(float(pnl))
        w,e,z,status=weight_from_groups(pos,ctrl)
        cards[feature]=FeatureCard(
            feature,tf,len(pos),len(ctrl),len(neg),
            _mean(pos),_mean(ctrl),e,z,w,status
        )

    pvalues={
        name:_two_sided_p(card.z)
        for name,card in cards.items()
        if card.positive_n>=40 and card.control_n>=40
    }
    approved=_bh_approved(pvalues,q=.10) if pvalues else set()
    if pvalues:
        adjusted={}
        for name,card in cards.items():
            if card.status in ("SUPPORTED","WEAK") and name not in approved:
                adjusted[name]=FeatureCard(
                    card.name,card.timeframe,card.positive_n,card.control_n,
                    card.negative_n,card.positive_exp,card.control_exp,
                    card.effect_r,card.z,1.0,"FDR_WAIT"
                )
            else:
                adjusted[name]=card
        cards=adjusted

    _cache[key]=(time.time(),cards)
    return cards


def weights(timeframe,force=False):
    cards=scorecards(timeframe,force)
    return {name:cards[name].weight for name in FEATURES}


def _family_total(values,positive_cap):
    positive=sum(max(0.0,float(v)) for v in values)
    negative=sum(min(0.0,float(v)) for v in values)
    return min(float(positive_cap),positive)+negative


def weighted_adjustment(signal):
    components=dict(getattr(signal,"alpha_components",{}) or {})
    w=weights(getattr(signal,"timeframe","1H"))
    applied={}

    weighted={}
    for feature in FEATURES:
        value=float(components.get(feature,0) or 0)
        # Learned weights apply only to bonuses. Contradictory evidence keeps
        # its full penalty regardless of past calibration.
        factor=w.get(feature,1.0) if value>0 else 1.0
        weighted[feature]=value*factor
        applied[feature]={"raw":value,"weight":factor,"weighted":weighted[feature]}

    # Avoid double-counting related evidence:
    # fresh+squeeze = timing family
    # OFI+quarter = flow family
    # relative momentum+BTC residual = relative-strength family
    families={
        "timing":_family_total([weighted["fresh"],weighted["squeeze"]],3.5),
        "flow":_family_total([weighted["ofi"],weighted["quarter"]],2.5),
        "relative":_family_total([weighted["momentum"],weighted["residual"]],2.5),
    }
    total=max(-10.0,min(5.5,sum(families.values())))

    signal.alpha_adjustment=total
    signal.alpha_weighted_adjustment=total
    signal.alpha_factor_weights=w
    signal.feature_snapshot.setdefault("alpha_v112",{}).update({
        "weighted_adjustment":total,
        "factor_weights":w,
        "applied_components":applied,
        "family_contributions":families,
        "factor_timeframe":str(getattr(signal,"timeframe","")).upper(),
    })
    return total


def lab_text():
    labels={
        "fresh":"Fresh",
        "momentum":"RelMomentum",
        "ofi":"OrderFlow",
        "squeeze":"Squeeze",
        "residual":"BTC residual",
        "quarter":"Quarter-hour",
    }
    lines=[
        "🧬 <b>V11.4.1 FACTOR LAB</b>",
        "━━━━━━━━━━━━━━━━━━",
        "1H и 15M обучаются отдельно.",
        "Production-сигналы сравниваются с counterfactual shadow-кандидатами.",
        "ENTRY_EXPIRED/INVALIDATED учитываются как 0R на выданного кандидата.",
        "Отрицательные risk-пенальти никогда не ослабляются.",
        "Связанные факторы capped по семьям timing / flow / relative strength.",
        "Повышение веса проходит Benjamini–Hochberg FDR-контроль по всем факторам.",
    ]
    for tf in ("1H","15M"):
        lines += ["",f"<b>{tf}</b>"]
        cards=scorecards(tf)
        for name in FEATURES:
            c=cards[name]
            lines.append(
                f"• {labels[name]}: <b>w={c.weight:.2f}</b> · {c.status} · "
                f"+{c.positive_n}/0:{c.control_n}/−:{c.negative_n} · Δ <b>{c.effect_r:+.2f}R</b>"
            )
    lines += [
        "",
        "Вес выше 1.00 возможен только после ≥40 positive и ≥40 neutral-control закрытых наблюдений.",
        "Shadow-кандидаты не отправляются в Telegram и не входят в production win rate.",
    ]
    return "\n".join(lines)
