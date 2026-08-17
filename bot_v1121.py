"""Korkovts Signal AI V11.2 QUALITY LAB."""

from __future__ import annotations

import asyncio
from html import escape

from telegram.constants import ParseMode

import app.bot as core
import app.config as config
import app.db as db
import app.scanner as scanner

from v11_engine import attach, classify_regime, init_rank_audit, record_rank_audit
from v11_liquidity import annotate as annotate_liquidity
from v11_live import monitor as live_monitor, health as live_health
from v11_manager import init as init_lifecycle, observe as observe_lifecycle, structure_watch
from v11_ui import card, life_text, main_menu, risk_text, signal_actions, stats_text, system_extra, why_text
from v112_alpha import annotate as annotate_alpha
from v112_health import check as health_check, text as health_text
from v112_lab import lab_text, weighted_adjustment, weights as factor_weights

APP_VERSION="11.2.1"
STRATEGY_VERSION="11.2.1-audited"

config.APP_VERSION=APP_VERSION
config.STRATEGY_VERSION=STRATEGY_VERSION
core.APP_VERSION=APP_VERSION
core.STRATEGY_VERSION=STRATEGY_VERSION
db.APP_VERSION=APP_VERSION
db.STRATEGY_VERSION=STRATEGY_VERSION

_last_regime=None
_last_health=None
_last_neutral=False
_live_task=None

# The core scanner used to truncate qualified results before the Production
# layer could compare execution/Alpha/portfolio quality. Keep a wider internal
# pool, then V11.2.1 publishes only the final 3–4.
_original_limit_live_results=getattr(scanner,"_limit_live_results",None)
if _original_limit_live_results is not None:
    scanner._limit_live_results=lambda results,neutral_mode: results[:8]

_raw_scan=core.scan
_raw_short=core.scan_short
_raw_save=core.save
_raw_save_pending=core.save_pending
_original_callback=core.callback
_original_post_init=core.post_init
_original_post_shutdown=core.post_shutdown


def thresholds_v112(state):
    global _last_regime,_last_neutral
    regime=classify_regime(state)
    _last_regime=regime
    neutral=state.get("btc_bias_raw")=="NEUTRAL"
    _last_neutral=bool(neutral)
    adjustment=max(float(state.get("score_adjustment",0) or 0),regime.penalty)
    if neutral:
        adjustment=max(adjustment,float(config.NEUTRAL_REGIME_SCORE_PENALTY))
    main=min(94,float(config.MIN_SIGNAL_SCORE)+adjustment)
    short_base=min(94,float(config.MIN_SIGNAL_SCORE)+adjustment)
    return {
        "main":main,"short_base":short_base,"short":min(96,short_base+4),
        "neutral_mode":neutral,"regime_profile":regime.name,
    }


scanner.scan_thresholds=thresholds_v112
core.scan_thresholds=thresholds_v112


def combine_rank(base_rank,alpha_adjustment,base_eligible=True):
    """Alpha may demote/reject a valid signal; it can never rescue an invalid base signal."""
    if not base_eligible:
        return None
    final=max(0.0,min(99.0,float(base_rank)+float(alpha_adjustment)))
    return final if final>=75.0 else None


def _portfolio_select(rows,max_results=4):
    rows=sorted(
        rows,key=lambda s:(float(getattr(s,"professional_rank",0)),float(getattr(s,"score",0))),
        reverse=True
    )
    if not rows:
        return []
    selected=[rows[0]]
    used={int(getattr(rows[0],"cluster_id",0) or 0)}
    sides={str(getattr(rows[0],"side","")):1}
    for s in rows[1:]:
        cluster=int(getattr(s,"cluster_id",0) or 0)
        side=str(getattr(s,"side",""))
        if cluster and cluster in used: continue
        if sides.get(side,0)>=3: continue
        selected.append(s)
        if cluster: used.add(cluster)
        sides[side]=sides.get(side,0)+1
        if len(selected)>=max_results: return selected
    existing={id(x) for x in selected}
    for s in rows[1:]:
        if id(s) in existing: continue
        side=str(getattr(s,"side",""))
        if sides.get(side,0)>=3: continue
        selected.append(s); sides[side]=sides.get(side,0)+1
        if len(selected)>=max_results: break
    return selected


async def _prepare(results,kind):
    if not results:
        return []

    # Freeze the regime from the scan result itself. This avoids a race where a
    # concurrent /system request could update process-level status variables.
    source_context=dict(getattr(results[0],"market_context",{}) or {})
    neutral_context=source_context.get("btc_bias_raw")=="NEUTRAL"

    results=await annotate_liquidity(results)
    results=await annotate_alpha(results)

    prepared=[]
    for s in results:
        # Every candidate is evaluated against its own immutable scan context.
        signal_context=dict(getattr(s,"market_context",{}) or source_context)
        signal_regime=classify_regime(signal_context)
        s.production_regime=signal_regime.name
        # Production eligibility is frozen before adaptive Alpha.
        s=attach(s,signal_regime)
        base=float(getattr(s,"professional_rank",0))
        base_ok=bool(getattr(s,"professional_eligible",False))
        s.base_professional_rank=base
        if not base_ok:
            continue

        alpha=weighted_adjustment(s)
        final=combine_rank(base,alpha,True)
        if final is None:
            s.feature_snapshot.setdefault("alpha_v112",{})["rejected_after_alpha"]=True
            continue

        s.professional_rank=final
        s.feature_snapshot.setdefault("alpha_v112",{}).update({
            "base_professional_rank":base,
            "final_professional_rank":final,
        })
        prepared.append(s)

    final_limit=int(config.NEUTRAL_REGIME_MAX_SIGNALS) if neutral_context else 4
    chosen=_portfolio_select(prepared,final_limit)
    try:
        d=scanner._last_scan[kind]
        d["pre_v112_final"]=len(results)
        d["final"]=len(chosen)
        d["v112_filtered"]=len(results)-len(chosen)
        d["regime_profile"]=getattr(_last_regime,"name","UNKNOWN")
        d["factor_weights"]=factor_weights()
        d["v112_top"]=[
            (s.symbol,round(float(s.professional_rank),1),
             round(float(getattr(s,"alpha_adjustment",0)),1))
            for s in chosen
        ]
    except Exception:
        core.log.exception("V11.2 diagnostics update failed")
    return chosen


async def _health_gate(kind):
    global _last_health
    _last_health=await health_check()
    if _last_health.hard_pause:
        try:
            d=scanner._last_scan.get(kind,{})
            d["status"]="ok"; d["reason"]="PRODUCTION HEALTH PAUSE"; d["final"]=0
            d["health"]=_last_health.status
        except Exception:
            pass
        return False
    return True


async def scan_v112():
    if not await _health_gate("main"):
        return []
    return await _prepare(await _raw_scan(),"main")


async def short_v112():
    if not await _health_gate("short"):
        return []
    return await _prepare(await _raw_short(),"short")


core.scan=scan_v112
core.scan_short=short_v112


def display_signal(signal):
    # Critical V11.2 fix: do not call attach() again on a signal whose Alpha-
    # adjusted rank is already frozen. Auto delivery therefore shows the same
    # rank that was actually selected.
    if not hasattr(signal,"professional_rank"):
        signal=attach(signal,_last_regime)
    return signal


core.fmt=lambda s,priority=False: card(display_signal(s),priority)
core.menu=main_menu


async def analyze_symbol_v112(symbol):
    if not await _health_gate("main"):
        return None
    lower,hourly,higher,derivatives,state,news=await asyncio.gather(
        core.get_klines(symbol,"15m",300),
        core.get_klines(symbol,"1h",400),
        core.get_klines(symbol,"4h",400),
        core.get_derivatives_snapshot(symbol),
        core.market_state(),
        core.get_news_sentiment(),
    )
    oi_notional=float(derivatives.get("open_interest",0))*float(derivatives.get("mark_price",0))
    derivatives.update(core.liquidation_snapshot(symbol,oi_notional))
    analysis_state,_=core.market_analysis_state(state)
    threshold=thresholds_v112(state)["main"]
    result=core.analyze(
        symbol,"1H",hourly,higher,threshold,lower,
        analysis_state["bias"],derivatives,core.for_symbol(news,symbol),analysis_state
    )
    if not result: return None
    result=(await annotate_liquidity([result]))[0]
    result=(await annotate_alpha([result]))[0]
    result=attach(result,classify_regime(state))
    if not result.professional_eligible: return None
    result.base_professional_rank=float(result.professional_rank)
    alpha=weighted_adjustment(result)
    final=combine_rank(result.base_professional_rank,alpha,True)
    if final is None: return None
    result.professional_rank=final
    result.feature_snapshot.setdefault("alpha_v112",{}).update({
        "base_professional_rank":result.base_professional_rank,
        "final_professional_rank":final,
    })
    return result


core._analyze_symbol=analyze_symbol_v112


def _ready(signal):
    return signal if hasattr(signal,"professional_rank") else attach(signal,_last_regime)


def save_v112(signal,chat_id=None,shadow_reason=None):
    signal=_ready(signal)
    signal_id=_raw_save(signal,chat_id,shadow_reason)
    record_rank_audit(signal_id,signal)
    return signal_id


def save_pending_v112(signal):
    signal=_ready(signal)
    signal_id=_raw_save_pending(signal)
    record_rank_audit(signal_id,signal)
    return signal_id


core.save=save_v112
core.save_pending=save_pending_v112


async def start_v112(update,context):
    core.subscribe(update.effective_chat.id,True)
    text=(
        "⚡ <b>KORKOVTS SIGNAL AI · V11.2.1 AUDITED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏆 Frozen Production Champion\n"
        "🧠 Orthogonal Alpha: Fresh · RelMomentum · OFI · Squeeze · BTC residual\n"
        "🧬 Factor Lab: новые факторы обязаны доказать edge на forward-сделках\n"
        "📡 Health Gate: latency · свежесть BTC 1m · clock · WebSocket\n"
        "💧 Liquidity Impact · 🧬 Drift · 🧺 Portfolio Filter\n"
        "🔄 Live lifecycle активных сигналов\n"
        f"⏱ Автоскан каждые {core.AUTO_SCAN_INTERVAL_MIN} минут · только новые сигналы\n\n"
        "Новый фактор не получает усиленный вес, пока не накопит достаточную статистику.\n"
        "⚠️ Бот не исполняет сделки. PRO-рейтинг не является вероятностью прибыли."
    )
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=main_menu())


core.start=start_v112


async def send_results_v112(bot,chat_id,results,automatic=False,short=False,diagnostics=None):
    if not results:
        if automatic: return None
        if _last_health is not None and _last_health.hard_pause:
            return await bot.send_message(
                chat_id,
                health_text(_last_health),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
        details=""
        if diagnostics and diagnostics.get("status")=="ok":
            details=(
                f"\n\nВоронка: <b>{diagnostics.get('liquid',0)}</b> → "
                f"<b>{diagnostics.get('prefiltered',0)}</b> → "
                f"<b>{diagnostics.get('pre_v112_final',diagnostics.get('final',0))}</b> → "
                f"<b>{diagnostics.get('final',0)} V11.2.1</b>"
            )
            details+=core._decision_details(diagnostics)
        return await bot.send_message(
            chat_id,
            "⚪ <b>СИГНАЛОВ НЕТ</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Сейчас нет сделки, прошедшей Production + Alpha контроль."
            f"{details}",
            parse_mode=ParseMode.HTML,reply_markup=main_menu()
        )

    mode="⚡ SHORT" if short else ("🤖 AUTO" if automatic else "🏆 MAIN")
    top=results[0]
    await bot.send_message(
        chat_id,
        f"{mode} <b>· V11.2.1</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Сигналов: <b>{len(results)}</b>\n"
        f"№1 <b>{top.symbol}</b> · PRO <b>{top.professional_rank:.1f}/{top.professional_grade}</b>\n"
        f"Alpha <b>{float(getattr(top,'alpha_adjustment',0)):+.1f}</b> · "
        f"Regime <b>{escape(str(getattr(top,'production_regime',getattr(_last_regime,'name','UNKNOWN'))))}</b>\n"
        "№1 выбран после core, execution, drift, portfolio и factor-weight контроля.",
        parse_mode=ParseMode.HTML
    )
    for i,s in enumerate(results):
        await bot.send_message(
            chat_id,card(s,i==0),parse_mode=ParseMode.HTML,reply_markup=signal_actions(s.symbol)
        )


core._send_results=send_results_v112


async def callback_v112(update,context):
    query=update.callback_query
    data=str(query.data or "")
    if data=="v112:lab":
        await query.answer()
        return await query.message.reply_text(lab_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v112:health":
        await query.answer()
        h=await health_check(force=True)
        return await query.message.reply_text(health_text(h),parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data=="v11:menu":
        await query.answer()
        return await query.message.reply_text("🏠 <b>ГЛАВНОЕ МЕНЮ V11.2.1</b>",parse_mode=ParseMode.HTML,reply_markup=main_menu())
    if data.startswith("v11:"):
        await query.answer()
        try:
            _,action,symbol=data.split(":",2)
            func={"why":why_text,"risk":risk_text,"stats":stats_text,"life":life_text}.get(action)
            if func:
                return await query.message.reply_text(func(symbol),parse_mode=ParseMode.HTML,reply_markup=signal_actions(symbol))
        except Exception:
            core.log.exception("V11.2 detail callback failed")
            return await query.message.reply_text("⚠️ Не удалось открыть детали.",reply_markup=main_menu())
        return
    return await _original_callback(update,context)


core.callback=callback_v112


async def system_v112(update,context):
    global _last_health
    try:
        state,h=await asyncio.gather(core.market_state(),health_check())
        _last_health=h
        thresholds=thresholds_v112(state)
        breadth=state.get("breadth",{}) or {}
        scans=core.scan_status(); main=scans.get("main",{}); short=scans.get("short",{})
        w=factor_weights()
        text=(
            "🛡 <b>KORKOVTS V11.2.1 AUDITED</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"BTC <b>{escape(str(state.get('bias','?')))}</b> · ATR <b>{float(state.get('btc_atr_pct',0)):.2f}%</b>\n"
            f"Regime <b>{escape(str(thresholds.get('regime_profile','?')))}</b> · "
            f"Breadth <b>{float(breadth.get('up_ratio',.5))*100:.0f}%</b>\n"
            f"Health <b>{h.status}</b> · REST <b>{h.rest_latency_ms:.0f}ms</b> · BTC age <b>{h.candle_age_sec:.0f}s</b>\n"
            f"DB <b>{'PERSISTENT' if h.db_persistent else 'LOCAL'}</b> · path <code>{escape(h.db_path)}</code>\n"
            f"{system_extra()}\n\n"
            f"🏆 Main <b>{main.get('liquid',0)} → {main.get('prefiltered',0)} → {main.get('final',0)}</b>\n"
            f"⚡ Short <b>{short.get('liquid',0)} → {short.get('prefiltered',0)} → {short.get('final',0)}</b>\n\n"
            "🧬 Factor weights: "
            + " · ".join(f"{k} <b>{v:.2f}</b>" for k,v in w.items())
        )
    except Exception:
        core.log.exception("V11.2 system status failed")
        text="⚠️ <b>DATA DEGRADED</b>\nПолный системный статус недоступен."
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=main_menu())


core.system_status=system_v112


async def lifecycle_job(context):
    try: await observe_lifecycle(context.bot,notify=True)
    except Exception: core.log.exception("V11.2 lifecycle job failed")


async def structure_job(context):
    try: await structure_watch(context.bot,notify=True)
    except Exception: core.log.exception("V11.2 structure job failed")


async def post_init_v112(application):
    global _live_task
    init_rank_audit(); init_lifecycle()
    await _original_post_init(application)
    _live_task=asyncio.create_task(live_monitor(),name="v112-binance-live-market")
    application.job_queue.run_repeating(lifecycle_job,interval=30,first=45,name="v112-live-lifecycle")
    application.job_queue.run_repeating(structure_job,interval=300,first=180,name="v112-structure-watch")


core.post_init=post_init_v112


async def post_shutdown_v112(application):
    global _live_task
    if _live_task:
        _live_task.cancel()
        try: await _live_task
        except asyncio.CancelledError: pass
        _live_task=None
    await _original_post_shutdown(application)


core.post_shutdown=post_shutdown_v112


if __name__=="__main__":
    core.main()
