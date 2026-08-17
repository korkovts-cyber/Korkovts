"""V11 Telegram UI and detail panels."""

from __future__ import annotations

import json
import math
import sqlite3
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import DATABASE_PATH
from v11_engine import challenger_summary
from v11_live import health as live_health
from v11_manager import lifecycle_status


def signal_actions(symbol):
    s=str(symbol).upper()
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Почему?",callback_data=f"v11:why:{s}"),
            InlineKeyboardButton("🛡 Риск",callback_data=f"v11:risk:{s}"),
        ],
        [
            InlineKeyboardButton("📊 Статистика",callback_data=f"v11:stats:{s}"),
            InlineKeyboardButton("🔄 Статус сделки",callback_data=f"v11:life:{s}"),
        ],
        [InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ",callback_data="v11:menu")],
    ])


def main_menu(analyze_symbol=None):
    rows=[
        [InlineKeyboardButton("🏆 ОСНОВНОЙ СКАН",callback_data="scan")],
        [InlineKeyboardButton("⚡ КРАТКОСРОЧНЫЙ СКАН",callback_data="short_scan")],
    ]
    if analyze_symbol:
        s=str(analyze_symbol).upper()
        coin=s.removesuffix("USDT")
        rows += [
            [InlineKeyboardButton(f"🔎 ПЕРЕПРОВЕРИТЬ {coin}",callback_data=f"analyze:{s}")],
            [
                InlineKeyboardButton("🔍 Почему?",callback_data=f"v11:why:{s}"),
                InlineKeyboardButton("🔄 Статус",callback_data=f"v11:life:{s}"),
            ],
        ]
    rows += [
        [
            InlineKeyboardButton("🛡 СИСТЕМА",callback_data="system"),
            InlineKeyboardButton("🧪 ЛАБОРАТОРИЯ",callback_data="lab"),
        ],
        [
            InlineKeyboardButton("📰 НОВОСТИ",callback_data="news"),
            InlineKeyboardButton("🚀 ДВИЖЕНИЯ",callback_data="movers"),
        ],
        [
            InlineKeyboardButton("🔔 АВТО ВКЛ",callback_data="alerts_on"),
            InlineKeyboardButton("🔕 АВТО ВЫКЛ",callback_data="alerts_off"),
        ],
        [
            InlineKeyboardButton("🗂 ИСТОРИЯ",callback_data="status"),
            InlineKeyboardButton("🧠 ПАМЯТЬ 24Ч",callback_data="memory"),
        ],
        [
            InlineKeyboardButton("🧬 ФАКТОРЫ",callback_data="v112:lab"),
            InlineKeyboardButton("📡 HEALTH",callback_data="v112:health"),
        ],
        [InlineKeyboardButton("🧹 ОЧИСТИТЬ ЧАТ",callback_data="clear_chat")],
    ]
    return InlineKeyboardMarkup(rows)


def _grade_icon(g):
    return {"A+":"💎","A":"🏆","B+":"✅","B":"🟡"}.get(str(g),"⚪")


def card(s,priority=False):
    short=str(getattr(s,"timeframe","")).upper()=="15M"
    if priority:
        title="⚡ <b>№1 КРАТКОСРОЧНАЯ СДЕЛКА</b>" if short else "🏆 <b>№1 ОСНОВНАЯ СДЕЛКА</b>"
    else:
        title="📌 <b>КРАТКОСРОЧНАЯ АЛЬТЕРНАТИВА</b>" if short else "📌 <b>ОСНОВНАЯ АЛЬТЕРНАТИВА</b>"

    side="LONG 🟢" if s.side=="LONG" else "SHORT 🔴"
    rank=float(getattr(s,"professional_rank",getattr(s,"score",0)) or 0)
    grade=str(getattr(s,"professional_grade","—"))
    health=float(getattr(s,"data_health_score",0) or 0)
    exe=float(getattr(s,"execution_quality",0) or 0)
    impact=float(getattr(s,"impact_1k_bps",0) or 0)
    impact_text="н/д" if bool(getattr(s,"liquidity_check_unavailable",False)) else f"{impact:.1f}bps"
    drift=str(getattr(s,"drift_label","нет данных"))
    sample=int(getattr(s,"cohort_sample",0) or 0)
    alpha=float(getattr(s,"alpha_adjustment",0) or 0)
    alpha_raw=float(getattr(s,"alpha_raw_adjustment",alpha) or 0)
    fresh=float(getattr(s,"alpha_fresh_score",0) or 0)
    mom=float(getattr(s,"alpha_momentum_percentile",50) or 50)
    ofi=float(getattr(s,"alpha_ofi_5m",0) or 0)

    cluster=(
        f"#{getattr(s,'cluster_id',0)} · лидер"
        if int(getattr(s,"cluster_rank",1) or 1)==1 else
        f"#{getattr(s,'cluster_id',0)} · коррелирующая альтернатива"
    )
    history=""
    if sample:
        pf=float(getattr(s,"cohort_pf",0) or 0)
        pf_text="∞" if pf>=999 else f"{pf:.2f}"
        history=(
            f"\n📚 Когорта: <b>{sample}</b> · WR <b>{float(getattr(s,'cohort_win_rate',0))*100:.0f}%</b> · "
            f"Exp <b>{float(getattr(s,'cohort_expectancy_r',0)):+.2f}R</b> · PF <b>{pf_text}</b>"
        )

    return (
        f"{title}\n━━━━━━━━━━━━━━━━━━\n"
        f"<b>{escape(str(s.symbol))}</b> · {side} · <b>{escape(str(s.timeframe))}</b>\n"
        f"{_grade_icon(grade)} PRO <b>{rank:.1f}/100 · {escape(grade)}</b>\n"
        f"🧩 {escape(str(getattr(s,'setup_type','SETUP')))}\n"
        f"🧺 {escape(cluster)}\n\n"
        f"🎯 <b>ENTRY</b>  {s.entry_low:.8g} – {s.entry_high:.8g}\n"
        f"🛑 <b>STOP</b>   {s.stop:.8g}\n"
        f"✅ <b>TP1</b>    {s.tp1:.8g}\n"
        f"✅ <b>TP2</b>    {s.tp2:.8g}\n"
        f"🚀 <b>TP3</b>    {s.tp3:.8g}\n"
        f"⚖️ <b>1:{s.rr:.1f}</b> · плечо до <b>{s.leverage}×</b>\n\n"
        f"📡 Data <b>{health:.0f}</b> · Execution <b>{exe:.0f}</b> · $1k impact <b>{impact_text}</b>"
        f"{history}\n"
        f"🧬 Drift: <b>{escape(drift)}</b>\n"
        f"🧠 Alpha: <b>{alpha:+.1f}</b> <i>(raw {alpha_raw:+.1f})</i> · Fresh <b>{fresh:.0f}</b> · RelMom <b>{mom:.0f}p</b> · OFI5m <b>{ofi:+.0%}</b>\n\n"
        f"🕒 {escape(str(getattr(s,'expected_window','—')))} · пересмотр {escape(str(getattr(s,'review_window','—')))}\n"
        "⚠️ PRO — индекс качества условий, не вероятность прибыли."
    )


def _latest(symbol):
    with sqlite3.connect(DATABASE_PATH) as c:
        c.row_factory=sqlite3.Row
        row=c.execute("""
            SELECT * FROM signals
            WHERE symbol=? AND COALESCE(is_shadow,0)=0
            ORDER BY id DESC LIMIT 1
        """,(str(symbol).upper(),)).fetchone()
    return dict(row) if row else None


def _feature(row):
    try: return json.loads(row.get("feature_json") or "{}")
    except Exception: return {}


def why_text(symbol):
    row=_latest(symbol)
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
    alpha=f.get("alpha_v112") or f.get("alpha_v111",{}) or {}
    if alpha:
        lines += [
            "",
            "<b>Alpha V11.1</b>",
            f"Fresh <b>{float(alpha.get('fresh_score',0)):.0f}/100</b> · RelMom <b>{float(alpha.get('momentum_percentile',50)):.0f}p</b>",
            f"OFI 1m/5m <b>{float(alpha.get('ofi_1m',0)):+.0%}/{float(alpha.get('ofi_5m',0)):+.0%}</b>",
            f"BTC residual 6h <b>{float(alpha.get('residual_6h_pct',0)):+.2f}%</b> · beta <b>{float(alpha.get('beta',1)):.2f}</b>",
            f"Корректировка: <b>{float(alpha.get('weighted_adjustment',alpha.get('raw_adjustment',alpha.get('adjustment',0)))):+.1f}</b>",
            f"Факторы: <b>{escape(', '.join(map(str,alpha.get('notes') or [])) or 'нейтрально')}</b>",
        ]
    return "\n".join(lines)


def risk_text(symbol):
    row=_latest(symbol)
    if not row: return "⚪ Риск-данные сигнала ещё не сохранены."
    f=_feature(row); v=f.get("v11",{}) or {}; d=f.get("derivatives",{}) or {}
    issues=v.get("issues") or []
    return "\n".join([
        f"🛡 <b>РИСК · {escape(str(row['symbol']))}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"Data Health: <b>{float(v.get('data_health',0)):.0f}/100</b>",
        f"Execution: <b>{float(v.get('execution_quality',0)):.0f}/100</b>",
        f"$1k impact: <b>{float(v.get('impact_1k_bps',0)):.1f}bps</b>",
        f"$5k impact: <b>{float(v.get('impact_5k_bps',0)):.1f}bps</b>",
        f"Spread: <b>{float(d.get('spread_bps',0)):.2f}bps</b>",
        f"ADL: <b>{escape(str(d.get('adl_risk','—')).upper())}</b>",
        f"Флаги: <b>{escape(', '.join(map(str,issues)) if issues else 'нет критических')}</b>",
        "",
        "Бот оценивает рыночный риск, но не знает, какую позицию пользователь реально открыл.",
    ])


def stats_text(symbol):
    row=_latest(symbol)
    if not row: return "⚪ Статистика сетапа пока недоступна."
    f=_feature(row); v=f.get("v11",{}) or {}
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
        "",
        f"Drift: <b>{escape(str(drift.get('label','нет данных')))}</b>",
        f"Recent Exp: <b>{float(drift.get('recent_expectancy',0)):+.2f}R</b> · PF <b>{rpf:.2f}</b>",
        f"Baseline Exp: <b>{float(drift.get('baseline_expectancy',0)):+.2f}R</b> · PF <b>{bpf:.2f}</b>",
    ])


def life_text(symbol):
    data=lifecycle_status(symbol)
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


def system_extra():
    h=live_health(); ch=challenger_summary()
    ws=("ONLINE" if h["connected"] and (h["last_age_sec"] is None or h["last_age_sec"]<30) else "DEGRADED")
    return (
        f"📡 Live WS: <b>{ws}</b> · symbols <b>{len(h['symbols'])}</b> · reconnects <b>{h['reconnects']}</b>\n"
        f"🧪 Challenger audit: <b>{ch['audited']}</b> · closed <b>{ch['closed']}</b> · avg <b>{ch['avg_r']:+.2f}R</b>"
    )
