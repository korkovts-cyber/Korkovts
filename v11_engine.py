"""Korkovts V11.7.1 Production decision engine.

The app.strategy module remains the signal generator. This module can only
rank, downgrade, or suppress already-confirmed signals. It never fabricates a
trade to satisfy a desired signal frequency.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from app.config import DATABASE_PATH
from v1171_sqlite import db_session


@dataclass(frozen=True)
class Regime:
    name: str
    penalty: float
    note: str
    hard_pause: bool = False


@dataclass(frozen=True)
class Cohort:
    sample: int
    win_rate: float
    expectancy_r: float
    profit_factor: float
    penalty: float
    label: str


@dataclass(frozen=True)
class Drift:
    recent_n: int
    baseline_n: int
    recent_expectancy: float
    baseline_expectancy: float
    recent_pf: float
    baseline_pf: float
    penalty: float
    label: str


@dataclass(frozen=True)
class Metrics:
    champion_rank: float
    challenger_rank: float
    grade: str
    data_health: float
    execution_quality: float
    regime_quality: float
    eligible: bool
    issues: tuple[str, ...]
    cohort: Cohort
    drift: Drift


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def _pf(values):
    gains = sum(x for x in values if x > 0)
    losses = -sum(x for x in values if x < 0)
    if losses:
        return gains / losses
    return 999.0 if gains else 0.0


def classify_regime(state) -> Regime:
    state = state or {}
    raw = str(state.get("btc_bias_raw", state.get("bias", "NEUTRAL")))
    atr = float(state.get("btc_atr_pct", 0) or 0)
    breadth = state.get("breadth", {}) or {}
    dispersion = float(breadth.get("dispersion", 0) or 0)
    blocked = bool(state.get("breadth_blocked"))

    # Do not freeze the whole market merely because volatility is high:
    # strong trends often live in high-volatility regimes. Hard pause is
    # reserved for genuinely extreme/chaotic combinations.
    if atr >= 3.5 or (atr >= 2.5 and dispersion >= 8.0):
        return Regime("SHOCK", 10.0, "экстремальная волатильность и разброс", True)
    if atr >= 2.5:
        return Regime("EXTREME_VOL", 8.0, "очень высокая волатильность BTC")
    if dispersion >= 9.0:
        return Regime("HIGH_DISPERSION", 7.0, "аномально широкий разброс альткоинов")
    if blocked:
        return Regime("DIVERGENCE", 6.0, "BTC и ширина рынка расходятся")
    if atr >= 1.6 or dispersion >= 6.5:
        return Regime("HIGH_VOL", 5.0, "повышенная волатильность")
    if raw == "NEUTRAL" and atr <= .35:
        return Regime("RANGE_LOW_VOL", 5.0, "флэт и слабый импульс")
    if raw == "NEUTRAL":
        return Regime("RANGE", 3.0, "нет устойчивого направления BTC")
    if atr <= .30:
        return Regime("LOW_VOL_TREND", 2.0, "тренд при слабой волатильности")
    return Regime("TREND", 0.0, "нормальный направленный рынок")


def _cohort_rows(signal, limit=150):
    try:
        with db_session(timeout=10) as c:
            rows = c.execute(
                """
                SELECT pnl_r FROM signals
                WHERE timeframe=? AND side=? AND COALESCE(setup_type,'')=?
                  AND closed_at IS NOT NULL AND activated_at IS NOT NULL
                  AND COALESCE(is_shadow,0)=0
                  AND COALESCE(release_version,'') LIKE '11.7.1%'
                  AND result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                  AND COALESCE(result,'') NOT LIKE 'AMBIGUOUS%'
                  AND pnl_r IS NOT NULL
                ORDER BY closed_at DESC LIMIT ?
                """,
                (
                    str(getattr(signal, "timeframe", "")),
                    str(getattr(signal, "side", "")),
                    str(getattr(signal, "setup_type", "") or ""),
                    int(limit),
                ),
            ).fetchall()
        return [float(x[0]) for x in rows]
    except Exception:
        return []


def cohort_stats(signal, min_sample=20) -> Cohort:
    values = _cohort_rows(signal, 150)
    n = len(values)
    if not n:
        return Cohort(0, 0, 0, 0, 0, "нет истории")
    wins = sum(1 for x in values if x > 0)
    exp = sum(values) / n
    pf = _pf(values)
    penalty = 0.0
    label = "наблюдение"
    if n >= min_sample:
        if exp < -.15 or pf < .80:
            penalty = 8.0; label = "слабая историческая когорта"
        elif exp < 0 or pf < 1.0:
            penalty = 5.0; label = "история ниже нормы"
        elif exp < .15 or pf < 1.20:
            penalty = 2.0; label = "умеренная история"
        else:
            label = "история подтверждает сетап"
    return Cohort(n, wins/n, exp, pf, penalty, label)


def drift_stats(signal, recent_n=20, baseline_n=80) -> Drift:
    values = _cohort_rows(signal, recent_n + baseline_n)
    if len(values) < 15:
        return Drift(len(values), 0, 0, 0, 0, 0, 0, "недостаточно данных")

    recent = values[:min(recent_n, len(values))]
    baseline = values[len(recent):len(recent)+baseline_n]
    recent_exp = sum(recent)/len(recent) if recent else 0
    base_exp = sum(baseline)/len(baseline) if baseline else recent_exp
    recent_pf = _pf(recent)
    base_pf = _pf(baseline) if baseline else recent_pf

    penalty = 0.0
    label = "стабильно"

    # "Drift" means deterioration relative to a meaningful prior baseline.
    # A small bad sample without a baseline must not disable a setup.
    enough_recent = len(recent) >= 20
    enough_baseline = len(baseline) >= 30
    if enough_recent and enough_baseline:
        prior_worked = base_exp >= .08 and base_pf >= 1.10
        collapse = recent_exp < -.15 and recent_pf < .80
        meaningful_drop = (
            recent_exp < base_exp - .30
            and recent_pf < max(1.0, base_pf * .70)
        )
        if prior_worked and collapse:
            penalty = 8.0; label = "DRIFT: edge резко ухудшился"
        elif meaningful_drop:
            penalty = 5.0; label = "DRIFT: заметное ухудшение"
        elif recent_exp < 0 or recent_pf < 1:
            penalty = 3.0; label = "слабая последняя выборка"
    elif enough_recent and (recent_exp < 0 or recent_pf < 1):
        penalty = 2.0
        label = "последняя выборка слабая; baseline ещё мал"

    return Drift(
        len(recent), len(baseline), recent_exp, base_exp,
        recent_pf, base_pf, penalty, label
    )


def evaluate(signal, regime=None) -> Metrics:
    snap = getattr(signal, "feature_snapshot", {}) or {}
    d = snap.get("derivatives", {}) or {}
    news = snap.get("news", {}) or {}
    market = snap.get("market", {}) or getattr(signal, "market_context", {}) or {}

    if regime is None:
        regime = classify_regime(market)

    quality = int(getattr(signal, "data_quality", d.get("data_quality", 0)) or 0)
    total = max(1, int(getattr(signal, "data_quality_total", d.get("data_quality_total", 9)) or 9))
    ratio = clamp(quality / total * 100)
    spread = float(d.get("spread_bps", 999) or 999)
    cost_r = float(getattr(signal, "estimated_cost_r", 999) or 999)
    adl = str(getattr(signal, "adl_risk", d.get("adl_risk", "unknown")) or "unknown").lower()
    _adl_age=d.get("adl_age_minutes")
    adl_age = float(_adl_age) if _adl_age is not None else 9999.0
    impact_1k = float(getattr(signal, "impact_1k_bps", 0) or 0)
    impact_5k = float(getattr(signal, "impact_5k_bps", 0) or 0)
    liquidity_unavailable = bool(getattr(signal, "liquidity_check_unavailable", False))
    event_risk = float(news.get("event_risk", 0) or 0)

    issues = []
    eligible = True
    if ratio < 75:
        eligible = False; issues.append(f"data {quality}/{total}")
    if spread > 5:
        eligible = False; issues.append(f"spread {spread:.1f}bps")
    if cost_r > .25:
        eligible = False; issues.append(f"cost {cost_r:.2f}R")
    if adl not in ("low", "medium"):
        eligible = False; issues.append(f"ADL {adl.upper()}")
    if adl_age > 90:
        eligible = False; issues.append("ADL stale")
    if impact_1k > 10:
        eligible = False; issues.append(f"$1k impact {impact_1k:.1f}bps")
    if impact_5k > 35:
        eligible = False; issues.append(f"$5k impact {impact_5k:.1f}bps")
    if liquidity_unavailable:
        issues.append("deep liquidity unavailable")
    if regime.hard_pause:
        eligible = False; issues.append("market shock pause")

    data_health = ratio - (8 if adl == "medium" else 0)
    if adl_age > 60:
        data_health -= min(15, (adl_age - 60)/2)
    data_health = clamp(data_health)

    execution = clamp(
        100
        - 4 * max(0, spread)
        - 80 * max(0, cost_r)
        - 2.2 * max(0, impact_1k)
        - .35 * max(0, impact_5k)
        - (8 if liquidity_unavailable else 0)
    )
    regime_quality = clamp(100 - regime.penalty * 2 - (4 if event_risk >= .67 else 0))

    cohort = cohort_stats(signal)
    drift = drift_stats(signal)
    if drift.penalty >= 8:
        eligible = False
        issues.append("strategy drift")

    condition = clamp(getattr(signal, "score", 0))
    history_quality = clamp(100 - cohort.penalty * 7 - drift.penalty * 8)

    # CHAMPION: stable production weighting.
    champion = (
        condition * .56
        + data_health * .18
        + execution * .14
        + regime_quality * .07
        + history_quality * .05
    )
    champion -= cohort.penalty + drift.penalty
    champion = clamp(champion)

    # CHALLENGER: experimental weighting. It is NEVER used for Telegram order.
    challenger = (
        condition * .48
        + data_health * .20
        + execution * .16
        + regime_quality * .06
        + history_quality * .10
    )
    challenger -= cohort.penalty * .8 + drift.penalty
    challenger = clamp(challenger)

    if not eligible:
        champion = min(champion, 69.9)
    elif champion < 75:
        eligible = False
        issues.append("PRO rank below 75")
        champion = min(champion, 74.9)

    grade = (
        "A+" if champion >= 90 else
        "A" if champion >= 85 else
        "B+" if champion >= 80 else
        "B" if champion >= 75 else
        "WATCH"
    )
    return Metrics(
        champion, challenger, grade, data_health, execution,
        regime_quality, eligible, tuple(issues), cohort, drift
    )


def attach(signal, regime=None):
    m = evaluate(signal, regime)
    signal.professional_rank = m.champion_rank
    signal.challenger_rank = m.challenger_rank
    signal.professional_grade = m.grade
    signal.data_health_score = m.data_health
    signal.execution_quality = m.execution_quality
    signal.professional_eligible = m.eligible
    signal.professional_issues = list(m.issues)
    signal.cohort_sample = m.cohort.sample
    signal.cohort_win_rate = m.cohort.win_rate
    signal.cohort_expectancy_r = m.cohort.expectancy_r
    signal.cohort_pf = m.cohort.profit_factor
    signal.cohort_label = m.cohort.label
    signal.drift_label = m.drift.label
    signal.drift_penalty = m.drift.penalty
    signal.feature_snapshot.setdefault("v11", {}).update({
        "champion_rank": m.champion_rank,
        "challenger_rank": m.challenger_rank,
        "grade": m.grade,
        "data_health": m.data_health,
        "execution_quality": m.execution_quality,
        "regime_quality": m.regime_quality,
        "eligible": m.eligible,
        "issues": list(m.issues),
        "cohort": m.cohort.__dict__,
        "drift": m.drift.__dict__,
        "impact_1k_bps": float(getattr(signal, "impact_1k_bps", 0) or 0),
        "impact_5k_bps": float(getattr(signal, "impact_5k_bps", 0) or 0),
    })
    return signal


def select(signals: Iterable, max_results=4, regime=None):
    rows = [attach(s, regime) for s in signals]
    rows = [s for s in rows if getattr(s, "professional_eligible", False)]
    rows.sort(
        key=lambda s: (
            float(getattr(s, "professional_rank", 0)),
            float(getattr(s, "score", 0)),
            -float(getattr(s, "estimated_cost_r", 0)),
        ),
        reverse=True,
    )
    if not rows:
        return []

    # Priority #1 is always the strongest CHAMPION.
    selected = [rows[0]]
    used_clusters = {int(getattr(rows[0], "cluster_id", 0) or 0)}
    side_count = {str(getattr(rows[0], "side", "")): 1}

    # Alternatives first come from independent risk clusters.
    for s in rows[1:]:
        cluster = int(getattr(s, "cluster_id", 0) or 0)
        side = str(getattr(s, "side", ""))
        if cluster and cluster in used_clusters:
            continue
        if side_count.get(side, 0) >= 3:
            continue
        selected.append(s)
        if cluster:
            used_clusters.add(cluster)
        side_count[side] = side_count.get(side, 0) + 1
        if len(selected) >= max_results:
            return selected

    # Correlated alternatives only fill unused capacity.
    existing = {id(s) for s in selected}
    for s in rows[1:]:
        if id(s) in existing:
            continue
        side = str(getattr(s, "side", ""))
        if side_count.get(side, 0) >= 3:
            continue
        selected.append(s)
        side_count[side] = side_count.get(side, 0) + 1
        if len(selected) >= max_results:
            break
    return selected


def init_rank_audit():
    try:
        with db_session(timeout=10) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS v11_rank_audit(
                    signal_id INTEGER PRIMARY KEY,
                    champion_rank REAL,
                    challenger_rank REAL,
                    grade TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
    except Exception:
        pass


def record_rank_audit(signal_id, signal):
    init_rank_audit()
    try:
        with db_session(timeout=10) as c:
            c.execute(
                """INSERT OR REPLACE INTO v11_rank_audit
                   (signal_id,champion_rank,challenger_rank,grade)
                   VALUES(?,?,?,?)""",
                (
                    int(signal_id),
                    float(getattr(signal, "professional_rank", 0)),
                    float(getattr(signal, "challenger_rank", 0)),
                    str(getattr(signal, "professional_grade", "")),
                ),
            )
    except Exception:
        pass


def challenger_summary():
    init_rank_audit()
    try:
        with db_session(timeout=10) as c:
            row = c.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN s.status='CLOSED'
                                 AND s.result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                                 AND COALESCE(s.result,'') NOT LIKE 'AMBIGUOUS%'
                                THEN 1 ELSE 0 END),
                       AVG(CASE WHEN s.status='CLOSED'
                                 AND s.result NOT IN ('ENTRY_EXPIRED','INVALIDATED')
                                 AND COALESCE(s.result,'') NOT LIKE 'AMBIGUOUS%'
                                THEN s.pnl_r END)
                FROM v11_rank_audit a
                JOIN signals s ON s.id=a.signal_id
                WHERE COALESCE(s.release_version,'') LIKE '11.7.1%'
            """).fetchone()
        return {
            "audited": int(row[0] or 0),
            "closed": int(row[1] or 0),
            "avg_r": float(row[2] or 0),
        }
    except Exception:
        return {"audited":0,"closed":0,"avg_r":0.0}
