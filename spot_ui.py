"""Telegram rendering for the independent Spot universe."""
from __future__ import annotations

from html import escape
from datetime import datetime

from spot_db import stats as db_stats,recent as db_recent,active_open_count,active_signals
from spot_scanner import status as scan_status
from spot_market import request_status
from spot_watch import count_active as watch_count
from spot_orderbook import health as local_book_health
from v1170_calibration import spot as spot_probability, short_text as probability_text


def card(s,priority=False):
    """Premium Spot card matching the Futures visual language."""
    buy=s.status=="BUY"
    entry_state=str(getattr(s,"spot_entry_state",("BUY_NOW" if buy else "WATCH")) or "WATCH").upper()
    streak=int(getattr(s,"spot_confirm_streak",0) or 0)
    if entry_state=="BUY_NOW":
        state_icon="🚨"; state_label="BUY NOW · CONFIRMED 2/2"
    elif entry_state=="READY_PENDING":
        state_icon="🟠"; state_label=f"READY · {max(1,streak)}/2"
    elif entry_state=="COOLDOWN":
        state_icon="🧊"; state_label="COOLDOWN"
    else:
        state_icon="🟡"; state_label="WATCH"
    title=("🟢 <b>YK SPOT PRIME · №1</b>" if priority else "▫️ <b>YK SPOT · АЛЬТЕРНАТИВА</b>")
    micro=s.micro or {}; news=s.news or {}; crowd=s.derivatives_risk or {}
    prob=spot_probability(s)
    snap=(getattr(s,"feature_snapshot",{}) or {})
    evidence=(snap.get("evidence_v117") or {})
    local_book=(snap.get("local_orderbook_v118") or {})
    if entry_state=="BUY_NOW":
        action="🚨 <b>ВХОД РАЗРЕШЁН СЕЙЧАС</b>\n<b>Действие:</b> покупка только внутри BUY ZONE"
    elif entry_state=="READY_PENDING":
        action="<b>ПОКА НЕ ПОКУПАТЬ</b> · ждать подтверждение 2/2"
    elif entry_state=="COOLDOWN":
        action="<b>ПОКА НЕ ПОКУПАТЬ</b> · новый вход временно заблокирован"
    else:
        action="<b>ПОКА НЕ ПОКУПАТЬ</b> · наблюдать за сетапом"
    plan=(f"BUY     {float(s.entry_low):.8g} — {float(s.entry_high):.8g}\n"
          f"STOP    {float(s.invalidation):.8g}\n"
          f"TP1     {float(s.tp1):.8g}\n"
          f"TP2     {float(s.tp2):.8g}\n"
          f"TP3     {float(s.tp3):.8g}")
    lines=[
        title,"━━━━━━━━━━━━━━━━━━━━",
        f"<b>{escape(str(s.symbol))}</b>  ·  🟢 SPOT  ·  <b>{escape(str(s.horizon))}</b>",
        f"💎 QUALITY <b>{float(s.score):.0f}/100</b>  ·  {escape(str(s.setup_type))}",
        f"🏎 Strength <b>{float(s.relative_percentile):.0f}p</b> · vs BTC 14D <b>{float(s.excess_btc_14d):+.1f}%</b>",
        f"📈 Фактическая forward-оценка <b>{escape(probability_text(prob))}</b>",
        "",
        "⚡ <b>СТАТУС</b>",
        f"<blockquote>{state_icon} <b>{state_label}</b>\n{action}</blockquote>",
        "",
        "🎯 <b>ТОРГОВЫЙ ПЛАН</b>",
        f"<pre>{plan}</pre>",
        "",
        "🧠 <b>MARKET INTELLIGENCE</b>",
        f"🌐 Regime <b>{escape(str(s.market_regime))}</b> · breadth <b>{float(s.market_breadth)*100:.0f}%</b>",
        f"🧪 Evidence <b>{int(evidence.get('support',0))}/{int(evidence.get('conflict',0))}</b> support/conflict",
        f"💧 Spread <b>{float(micro.get('spread_bps',999)):.1f}bps</b> · $5k impact <b>{float(micro.get('impact_5k_bps',999)):.1f}bps</b>",
        f"🌊 L2 imbalance <b>{float(micro.get('book_imbalance_20bps',0)):+.2f}</b> · taker BUY <b>{float(micro.get('buy_share',.5))*100:.0f}%</b>",
        f"🕐 Flow 5m/15m <b>{float(micro.get('closed_buy_share_5m',.5))*100:.0f}%/{float(micro.get('closed_buy_share_15m',.5))*100:.0f}%</b>",
    ]
    if local_book:
        lines.append(f"📖 Local book <b>{float(local_book.get('stability_score',0) or 0):.0f}/100</b> · replenish <b>{float(local_book.get('bid_replenishment_ratio',0) or 0):.2f}×</b>")
    if crowd.get("available"):
        lines.append(f"🧯 Crowding <b>{'EXTREME' if crowd.get('extreme') else 'OK'}</b> · funding <b>{float(crowd.get('funding',0))*100:+.3f}%</b> · OI <b>{float(crowd.get('oi_change_pct',0)):+.1f}%</b>")
    elif crowd.get("degraded"):
        lines.append("🧯 Crowding <b>DEGRADED · BUY BLOCKED</b>")
    else:
        lines.append("🧯 Crowding <b>N/A</b>")
    if news.get("degraded"):
        lines.append("📰 News <b>DEGRADED · BUY BLOCKED</b>")
    elif news.get("block"):
        lines.append("📰 News <b>NEGATIVE RISK · BUY BLOCKED</b>")
    elif news.get("global_breaking"):
        lines.append("📰 News <b>HIGH-IMPACT EVENT · WAIT</b>")
    elif news.get("catalyst"):
        lines.append("📰 News <b>POSITIVE CATALYST · MULTI-SOURCE</b>")
    else:
        lines.append("📰 News <b>neutral</b>")
    if s.reasons:
        lines += ["","✨ <b>ПОЧЕМУ В СПИСКЕ</b>"]+[f"  • {escape(str(x))}" for x in s.reasons[:2]]
    if s.risks:
        lines += ["","⚠️ <b>РИСКИ</b>"]+[f"  • {escape(str(x))}" for x in s.risks[:3]]
    lines += ["","<i>YK rule: BUY NOW → только зона. STOP/invalidation → полный выход. Не увеличивать риск после входа.</i>","<i>Quality/Readiness — индексы условий, не вероятность прибыли.</i>"]
    return "\n".join(lines)

def history_text():
    rows=db_recent(12)
    if not rows:
        return "🟢 <b>SPOT HISTORY</b>\n━━━━━━━━━━━━━━━━━━\nПока нет сохранённых BUY-сигналов."
    lines=["🟢 <b>SPOT HISTORY · 3–10 DAYS</b>","━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        r7=r.get("return_7d"); r10=r.get("return_10d")
        perf=(f"7D {float(r7):+.1f}%" if r7 is not None else "7D …")
        if r10 is not None: perf+=f" · 10D {float(r10):+.1f}%"
        result=str(r.get("result") or "OPEN")
        result_label={
            "AMBIGUOUS_INVALIDATION_TP":"❔ AMBIGUOUS TP/INVALIDATION",
            "INVALIDATED":"🛑 INVALIDATED","TP3":"✅ TP3",
            "POSITIVE_10D":"✅ POSITIVE 10D","NEGATIVE_10D":"🔴 NEGATIVE 10D",
        }.get(result,result)
        lines.append(
            f"• <b>{escape(r['symbol'])}</b> · {escape(r['state'])} · {escape(result_label)} · "
            f"Q {float(r['score']):.0f} · {perf} · "
            f"MFE {float(r.get('max_favorable_pct') or 0):+.1f}% / MAE {float(r.get('max_adverse_pct') or 0):+.1f}%"
        )
    return "\n".join(lines)


def stats_text():
    s=db_stats(); n7=int(s.get("n7") or 0)
    wr=("—" if s.get("win_rate_7d") is None else f"{float(s['win_rate_7d'])*100:.0f}%")
    return (
        "📊 <b>SPOT FORWARD STATS</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"BUY issued <b>{int(s.get('issued') or 0)}</b> · resolved <b>{int(s.get('resolved') or 0)}</b>\n"
        f"7D evaluated <b>{n7}</b> · positive <b>{wr}</b> · avg <b>{float(s.get('avg_7d') or 0):+.2f}%</b>\n"
        f"10D avg <b>{float(s.get('avg_10d') or 0):+.2f}%</b>\n"
        f"Avg MFE <b>{float(s.get('avg_mfe') or 0):+.2f}%</b> · Avg MAE <b>{float(s.get('avg_mae') or 0):+.2f}%</b>\n"
        f"Invalidated <b>{int(s.get('invalidated') or 0)}</b> · ambiguous <b>{int(s.get('ambiguous') or 0)}</b>\n\n"
        "Адаптивные Spot-бонусы не включаются на маленькой выборке: сначала накапливаем forward-историю."
    )


def active_text():
    rows=active_signals(10)
    if not rows:
        return "📍 <b>ACTIVE SPOT</b>\n━━━━━━━━━━━━━━━━━━\nСейчас нет активных доставленных Spot BUY NOW."
    lines=[
        "📍 <b>ACTIVE SPOT · 3–10 DAYS</b>","━━━━━━━━━━━━━━━━━━",
        "Исходные уровни не переписываются задним числом.",
    ]
    for r in rows:
        lines += [
            "",
            f"🟢 <b>#{r['id']} · {escape(str(r['symbol']))}</b> · Q {float(r['score']):.0f}",
            f"Entry <b>{float(r['entry_price']):.8g}</b> · invalidation <b>{float(r['invalidation']):.8g}</b>",
            f"TP1 <b>{float(r['tp1']):.8g}</b> · TP2 <b>{float(r['tp2']):.8g}</b> · TP3 <b>{float(r['tp3']):.8g}</b>",
            f"MFE <b>{float(r.get('max_favorable_pct') or 0):+.1f}%</b> · MAE <b>{float(r.get('max_adverse_pct') or 0):+.1f}%</b>",
            f"TP1/2/3 <b>{int(r.get('tp1_hit') or 0)}/{int(r.get('tp2_hit') or 0)}/{int(r.get('tp3_hit') or 0)}</b>",
        ]
    lines += ["","🛑 Invalidation = заранее определённый полный выход; бот не расширяет риск после входа."]
    return "\n".join(lines)


def system_text():
    d=scan_status(); r=request_status(); ob=local_book_health()
    return (
        "🟢 <b>SPOT UNIVERSE · V11.8.1</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Scan <b>{escape(str(d.get('status','?')))}</b> · regime <b>{escape(str(d.get('regime','?')))}</b>\n"
        f"Liquid <b>{int(d.get('liquid',0))}</b> → daily <b>{int(d.get('daily_ok',0))}</b> → "
        f"pre <b>{int(d.get('prefiltered',0))}</b> → deep <b>{int(d.get('deep_checked',0))}</b>\n"
        f"BUY <b>{int(d.get('buy',0))}</b> · WATCH <b>{int(d.get('watch',0))}</b> · "
        f"Watchtower <b>{watch_count()}</b> · Active Spot <b>{active_open_count()}/2</b>\n"
        f"Breadth <b>{float(d.get('breadth',0))*100:.0f}%</b> · dispersion7D <b>{float(d.get('dispersion_7d',0)):.1f}%</b>\n"
        f"Spot REST cache <b>{int(r.get('cache_entries',0))}</b> · cooldown <b>{float(r.get('cooldown_seconds',0)):.0f}s</b>\n"
        f"Local book WS <b>{'OK' if ob.get('connected') else 'OFF'}</b> · synced <b>{int(ob.get('healthy',0))}/{int(ob.get('total',0))}</b> · "
        f"gaps <b>{int(ob.get('gaps',0))}</b> · reconnects <b>{int(ob.get('reconnects',0))}</b>\n\n"
        "Source of truth: Binance <b>Spot</b> candles + live sequence-synchronised Spot order book + Spot aggTrades. "
        "Futures data используется только как crowding-risk overlay."
    )
