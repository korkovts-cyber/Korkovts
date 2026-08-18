"""V11 Telegram UI and detail panels."""

from __future__ import annotations

import json
import math
import sqlite3
from types import SimpleNamespace
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import DATABASE_PATH
from v1171_sqlite import db_session
from v11_engine import challenger_summary
from v11_live import health as live_health
from v11_manager import lifecycle_status, lifecycle_status_by_id
from v112_details import signal_row, snapshot_row
from v1170_calibration import futures as futures_probability, short_text as probability_text


def signal_actions(symbol=None,timeframe=None,signal_id=None,snapshot_id=None):
    """Premium compact action panel for a rendered signal."""
    if signal_id is not None:
        ref="v11i"; ident=str(int(signal_id))
    elif snapshot_id is not None:
        ref="v11s"; ident=str(int(snapshot_id))
    else:
        sym=str(symbol or "").upper()
        tf=str(timeframe or "").upper()
        suffix=f":{tf}" if tf else ""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🧠 Разбор",callback_data=f"v11:why:{sym}{suffix}"),
                InlineKeyboardButton("🛡 Риск",callback_data=f"v11:risk:{sym}{suffix}"),
            ],
            [
                InlineKeyboardButton("📊 Метрики",callback_data=f"v11:stats:{sym}{suffix}"),
                InlineKeyboardButton("📡 Live статус",callback_data=f"v11:life:{sym}{suffix}"),
            ],
            [InlineKeyboardButton("⌂  YK CONTROL CENTER",callback_data="v11:menu")],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧠 Разбор",callback_data=f"{ref}:why:{ident}"),
            InlineKeyboardButton("🛡 Риск",callback_data=f"{ref}:risk:{ident}"),
        ],
        [
            InlineKeyboardButton("📊 Метрики",callback_data=f"{ref}:stats:{ident}"),
            InlineKeyboardButton("📡 Live статус",callback_data=f"{ref}:life:{ident}"),
        ],
        [InlineKeyboardButton("⌂  YK CONTROL CENTER",callback_data="v11:menu")],
    ])

def main_menu(analyze_symbol=None):
    """Compact control-center layout; all existing actions remain reachable."""
    rows=[
        [InlineKeyboardButton("🏆  PRIME FUTURES",callback_data="scan")],
        [
            InlineKeyboardButton("⚡ FAST · 15M",callback_data="short_scan"),
            InlineKeyboardButton("🟢 SPOT · SWING",callback_data="v115:spot"),
        ],
        [
            InlineKeyboardButton("🚨 ENTRY NOW",callback_data="v1142:entrynow"),
            InlineKeyboardButton("👁 WATCHTOWER",callback_data="v116:spotwatch"),
        ],
        [
            InlineKeyboardButton("📍 FUTURES LIVE",callback_data="v1142:active"),
            InlineKeyboardButton("📍 SPOT LIVE",callback_data="v117:spotactive"),
        ],
    ]
    if analyze_symbol:
        sym=str(analyze_symbol).upper()
        coin=sym.removesuffix("USDT")
        rows.append([InlineKeyboardButton(f"🔎 ПЕРЕПРОВЕРИТЬ · {coin}",callback_data=f"analyze:{sym}")])
    rows += [
        [
            InlineKeyboardButton("🎯 PRECISION",callback_data="v113:meta"),
            InlineKeyboardButton("🧪 EDGE LAB",callback_data="v118:edgelab"),
        ],
        [
            InlineKeyboardButton("🧭 MANAGER",callback_data="v118:manager"),
            InlineKeyboardButton("🧬 FACTORS",callback_data="v112:lab"),
        ],
        [
            InlineKeyboardButton("📊 SPOT STATS",callback_data="v115:spotstats"),
            InlineKeyboardButton("🗂 SPOT HISTORY",callback_data="v115:spothistory"),
        ],
        [
            InlineKeyboardButton("📰 NEWS",callback_data="news"),
            InlineKeyboardButton("🚀 MOVERS",callback_data="movers"),
        ],
        [
            InlineKeyboardButton("🛡 SAFETY",callback_data="v1142:safety"),
            InlineKeyboardButton("🎯 ENTRY QUALITY",callback_data="v114:entry"),
        ],
        [
            InlineKeyboardButton("🧪 ROBUST",callback_data="v113:robust"),
            InlineKeyboardButton("🧫 LAB",callback_data="lab"),
        ],
        [
            InlineKeyboardButton("🛡 SYSTEM",callback_data="system"),
            InlineKeyboardButton("📡 HEALTH",callback_data="v112:health"),
        ],
        [
            InlineKeyboardButton("🗂 HISTORY",callback_data="status"),
            InlineKeyboardButton("🧠 MEMORY · 24H",callback_data="memory"),
        ],
        [
            InlineKeyboardButton("🔔 AUTO · ON",callback_data="alerts_on"),
            InlineKeyboardButton("🔕 AUTO · OFF",callback_data="alerts_off"),
        ],
        [InlineKeyboardButton("🧹 Очистить чат",callback_data="clear_chat")],
    ]
    return InlineKeyboardMarkup(rows)

def _grade_icon(g):
    return {"A+":"💎","A":"🏆","B+":"✅","B":"🟡"}.get(str(g),"⚪")


def card(s,priority=False):
    """Premium Telegram signal card: compact first screen, dense data kept below."""
    short=str(getattr(s,"timeframe","")).upper()=="15M"
    entry_state=str(getattr(s,"entry_now_state","SETUP") or "SETUP").upper()
    if entry_state=="ENTER_NOW":
        title=("🚨 <b>YK PRIME · №1 · ВХОД СЕЙЧАС</b>" if priority else "🚨 <b>YK SIGNAL · ВХОД СЕЙЧАС</b>")
    elif entry_state in ("ARMED","READY_PENDING"):
        title=("🏆 <b>YK PRIME · №1 ОСНОВНАЯ СДЕЛКА</b>" if priority and not short
               else "⚡ <b>YK FAST · №1 КРАТКОСРОЧНАЯ</b>" if priority
               else "▫️ <b>YK SIGNAL · СЕТАП</b>")
    elif entry_state=="COOLDOWN":
        title="🧊 <b>YK SIGNAL · COOLDOWN</b>"
    elif priority:
        title="⚡ <b>YK FAST · №1 КРАТКОСРОЧНАЯ</b>" if short else "🏆 <b>YK PRIME · №1 ОСНОВНАЯ СДЕЛКА</b>"
    else:
        title="▫️ <b>YK FAST · АЛЬТЕРНАТИВА</b>" if short else "▫️ <b>YK SIGNAL · АЛЬТЕРНАТИВА</b>"

    side_icon="🟢" if str(s.side).upper()=="LONG" else "🔴"
    side=str(s.side).upper()
    rank=float(getattr(s,"professional_rank",getattr(s,"score",0)) or 0)
    decision=float(getattr(s,"decision_priority",rank) or rank)
    grade=str(getattr(s,"professional_grade","—"))
    health=float(getattr(s,"data_health_score",0) or 0)
    exe=float(getattr(s,"execution_quality",0) or 0)
    impact=float(getattr(s,"impact_1k_bps",0) or 0)
    impact_text="n/a" if bool(getattr(s,"liquidity_check_unavailable",False)) else f"{impact:.1f} bps"
    alpha=float(getattr(s,"alpha_adjustment",0) or 0)
    fresh=float(getattr(s,"alpha_fresh_score",0) or 0)
    l2_state=str(getattr(s,"l2_state","—") or "—")
    l2_imb=float(getattr(s,"l2_signed_imbalance_10",0) or 0)
    micro=str(getattr(s,"micro_label","—") or "—")
    meta_status=str(getattr(s,"meta_status","LEARNING") or "LEARNING")
    meta_score=float(getattr(s,"meta_score",.5) or .5)
    entry_score=float(getattr(s,"entry_now_score",0) or 0)
    entry_price=float(getattr(s,"entry_now_price",0) or 0)
    entry_streak=int(getattr(s,"entry_now_streak",0) or 0)
    entry_reason=str(getattr(s,"entry_now_reason","") or "")
    probability=futures_probability(s)
    evidence=((getattr(s,"feature_snapshot",{}) or {}).get("evidence_v117") or {})
    edge=getattr(s,"expected_net_r",None)
    edge_lcb=getattr(s,"expected_net_r_lcb",None)
    edge_n=int(getattr(s,"edge_sample_n",0) or 0)
    edge_days=int(getattr(s,"edge_block_days",0) or 0)
    edge_prob=getattr(s,"edge_positive_probability",None)
    guard=str(getattr(s,"protection_label","CLEAR") or "CLEAR").upper()
    guard_penalty=float(getattr(s,"protection_penalty",0) or 0)
    coherence=((getattr(s,"feature_snapshot",{}) or {}).get("data_coherence_v11100") or {})
    coherence_status=str(coherence.get("status","UNOBSERVABLE") or "UNOBSERVABLE")
    stability=str(getattr(s,"selection_stability_label","") or "")
    stability_gap=getattr(s,"selection_priority_gap",None)
    stability_consensus=getattr(s,"selection_base_consensus",None)

    if edge is None:
        edge_line=f"🧮 Robust edge <b>LEARNING</b> · n <b>{edge_n}</b> · days <b>{edge_days}</b>"
    else:
        lcb_text="n/a" if edge_lcb is None else f"{float(edge_lcb):+.2f}R"
        prob_text="n/a" if edge_prob is None else f"{float(edge_prob)*100:.0f}%"
        edge_line=(f"🧮 Net-R <b>{float(edge):+.2f}R</b> · LCB90 <b>{lcb_text}</b> · "
                   f"days <b>{edge_days}</b> · Boot P(edge&gt;0) <b>{prob_text}</b>")
    guard_line=(f"🛡 Guard <b>{escape(guard)}</b>"
                + (f" · penalty <b>-{guard_penalty:.1f} PRO</b>" if guard_penalty>0 else "")
                + f" · Data coherence <b>{escape(coherence_status)}</b>")
    stability_line=""
    if priority and stability:
        gap_text="solo" if stability_gap is None else f"gap {float(stability_gap):.1f}"
        consensus_text=("PRO consensus" if stability_consensus else "edge reordered")
        stability_line=f"\n🎚 #1 stability <b>{escape(stability)}</b> · {escape(gap_text)} · {escape(consensus_text)}"

    if entry_state=="ENTER_NOW":
        status=(f"🚨 <b>ENTRY NOW CONFIRMED · 2/2</b>\n"
                f"Readiness <b>{entry_score:.0f}/100</b> · live <b>{entry_price:.8g}</b>\n"
                "<b>Действие:</b> вход разрешён только внутри ENTRY ZONE")
    elif entry_state=="READY_PENDING":
        status=(f"🟠 <b>READY · {max(1,entry_streak)}/2</b> · Readiness <b>{entry_score:.0f}/100</b>\n"
                "<b>НЕ ВХОДИТЬ СЕЙЧАС</b> · ждать повторное подтверждение")
    elif entry_state=="ARMED":
        status=(f"🟡 <b>ARMED</b> · Readiness <b>{entry_score:.0f}/100</b>\n"
                "<b>НЕ ВХОДИТЬ СЕЙЧАС</b> · ждать micro-trigger")
    elif entry_state=="COOLDOWN":
        status="🧊 <b>COOLDOWN</b> · повторный Production-вход временно заблокирован"
    else:
        status="⚪ <b>SETUP</b> · <b>НЕ ВХОДИТЬ СЕЙЧАС</b> · ENTRY NOW пока не подтверждён"

    reasons=list(dict.fromkeys(getattr(s,"reasons",[]) or []))[:2]
    why=("\n".join(f"  • {escape(str(x))}" for x in reasons) if reasons else "  • условия собраны, ждём точный триггер")
    sample=int(getattr(s,"cohort_sample",0) or 0)
    history=""
    if sample:
        pf=float(getattr(s,"cohort_pf",0) or 0)
        pf_text="∞" if pf>=999 else f"{pf:.2f}"
        history=(f"\n📚 Forward cohort · n <b>{sample}</b> · WR <b>{float(getattr(s,'cohort_win_rate',0))*100:.0f}%</b> · "
                 f"Exp <b>{float(getattr(s,'cohort_expectancy_r',0)):+.2f}R</b> · PF <b>{pf_text}</b>")

    plan=(f"ENTRY   {float(s.entry_low):.8g} — {float(s.entry_high):.8g}\n"
          f"STOP    {float(s.stop):.8g}\n"
          f"TP1     {float(s.tp1):.8g}\n"
          f"TP2     {float(s.tp2):.8g}\n"
          f"TP3     {float(s.tp3):.8g}")
    probability_label=escape(probability_text(probability))
    return (
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{escape(str(s.symbol))}</b>  ·  {side_icon} <b>{escape(side)}</b>  ·  <b>{escape(str(s.timeframe))}</b>\n"
        f"{_grade_icon(grade)} <b>{escape(grade)}</b>  ·  PRO <b>{rank:.0f}</b>  ·  PRIORITY <b>{decision:.0f}</b>\n"
        f"{edge_line}\n"
        f"📈 Фактическая forward-оценка <b>{probability_label}</b>\n\n"
        "⚡ <b>СТАТУС</b>\n"
        f"<blockquote>{status}</blockquote>\n"
        + (f"🔎 <i>{escape(entry_reason[:150])}</i>\n" if entry_reason else "")
        + "\n🎯 <b>ТОРГОВЫЙ ПЛАН</b>\n"
        f"<pre>{plan}</pre>\n"
        f"⚖️ RR <b>1:{float(s.rr):.1f}</b>  ·  max leverage <b>{int(s.leverage)}×</b>\n\n"
        "✨ <b>ПОЧЕМУ ЭТОТ СИГНАЛ</b>\n"
        f"{why}\n\n"
        "🧠 <b>AI INTELLIGENCE</b>\n"
        f"🧪 Evidence <b>{int(evidence.get('support',0))}/{int(evidence.get('conflict',0))}</b> support/conflict\n"
        f"📡 Data <b>{health:.0f}</b> · Execution <b>{exe:.0f}</b> · Impact <b>{impact_text}</b>\n"
        f"🌊 L2 <b>{escape(l2_state)}</b> · imbalance <b>{l2_imb:+.0%}</b> · {escape(micro)}\n"
        f"🧠 Alpha <b>{alpha:+.1f}</b> · Fresh <b>{fresh:.0f}</b> · Meta <b>{escape(meta_status)} {meta_score:.2f}</b>\n"
        f"{guard_line}{stability_line}"
        f"{history}\n\n"
        f"🕒 <b>{escape(str(getattr(s,'expected_window','—')))}</b> · review {escape(str(getattr(s,'review_window','—')))}\n"
        "<i>YK rule: ENTRY NOW → только зона. STOP → полный выход. Не расширять первоначальный риск.</i>\n"
        "<i>PRO/Readiness — индексы условий, не вероятность прибыли.</i>"
    )

def _latest(symbol,timeframe=None):
    with db_session() as c:
        c.row_factory=sqlite3.Row
        if timeframe:
            row=c.execute("""
                SELECT * FROM signals
                WHERE symbol=? AND timeframe=? AND COALESCE(is_shadow,0)=0
                ORDER BY id DESC LIMIT 1
            """,(str(symbol).upper(),str(timeframe).upper())).fetchone()
        else:
            row=c.execute("""
                SELECT * FROM signals
                WHERE symbol=? AND COALESCE(is_shadow,0)=0
                ORDER BY id DESC LIMIT 1
            """,(str(symbol).upper(),)).fetchone()
    return dict(row) if row else None


def _feature(row):
    try: return json.loads(row.get("feature_json") or "{}")
    except Exception: return {}


def why_text(symbol,timeframe=None):
    row=_latest(symbol,timeframe)
    if not row:
        return "⚪ Детали этого сигнала ещё не сохранены."
    f=_feature(row); t=f.get("technical",{}) or {}; d=f.get("derivatives",{}) or {}; v=f.get("v11",{}) or {}
    lines=[
        f"🔍 <b>ПОЧЕМУ {escape(str(row['symbol']))}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"Сценарий: <b>{escape(str(row.get('setup_type') or '—'))}</b>",
        f"Направление: <b>{escape(str(row.get('side') or '—'))}</b> · TF <b>{escape(str(row.get('timeframe') or '—'))}</b>",
    ]
    if t:
        lines += [
            "",
            "<b>Техника</b>",
            f"ADX <b>{float(t.get('adx',0)):.1f}</b> · RSI <b>{float(t.get('rsi',0)):.1f}</b>",
            f"Efficiency <b>{float(t.get('efficiency20',0)):.2f}</b> · EMA20 distance <b>{float(t.get('distance_ema20_atr',0)):+.2f} ATR</b>",
        ]
    if d:
        lines += [
            "",
            "<b>Деривативы</b>",
            f"OI Δ <b>{float(d.get('oi_change_pct',0)):+.2f}%</b> · taker <b>{float(d.get('taker_ratio',1)):.2f}</b>",
            f"Spread <b>{float(d.get('spread_bps',0)):.2f}bps</b> · basis <b>{float(d.get('basis_bps',0)):+.1f}bps</b>",
            f"ADL <b>{escape(str(d.get('adl_risk','—')).upper())}</b>",
        ]
    if v:
        lines += [
            "",
            f"Champion <b>{float(v.get('champion_rank',0)):.1f}</b> · Challenger <b>{float(v.get('challenger_rank',0)):.1f}</b>",
        ]
    ev=f.get("evidence_v117") or {}
    if ev:
        lines += [
            "",
            f"<b>Independent evidence</b> · support <b>{int(ev.get('support',0))}</b> · conflict <b>{int(ev.get('conflict',0))}</b>",
        ]
        for fam in (ev.get("families") or [])[:8]:
            icon="✅" if fam.get("state")=="SUPPORT" else ("❌" if fam.get("state")=="CONFLICT" else "➖")
            lines.append(f"{icon} {escape(str(fam.get('name','?')))}: {escape(str(fam.get('detail','')))}")
    alpha=f.get("alpha_v112") or f.get("alpha_v111",{}) or {}
    if alpha:
        lines += [
            "",
            "<b>Alpha V11.4</b>",
            f"Fresh <b>{float(alpha.get('fresh_score',0)):.0f}/100</b> · RelMom <b>{float(alpha.get('momentum_percentile',50)):.0f}p</b>",
            f"OFI recent/closed-5m <b>{float(alpha.get('ofi_recent',alpha.get('ofi_1m',0))):+.0%}/{float(alpha.get('ofi_5m',0)):+.0%}</b>",
            f"Agg sample coverage <b>{float(alpha.get('agg_coverage_sec',0)):.0f}s</b>",
            f"BTC residual {escape(str(alpha.get('residual_horizon','6h')))} <b>{float(alpha.get('residual_pct',alpha.get('residual_6h_pct',0))):+.2f}%</b> · beta <b>{float(alpha.get('beta',1)):.2f}</b>",
            f"Корректировка: <b>{float(alpha.get('weighted_adjustment',alpha.get('raw_adjustment',alpha.get('adjustment',0)))):+.1f}</b>",
            f"Факторы: <b>{escape(', '.join(map(str,alpha.get('notes') or [])) or 'нейтрально')}</b>",
        ]
    l2=f.get("execution_v113") or {}
    micro=f.get("micro_v113") or {}
    meta=f.get("meta_v113") or {}
    if l2:
        lines += [
            "",
            "<b>L2 state</b>",
            f"State <b>{escape(str(l2.get('l2_state','—')))}</b> · signed imbalance <b>{float(l2.get('signed_imbalance_10bps',0)):+.0%}</b>",
            f"Microprice <b>{float(l2.get('signed_microprice_bias_bps',0)):+.2f}bps</b> · depth10 <b>${float(l2.get('depth_10bps_usd',0)):,.0f}</b>",
        ]
    if micro:
        lines += [f"Micro decision <b>{escape(str(micro.get('label','—')))}</b> · adjustment <b>{float(micro.get('adjustment',0)):+.1f}</b>"]
    if meta:
        lines += [
            "",
            "<b>Meta Precision</b>",
            f"Status <b>{escape(str((meta.get('report') or {}).get('status','LEARNING')))}</b> · score <b>{float(meta.get('score',.5)):.2f}</b>",
            f"Gate <b>{float(meta.get('threshold',.60)):.2f}</b> · active <b>{'YES' if meta.get('ready') else 'NO'}</b>",
        ]
    return "\n".join(lines)


def risk_text(symbol,timeframe=None):
    row=_latest(symbol,timeframe)
    if not row: return "⚪ Риск-данные сигнала ещё не сохранены."
    f=_feature(row); v=f.get("v11",{}) or {}; d=f.get("derivatives",{}) or {}
    execution=f.get("execution_v113") or f.get("execution_v1121",{}) or {}
    issues=v.get("issues") or []
    return "\n".join([
        f"🛡 <b>РИСК · {escape(str(row['symbol']))}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"Data Health: <b>{float(v.get('data_health',0)):.0f}/100</b>",
        f"Execution: <b>{float(v.get('execution_quality',0)):.0f}/100</b>",
        f"$1k impact: <b>{'н/д' if execution.get('liquidity_check_unavailable') else f'{float(v.get("impact_1k_bps",0)):.1f}bps'}</b>",
        f"$5k impact: <b>{'н/д' if execution.get('liquidity_check_unavailable') else f'{float(v.get("impact_5k_bps",0)):.1f}bps'}</b>",
        f"Spread: <b>{float(d.get('spread_bps',0)):.2f}bps</b>",
        f"ADL: <b>{escape(str(d.get('adl_risk','—')).upper())}</b>",
        f"Флаги: <b>{escape(', '.join(map(str,issues)) if issues else 'нет критических')}</b>",
        "",
        "Бот оценивает рыночный риск, но не знает, какую позицию пользователь реально открыл.",
    ])


def stats_text(symbol,timeframe=None):
    row=_latest(symbol,timeframe)
    if not row: return "⚪ Статистика сетапа пока недоступна."
    f=_feature(row); v=f.get("v11",{}) or {}
    proxy=SimpleNamespace(
        setup_type=row.get("setup_type"),timeframe=row.get("timeframe"),side=row.get("side"),
        market_context=f.get("market") or {},production_regime=row.get("market_regime") or "",
    )
    calibrated=futures_probability(proxy)
    cohort=v.get("cohort",{}) or {}; drift=v.get("drift",{}) or {}
    pf=float(cohort.get("profit_factor",0) or 0)
    pf_text="∞" if pf>=999 else f"{pf:.2f}"
    rpf=float(drift.get("recent_pf",0) or 0); bpf=float(drift.get("baseline_pf",0) or 0)
    return "\n".join([
        f"📊 <b>СТАТИСТИКА · {escape(str(row['symbol']))}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"{escape(str(row.get('timeframe')))} · {escape(str(row.get('side')))} · {escape(str(row.get('setup_type') or '—'))}",
        f"Выборка: <b>{int(cohort.get('sample',0) or 0)}</b>",
        f"Win rate: <b>{float(cohort.get('win_rate',0))*100:.0f}%</b>",
        f"Expectancy: <b>{float(cohort.get('expectancy_r',0)):+.2f}R</b>",
        f"Profit factor: <b>{pf_text}</b>",
        f"Фактическая forward-оценка: <b>{escape(probability_text(calibrated))}</b>",
        "",
        f"Drift: <b>{escape(str(drift.get('label','нет данных')))}</b>",
        f"Recent Exp: <b>{float(drift.get('recent_expectancy',0)):+.2f}R</b> · PF <b>{rpf:.2f}</b>",
        f"Baseline Exp: <b>{float(drift.get('baseline_expectancy',0)):+.2f}R</b> · PF <b>{bpf:.2f}</b>",
    ])


def life_text(symbol,timeframe=None):
    data=lifecycle_status(symbol,timeframe)
    if not data: return "⚪ Сигнал для этого символа не найден."
    s=data["signal"]; l=data["live"]
    status=str(s.get("status") or "—")
    result=str(s.get("result") or "—")
    lines=[
        f"🔄 <b>СТАТУС · {escape(str(s['symbol']))}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"Состояние: <b>{escape(status)}</b>",
        f"Результат: <b>{escape(result)}</b>",
        f"Entry: <b>{float(s.get('entry') or 0):.8g}</b>",
    ]
    if s.get("activated_at"): lines.append(f"Активирован: <b>{escape(str(s['activated_at'])[:19])}</b>")
    if l:
        if l.get("last_price") is not None: lines.append(f"Live: <b>{float(l['last_price']):.8g}</b>")
        lines.append(f"Max R live: <b>{float(l.get('max_r') or 0):+.2f}R</b>")
        lines.append(f"TP1 live: <b>{'ДА' if l.get('tp1_seen') else 'НЕТ'}</b> · TP2 live: <b>{'ДА' if l.get('tp2_seen') else 'НЕТ'}</b>")
        if l.get("last_event"): lines.append(f"Последнее событие: <b>{escape(str(l['last_event']))}</b>")
    if s.get("pnl_r") is not None: lines.append(f"Forward-test: <b>{float(s['pnl_r']):+.2f}R</b>")
    return "\n".join(lines)


def _why_from_row(row):
    if not row: return "⚪ Детали сигнала не найдены."
    f=_feature(row); t=f.get("technical",{}) or {}; d=f.get("derivatives",{}) or {}; v=f.get("v11",{}) or {}
    lines=[
        f"🔍 <b>ПОЧЕМУ {escape(str(row.get('symbol','?')))}</b> · {escape(str(row.get('timeframe','—')))}",
        "━━━━━━━━━━━━━━━━━━",
        f"Сценарий: <b>{escape(str(row.get('setup_type') or '—'))}</b>",
        f"Направление: <b>{escape(str(row.get('side') or '—'))}</b>",
    ]
    if t:
        lines += ["", "<b>Техника</b>",
                  f"ADX <b>{float(t.get('adx',0)):.1f}</b> · RSI <b>{float(t.get('rsi',0)):.1f}</b>",
                  f"Efficiency <b>{float(t.get('efficiency20',0)):.2f}</b> · EMA20 distance <b>{float(t.get('distance_ema20_atr',0)):+.2f} ATR</b>"]
    if d:
        lines += ["", "<b>Деривативы</b>",
                  f"OI Δ <b>{float(d.get('oi_change_pct',0)):+.2f}%</b> · taker <b>{float(d.get('taker_ratio',1)):.2f}</b>",
                  f"Spread <b>{float(d.get('spread_bps',0)):.2f}bps</b> · basis <b>{float(d.get('basis_bps',0)):+.1f}bps</b>",
                  f"ADL <b>{escape(str(d.get('adl_risk','—')).upper())}</b>"]
    alpha=f.get("alpha_v112") or {}
    if alpha:
        lines += ["", "<b>Alpha V11.4</b>",
                  f"Fresh <b>{float(alpha.get('fresh_score',0)):.0f}/100</b> · RelMom <b>{float(alpha.get('momentum_percentile',50)):.0f}p</b>",
                  f"OFI recent/5m <b>{float(alpha.get('ofi_recent',0)):+.0%}/{float(alpha.get('ofi_5m',0)):+.0%}</b>",
                  f"BTC residual {escape(str(alpha.get('residual_horizon','—')))} <b>{float(alpha.get('residual_pct',0)):+.2f}%</b>",
                  f"Alpha adjustment <b>{float(alpha.get('weighted_adjustment',alpha.get('raw_adjustment',0))):+.1f}</b>"]
    ev=f.get("evidence_v117") or {}
    if ev:
        lines += ["",f"<b>Independent evidence</b> · support <b>{int(ev.get('support',0))}</b> · conflict <b>{int(ev.get('conflict',0))}</b>"]
        for fam in (ev.get("families") or [])[:8]:
            icon="✅" if fam.get("state")=="SUPPORT" else ("❌" if fam.get("state")=="CONFLICT" else "➖")
            lines.append(f"{icon} {escape(str(fam.get('name','?')))}: {escape(str(fam.get('detail','')))}")
    return "\n".join(lines)


def _risk_from_row(row):
    if not row: return "⚪ Риск-данные сигнала не найдены."
    f=_feature(row); v=f.get("v11",{}) or {}; d=f.get("derivatives",{}) or {}; ex=f.get("execution_v113") or f.get("execution_v1121",{}) or {}
    issues=v.get("issues") or []
    unavailable=bool(ex.get("liquidity_check_unavailable"))
    impact1="н/д" if unavailable else f"{float(v.get('impact_1k_bps',0)):.1f}bps"
    impact5="н/д" if unavailable else f"{float(v.get('impact_5k_bps',0)):.1f}bps"
    return "\n".join([
        f"🛡 <b>РИСК · {escape(str(row.get('symbol','?')))}</b> · {escape(str(row.get('timeframe','—')))}",
        "━━━━━━━━━━━━━━━━━━",
        f"Data Health: <b>{float(v.get('data_health',0)):.0f}/100</b>",
        f"Execution: <b>{float(v.get('execution_quality',0)):.0f}/100</b>",
        f"$1k impact: <b>{impact1}</b> · $5k: <b>{impact5}</b>",
        f"Spread: <b>{float(d.get('spread_bps',0)):.2f}bps</b>",
        f"ADL: <b>{escape(str(d.get('adl_risk','—')).upper())}</b>",
        f"L2: <b>{escape(str(ex.get('l2_state','—')))}</b> · signed imbalance <b>{float(ex.get('signed_imbalance_10bps',0)):+.0%}</b>",
        f"Флаги: <b>{escape(', '.join(map(str,issues)) if issues else 'нет критических')}</b>",
    ])


def _stats_from_row(row):
    if not row: return "⚪ Статистика сетапа не найдена."
    f=_feature(row); v=f.get("v11",{}) or {}; cohort=v.get("cohort",{}) or {}; drift=v.get("drift",{}) or {}
    proxy=SimpleNamespace(
        setup_type=row.get("setup_type"),timeframe=row.get("timeframe"),side=row.get("side"),
        market_context=f.get("market") or {},production_regime=row.get("market_regime") or "",
    )
    calibrated=futures_probability(proxy)
    pf=float(cohort.get("profit_factor",0) or 0); pf_text="∞" if pf>=999 else f"{pf:.2f}"
    return "\n".join([
        f"📊 <b>СТАТИСТИКА · {escape(str(row.get('symbol','?')))}</b> · {escape(str(row.get('timeframe','—')))}",
        "━━━━━━━━━━━━━━━━━━",
        f"{escape(str(row.get('side','—')))} · {escape(str(row.get('setup_type') or '—'))}",
        f"Выборка: <b>{int(cohort.get('sample',0) or 0)}</b> · WR <b>{float(cohort.get('win_rate',0))*100:.0f}%</b>",
        f"Expectancy <b>{float(cohort.get('expectancy_r',0)):+.2f}R</b> · PF <b>{pf_text}</b>",
        f"Фактическая forward-оценка: <b>{escape(probability_text(calibrated))}</b>",
        f"Drift: <b>{escape(str(drift.get('label','нет данных')))}</b>",
        f"Recent/Baseline Exp: <b>{float(drift.get('recent_expectancy',0)):+.2f}R / {float(drift.get('baseline_expectancy',0)):+.2f}R</b>",
    ])


def detail_by_signal_id(action,signal_id):
    row=signal_row(signal_id)
    if action=="why": return _why_from_row(row)
    if action=="risk": return _risk_from_row(row)
    if action=="stats": return _stats_from_row(row)
    if action=="life":
        data=lifecycle_status_by_id(signal_id)
        if not data: return "⚪ Статус этого сигнала не найден."
        s=data["signal"]; l=data["live"]
        lines=[f"🔄 <b>СТАТУС · {escape(str(s['symbol']))}</b> · {escape(str(s['timeframe']))}",
               "━━━━━━━━━━━━━━━━━━",f"Состояние: <b>{escape(str(s.get('status') or '—'))}</b>",
               f"Результат: <b>{escape(str(s.get('result') or '—'))}</b>",
               f"Entry: <b>{float(s.get('entry') or 0):.8g}</b>"]
        if s.get("activated_at"): lines.append(f"Активирован: <b>{escape(str(s['activated_at'])[:19])}</b>")
        if l:
            if l.get("last_price") is not None: lines.append(f"Live: <b>{float(l['last_price']):.8g}</b>")
            lines.append(f"Max R live: <b>{float(l.get('max_r') or 0):+.2f}R</b>")
            lines.append(f"TP1: <b>{'ДА' if l.get('tp1_seen') else 'НЕТ'}</b> · TP2: <b>{'ДА' if l.get('tp2_seen') else 'НЕТ'}</b>")
        if s.get("pnl_r") is not None: lines.append(f"Forward-test: <b>{float(s['pnl_r']):+.2f}R</b>")
        return "\n".join(lines)
    return "⚪ Неизвестный раздел."


def detail_by_snapshot_id(action,snapshot_id):
    row=snapshot_row(snapshot_id)
    if not row: return "⚪ Снимок сигнала уже недоступен."
    if action=="why": return _why_from_row(row)
    if action=="risk": return _risk_from_row(row)
    if action=="stats": return _stats_from_row(row)
    if action=="life":
        linked=row.get("_signal_id")
        if linked:
            return detail_by_signal_id("life",linked)
        return "🔄 <b>ПОВТОРНЫЙ РУЧНОЙ АНАЛИЗ</b>\nЭтот снимок не является отдельной production-сделкой и не отслеживается как новая позиция."
    return "⚪ Неизвестный раздел."


def system_extra():
    h=live_health(); ch=challenger_summary()
    ws=("ONLINE" if h["connected"] and (h["last_age_sec"] is None or h["last_age_sec"]<30) else "DEGRADED")
    return (
        f"📡 Live WS: <b>{ws}</b> · symbols <b>{len(h['symbols'])}</b> · "
        f"flow <b>{int(h.get('fresh_flow',0))}</b> · reconnects <b>{h['reconnects']}</b>\n"
        f"🧪 Challenger audit: <b>{ch['audited']}</b> · closed <b>{ch['closed']}</b> · avg <b>{ch['avg_r']:+.2f}R</b>"
    )
