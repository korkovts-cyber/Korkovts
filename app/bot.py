import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from html import escape

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from .config import (
    APP_VERSION,
    AUTO_SCAN_INTERVAL_MIN,
    DATABASE_PATH,
    NEWS_POLL_INTERVAL_SEC,
    SIGNAL_COOLDOWN_HOURS,
    STRATEGY_VERSION,
    TELEGRAM_BOT_TOKEN,
    TOP_COINS,
)
from .db import (
    delivery_stats,
    enqueue_delivery,
    enqueue_news_alert,
    expire_pending_deliveries,
    forward_test_stats,
    init,
    mark_delivery_failed,
    mark_delivery_sent,
    mark_news_alert_failed,
    mark_news_alert_sent,
    news_alert_stats,
    pending_deliveries,
    pending_news_alerts,
    recent,
    register_breaking_news,
    research_stats,
    save,
    save_pending,
    signal_memory_stats,
    subscribe,
    subscribers,
    was_sent_recently,
)
from .liquidations import monitor as monitor_liquidations
from .liquidations import snapshot as liquidation_snapshot
from .liquidations import stream_status as liquidation_stream_status
from .market import (
    close_http_client,
    get_adl_risks,
    get_derivatives_snapshot,
    get_klines,
    get_prices,
    get_tickers,
    kline_cache_status,
)
from .news import for_symbol, get_news_sentiment
from .scanner import (
    market_analysis_state,
    market_state,
    scan,
    scan_short,
    scan_status,
    scan_thresholds,
)
from .strategy import analyze, fmt
from .tracker import update_outcomes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
_scan_lock = asyncio.Lock()
_delivery_lock = asyncio.Lock()
_news_delivery_lock = asyncio.Lock()
_news_watch_lock = asyncio.Lock()
_liquidation_task = None


def menu(analyze_symbol=None):
    rows = [
        [InlineKeyboardButton("⚡ ОСНОВНЫЕ СИГНАЛЫ", callback_data="scan")],
        [InlineKeyboardButton("⏱ КОРОТКИЕ СДЕЛКИ", callback_data="short_scan")],
    ]
    if analyze_symbol:
        coin = analyze_symbol.removesuffix("USDT")
        rows.append([InlineKeyboardButton(f"🔎 ПОВТОРИТЬ АНАЛИЗ {coin}", callback_data=f"analyze:{analyze_symbol}")])
    rows += [
        [InlineKeyboardButton("📰 ВАЖНЫЕ НОВОСТИ", callback_data="news")],
        [InlineKeyboardButton(f"{coin}/USDT", callback_data=f"price:{coin}USDT") for coin in TOP_COINS[:3]],
        [InlineKeyboardButton(f"{coin}/USDT", callback_data=f"price:{coin}USDT") for coin in TOP_COINS[3:6]],
        [InlineKeyboardButton("💹 КУРСЫ", callback_data="prices"), InlineKeyboardButton("🚀 ДВИЖЕНИЯ", callback_data="movers")],
        [InlineKeyboardButton("🔔 АВТО: ВКЛ", callback_data="alerts_on"), InlineKeyboardButton("🔕 АВТО: ВЫКЛ", callback_data="alerts_off")],
        [InlineKeyboardButton("🗂 ИСТОРИЯ", callback_data="status"), InlineKeyboardButton("🧠 ПАМЯТЬ 24Ч", callback_data="memory")],
        [InlineKeyboardButton("🛡 СОСТОЯНИЕ", callback_data="system"), InlineKeyboardButton("🧪 ЛАБОРАТОРИЯ", callback_data="lab")],
        [InlineKeyboardButton("🧹 ОЧИСТИТЬ ЧАТ", callback_data="clear_chat")],
    ]
    return InlineKeyboardMarkup(rows)


async def start(update, context):
    subscribe(update.effective_chat.id, True)
    text = (
        f"⚡ <b>KORKOVTS SIGNAL AI · V{APP_VERSION} RESEARCH</b>\n━━━━━━━━━━━━━━━━━━\n"
        "📡 Весь рынок Binance Futures USDT-M\n"
        "🧠 Многофакторный анализ 15m · 1H · 4H\n"
        "🧯 ADL-риск Binance · ширина рынка · кластеры корреляции\n"
        f"📰 Радар важных новостей каждые {NEWS_POLL_INTERVAL_SEC//60} мин и мгновенная перепроверка рынка\n"
        "🔎 При исходно нейтральном BTC бот ищет независимые сетапы монет с повышенным порогом\n"
        f"⏱ Автоскан каждые {AUTO_SCAN_INTERVAL_MIN} минут — уведомления только о новых сигналах\n\n"
        f"🧠 Повтор {SIGNAL_COOLDOWN_HOURS}ч блокируется по монете, направлению и таймфрейму\n"
        "Бот показывает <b>все найденные сильные сигналы</b>, а лучший ставит первым. "
        "При ручном скане бот сообщает, если условий нет; авто-режим в этом случае молчит.\n\n"
        "⚠️ Это исследовательский инструмент, а не гарантия прибыли. "
        "Не увеличивай ставку после убытка и не торгуй заёмными деньгами."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu())


async def _analyze_symbol(symbol):
    lower, hourly, higher, derivatives, state, news = await asyncio.gather(
        get_klines(symbol, "15m", 300), get_klines(symbol, "1h", 400),
        get_klines(symbol, "4h", 400), get_derivatives_snapshot(symbol),
        market_state(), get_news_sentiment(),
    )
    oi_notional=float(derivatives.get("open_interest",0))*float(derivatives.get("mark_price",0))
    derivatives.update(liquidation_snapshot(symbol,oi_notional))
    analysis_state,neutral_mode=market_analysis_state(state)
    if state.get("breadth_blocked") and not neutral_mode:
        return None
    threshold=scan_thresholds(state)["main"]
    return analyze(symbol,"1H",hourly,higher,threshold,lower,analysis_state["bias"],derivatives,
                   for_symbol(news,symbol),analysis_state)


async def signal(update, context):
    symbol = (context.args[0] if context.args else "BTCUSDT").upper().replace("/", "")
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    msg = update.effective_message
    await msg.reply_text(f"🧠 Анализирую <b>{symbol}</b>…", parse_mode=ParseMode.HTML)
    try:
        result = await _analyze_symbol(symbol)
        if result:
            await msg.reply_text(fmt(result, True), parse_mode=ParseMode.HTML, reply_markup=menu(symbol))
            if not was_sent_recently(result.symbol, result.side, SIGNAL_COOLDOWN_HOURS, result.timeframe):
                save(result, update.effective_chat.id)
        else:
            await msg.reply_text(
                f"⚪ <b>{symbol}</b>: сейчас нет достаточно сильного подтверждённого входа.\n"
                "Лучшее действие — пропустить слабую сделку.",
                parse_mode=ParseMode.HTML, reply_markup=menu(symbol))
    except Exception:
        log.exception("symbol analysis failed for %s", symbol)
        await msg.reply_text("⚠️ Не удалось получить все рыночные данные. Попробуй ещё раз через минуту.", reply_markup=menu())


async def _send_results(bot,chat_id,results,automatic=False,short=False,diagnostics=None):
    label = "КОРОТКИЕ СДЕЛКИ" if short else ("АВТОСКАН" if automatic else "СКАН РЫНКА")
    stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if not results:
        if automatic:
            return None
        details=""
        if diagnostics and diagnostics.get("status")=="ok":
            details=(
                f"\n\n🔎 Проверено: <b>{diagnostics.get('liquid',0)} ликвидных</b> → "
                f"<b>{diagnostics.get('prefiltered',0)} кандидатов</b> → <b>0 сигналов</b>.\n"
                f"Порог скана: <b>{float(diagnostics.get('threshold',0)):.0f}</b>."
            )
            if diagnostics.get("independent_mode"):
                details+=(
                    "\nBTC нейтрален, но скан <b>не блокировался</b>: "
                    "каждая монета была проверена как независимый сетап."
                )
            if int(diagnostics.get("prefiltered",0)):
                details+=(
                    f"\nПосле проверки OI, taker-flow, ADL, спреда и новостей "
                    f"отклонено: <b>{diagnostics.get('deep_rejected',0)}</b>."
                )
        return await bot.send_message(
            chat_id,
            f"📡 <b>{label} ЗАВЕРШЁН</b> · {stamp}\n━━━━━━━━━━━━━━━━━━\n"
            "⚪ Сильных подтверждённых сигналов сейчас нет.\n"
            "Бот продолжит наблюдение — отсутствие сделки тоже является результатом анализа."
            f"{details}",
            parse_mode=ParseMode.HTML, reply_markup=menu())
    clusters=len({result.cluster_id for result in results if result.cluster_id})
    await bot.send_message(
        chat_id,
        f"📡 <b>{label} ЗАВЕРШЁН</b> · {stamp}\n━━━━━━━━━━━━━━━━━━\n"
        f"Найдено сильных сигналов: <b>{len(results)}</b>\n"
        f"Независимых кластеров риска: <b>{clusters or len(results)}</b>\n"
        "Они отсортированы по силе; первый — приоритетный.\n"
        "⚠️ Не открывай весь список одновременно: криптоактивы часто являются одной коррелирующей позицией.",
        parse_mode=ParseMode.HTML)
    for index, result in enumerate(results):
        await bot.send_message(
            chat_id, fmt(result, index == 0), parse_mode=ParseMode.HTML,
            reply_markup=menu(result.symbol) if index == len(results) - 1 else None)


async def scan_cmd(update, context):
    msg = update.effective_message
    if _scan_lock.locked():
        return await msg.reply_text("⏳ Сканирование уже выполняется. Дождись результата.", reply_markup=menu())
    await msg.reply_text("🔎 Проверяю ликвидные бессрочные пары Binance Futures…")
    try:
        async with _scan_lock:
            results = await scan()
        await _send_results(
            context.bot,update.effective_chat.id,results,
            diagnostics=scan_status().get("main"))
        for result in results:
            if not was_sent_recently(result.symbol, result.side, SIGNAL_COOLDOWN_HOURS, result.timeframe):
                save(result, update.effective_chat.id)
    except Exception:
        log.exception("manual market scan failed")
        await msg.reply_text("⚠️ Скан временно не выполнен: один из источников рынка недоступен.", reply_markup=menu())


async def short_scan_cmd(update, context):
    msg = update.effective_message
    if _scan_lock.locked():
        return await msg.reply_text("⏳ Другой скан уже выполняется. Дождись результата.", reply_markup=menu())
    await msg.reply_text("⏱ Ищу краткосрочные входы: 5m → 15m → 1H…")
    try:
        async with _scan_lock:
            results = await scan_short()
        await _send_results(
            context.bot,update.effective_chat.id,results,short=True,
            diagnostics=scan_status().get("short"))
        for result in results:
            if not was_sent_recently(result.symbol, result.side, SIGNAL_COOLDOWN_HOURS, result.timeframe):
                save(result, update.effective_chat.id)
    except Exception:
        log.exception("short-term market scan failed")
        await msg.reply_text("⚠️ Краткосрочный скан временно не выполнен.", reply_markup=menu())


async def status(update, context):
    rows = recent(30)
    if not rows:
        text = "🗂 История сигналов пока пуста."
    else:
        lines = ["🗂 <b>ПОСЛЕДНИЕ СИГНАЛЫ</b>", "━━━━━━━━━━━━━━━━━━"]
        result_names={"TP2":"✅ TP2","SL":"🛑 STOP","ENTRY_EXPIRED":"⌛ НЕ АКТИВИРОВАН",
                      "INVALIDATED":"🚫 ОТМЕНЁН ДО ВХОДА","EXPIRED":"⏱ ЗАВЕРШЁН"}
        for created, symbol, timeframe, side, score, state, result, pnl_r, setup_type in rows:
            icon = "🟢" if side == "LONG" else "🔴"
            outcome=result_names.get(result,"🟡 ОЖИДАЕТ" if state=="SENT" else "🔵 АКТИВЕН")
            if pnl_r is not None and result not in ("ENTRY_EXPIRED","INVALIDATED",None):
                outcome+=f" · {pnl_r:+.2f}R"
            setup=f" · {setup_type}" if setup_type else ""
            lines.append(f"{icon} {created[:16]} · <b>{symbol}</b> · {timeframe} · {side} · {score:.0f}/100{setup}\n└ {outcome}")
        text = "\n".join(lines)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu())


async def memory(update, context):
    stats=signal_memory_stats()
    text=("🧠 <b>ПАМЯТЬ СИГНАЛОВ</b>\n━━━━━━━━━━━━━━━━━━\n"
          f"За последние 24 часа: <b>{stats['last_24h']}</b>\n"
          f"За предыдущие 24 часа: <b>{stats['previous_24h']}</b>\n"
          f"Всего сохранено: <b>{stats['total']}</b>\n\n"
          f"Теневых кандидатов за 24 часа: <b>{stats['shadow_24h']}</b> <i>(в Telegram не отправлялись)</i>\n\n"
          f"Одинаковый сигнал не отправляется повторно {SIGNAL_COOLDOWN_HOURS} часов. "
          "Для сохранения после деплоя Railway нужен Volume /data.")
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=menu())


def _news_age_text(age_minutes):
    if age_minutes is None:
        return "время не указано"
    age=float(age_minutes)
    if age<2: return "только что"
    if age<60: return f"{age:.0f} мин назад"
    if age<1440: return f"{age/60:.0f} ч назад"
    return f"{age/1440:.0f} дн назад"


def _news_item_text(item,index=None):
    direction={"POSITIVE":"🟢 позитивно",
               "NEGATIVE":"🔴 негативно",
               "NEUTRAL":"⚪ нейтрально"}.get(item.get("direction"),"⚪ нейтрально")
    impact="🚨" if item.get("high_impact") else "📌"
    title=escape(str(item.get("title","")[:180]))
    url=escape(str(item.get("url","")),quote=True)
    title=f'<a href="{url}">{title}</a>' if url else title
    assets=", ".join(item.get("assets",[])) or "весь рынок"
    prefix=f"{index}. " if index is not None else ""
    return (f"{prefix}{impact} <b>{escape(str(item.get('source','')))}</b> · "
            f"{_news_age_text(item.get('age_minutes'))}\n{title}\n"
            f"Оценка: <b>{direction}</b> · активы: <b>{escape(assets)}</b>")


async def news_status(update,context):
    msg=update.effective_message
    await msg.reply_text("📰 Обновляю новостной радар…")
    try:
        snapshot=await get_news_sentiment(force=True)
        sources=int(snapshot.get("sources",0)); total=int(snapshot.get("source_total",0))
        score=float(snapshot.get("global",0))
        tone="🟢 ПОЗИТИВНЫЙ" if score>=.20 else (
            "🔴 НЕГАТИВНЫЙ" if score<=-.20 else "⚪ СМЕШАННЫЙ / НЕЙТРАЛЬНЫЙ")
        x_status=("ПОДКЛЮЧЁН" if snapshot.get("x_connected") else
                  ("ОШИБКА ДОСТУПА" if snapshot.get("x_configured") else "НЕ НАСТРОЕН"))
        header="\n".join([
            "📰 <b>ВАЖНЫЕ НОВОСТИ КРИПТОРЫНКА</b>","━━━━━━━━━━━━━━━━━━",
            f"Источники: <b>{sources}/{total}</b>",
            f"Прямые посты X: <b>{x_status}</b>",
            f"Общий фон: <b>{tone}</b> ({score:+.2f})",
            f"Важных событий за 24ч: <b>{int(snapshot.get('high_impact_count',0))}</b>"])
        items=snapshot.get("items",[])[:6]
        if not items:
            body="⚠️ Свежие новости временно недоступны."
        else:
            body="\n\n".join(
                _news_item_text(item,index) for index,item in enumerate(items,1))
        footer=("🧠 Важная свежая новость запускает внеплановый скан. "
                "Сигнал появится только после подтверждения ценой, объёмом, OI и taker-flow.")
        await msg.reply_text(f"{header}\n\n{body}\n\n{footer}",parse_mode=ParseMode.HTML,
                             disable_web_page_preview=True,reply_markup=menu())
    except Exception:
        log.exception("news status failed")
        await msg.reply_text("⚠️ Не удалось обновить новости. Попробуй ещё раз через минуту.",
                             reply_markup=menu())


def _breaking_news_payload(event):
    return ("🚨 <b>ВАЖНАЯ НОВОСТЬ — РЫНОК ПЕРЕПРОВЕРЯЕТСЯ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"+_news_item_text(event)+
            "\n\n🔎 Бот запускает внеплановые сканы 15M и 1H. "
            "Торговый сигнал придёт отдельно, если рынок подтвердит новость.")


async def system_status(update, context):
    try:
        state_result,news_result,adl_result=await asyncio.gather(
            market_state(),get_news_sentiment(),get_adl_risks("BTCUSDT"),
            return_exceptions=True)
        failures=[]
        state=None if isinstance(state_result,Exception) else state_result
        news={} if isinstance(news_result,Exception) else news_result
        adl={} if isinstance(adl_result,Exception) else adl_result
        if isinstance(state_result,Exception): failures.append("рынок Binance")
        if isinstance(news_result,Exception) or int(news.get("sources",0))<1: failures.append("новости")
        if isinstance(adl_result,Exception) or not adl.get("BTCUSDT"): failures.append("ADL")

        stats=signal_memory_stats(); test=forward_test_stats(); deliveries=delivery_stats()
        news_deliveries=news_alert_stats()
        liq=liquidation_stream_status(); cache=kline_cache_status()
        btc_adl=adl.get("BTCUSDT",{})
        names={"LONG":"🟢 ВОСХОДЯЩИЙ","SHORT":"🔴 НИСХОДЯЩИЙ","NEUTRAL":"⚪ НЕЙТРАЛЬНЫЙ"}
        pf="∞" if test['profit_factor']>=999 else f"{test['profit_factor']:.2f}"
        lines=[f"🛡 <b>KORKOVTS V{APP_VERSION}</b>","━━━━━━━━━━━━━━━━━━",
               f"Когорта стратегии: <b>{STRATEGY_VERSION}</b>"]
        if state:
            breadth=state.get("breadth",{})
            thresholds=scan_thresholds(state)
            lines += [
                f"Режим BTC: <b>{names.get(state['bias'],state['bias'])}</b>",
                f"Состояние: <b>{state['label']}</b>",
                f"ATR BTC 1H: <b>{state['btc_atr_pct']:.2f}%</b>",
                f"Ширина рынка: <b>{float(breadth.get('up_ratio',.5))*100:.0f}% растут</b> · медиана <b>{float(breadth.get('median_change',0)):+.2f}%</b>",
                f"Порог 1H / 15M: <b>{thresholds['main']:.0f} / {thresholds['short']:.0f}</b>",
                f"Независимый поиск при neutral BTC: <b>{'ВКЛЮЧЁН' if thresholds['neutral_mode'] else 'НЕ ТРЕБУЕТСЯ'}</b>",
            ]
        else:
            lines.append("Режим BTC: <b>⚠️ ДАННЫЕ НЕДОСТУПНЫ</b>")
        lines += [
            f"ADL-риск BTC: <b>{str(btc_adl.get('risk','недоступен')).upper()}</b>",
            f"Поток ликвидаций: <b>{'ПРОГРЕТ / ПОДКЛЮЧЁН' if liq.get('warm') and liq.get('connected') else ('ПОДКЛЮЧЁН / ПРОГРЕВ' if liq.get('connected') else 'ОТКЛЮЧЁН')}</b>",
            f"Новостные источники: <b>{news.get('sources',0)}/{news.get('source_total',6)}</b>",
            f"Прямые посты X: <b>{'ПОДКЛЮЧЕНЫ' if news.get('x_connected') else ('ОШИБКА ДОСТУПА' if news.get('x_configured') else 'НЕ НАСТРОЕНЫ')}</b>",
            f"Событийный риск: <b>{'ПОВЫШЕННЫЙ' if float(news.get('event_risk',0))>=.67 else 'НОРМАЛЬНЫЙ'}</b> · важных заголовков: <b>{int(news.get('high_impact_count',0))}</b>",
            f"Новостной радар: <b>каждые {NEWS_POLL_INTERVAL_SEC//60} мин</b> · алертов 24ч: <b>{news_deliveries['delivered_24h']}</b>",
            f"Кэш закрытых свечей: <b>{cache['fresh']} актуальных наборов</b>",
            f"Автоскан: <b>каждые {AUTO_SCAN_INTERVAL_MIN} мин · 1H и 15M по очереди</b>",
            f"Очередь доставки: <b>{deliveries['pending']}</b> · ошибок за 7 дней: <b>{deliveries['failed_7d']}</b>",
            f"Очередь новостей: <b>{news_deliveries['pending']}</b>",
            f"Память 24ч: <b>{stats['last_24h']} доставленных сигналов</b>",
            f"Хранилище памяти: <b>{'VOLUME /data' if DATABASE_PATH.startswith('/data/') else 'ЛОКАЛЬНО / МОЖЕТ СБРОСИТЬСЯ'}</b>",
        ]
        status_names={"idle":"ЕЩЁ НЕ ЗАПУСКАЛСЯ","running":"ВЫПОЛНЯЕТСЯ","ok":"OK",
                      "blocked":"ЗАБЛОКИРОВАН РЕЖИМОМ","error":"ОШИБКА"}
        all_scans=scan_status()
        for key,label in (("main","1H"),("short","15M")):
            last=all_scans.get(key,{"status":"idle"})
            lines += ["",f"📊 Последний скан {label}: <b>{status_names.get(last.get('status'),last.get('status'))}</b>"]
            if last.get("reason"):
                lines.append(f"Причина: <b>{escape(str(last['reason']))}</b>")
            if last.get("status") not in (None,"idle"):
                scan_errors=int(last.get("technical_errors",0))+int(last.get("deep_errors",0))
                lines += [
                    f"Воронка: <b>{last.get('liquid',0)} ликвидных → {last.get('prefiltered',0)} кандидатов → {last.get('final',0)} сигналов</b>",
                    f"Отсев тех./финал: <b>{last.get('technical_rejected',0)}/{last.get('deep_rejected',0)}</b> · неполные деривативы: <b>{last.get('derivatives_incomplete',0)}</b>",
                    f"Ошибки отдельных монет: <b>{scan_errors}</b>",
                ]
        lines += ["",f"🧪 Закрыто в тесте v{APP_VERSION}: <b>{test['closed']}/100</b>",
                  f"Чистый результат: <b>{test['net_r']:+.2f}R</b>",
                  f"Profit factor: <b>{pf}</b>",
                  f"Макс. просадка: <b>{test['max_drawdown_r']:.2f}R</b>"]
        if failures:
            lines += ["",f"⚠️ Недоступные обязательные компоненты: <b>{', '.join(failures)}</b>.",
                      "Пока они не восстановятся, отсутствие сигнала нельзя считать результатом рынка."]
        lines += ["","До 100 закрытых активированных сигналов результат считается недостаточной выборкой.",
                  "При исходно нейтральном BTC рынок не блокируется: бот ищет независимые сетапы с повышенным порогом и выдаёт не более одного сигнала за скан. Экстремальное расхождение ширины рынка остаётся защитной блокировкой."]
        text="\n".join(lines)
    except Exception:
        log.exception("system status failed")
        text="⚠️ Не удалось сформировать системный отчёт. Проверь логи Railway перед использованием сигналов."
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=menu())


async def lab_status(update, context):
    data=research_stats(); test=forward_test_stats()
    shadow=data["shadow"]
    shadow_pf="∞" if shadow["profit_factor"]>=999 else f"{shadow['profit_factor']:.2f}"
    lines=[f"🧪 <b>ЛАБОРАТОРИЯ V{APP_VERSION} RESEARCH</b>","━━━━━━━━━━━━━━━━━━",
           f"Снимков факторов: <b>{data['feature_snapshots']}</b>",
           f"Закрыто активированных: <b>{test['closed']}/100</b>",
           f"Чистый результат: <b>{test['net_r']:+.2f}R</b>",
           f"Теневой контроль: <b>{shadow['closed']} закрыто · {shadow['pending']} в наблюдении</b>",
           f"Теневой net/PF: <b>{shadow['net_r']:+.2f}R · {shadow_pf}</b>","",
           "<b>Когорты:</b>"]
    if not data["cohorts"]:
        lines.append("Пока нет сигналов этой версии.")
    for row in data["cohorts"][:8]:
        pf="∞" if row["profit_factor"]>=999 else f"{row['profit_factor']:.2f}"
        lines.append(f"• {row['timeframe']} · {row['side']} · {row['setup']} — "
                     f"{row['closed']}/{row['issued']} · {row['net_r']:+.2f}R · PF {pf}")
    if data["shadow_cohorts"]:
        lines += ["","<b>Что отсеяли защитные фильтры:</b>"]
        for row in data["shadow_cohorts"][:6]:
            pf="∞" if row["profit_factor"]>=999 else f"{row['profit_factor']:.2f}"
            lines.append(f"• {row['reason']} — {row['closed']}/{row['issued']} · "
                         f"{row['net_r']:+.2f}R · PF {pf} · DD {row['max_drawdown_r']:.2f}R")
    lines += ["",(
        "Ликвидации пока записываются как телеметрия и не угадывают направление. "
        "Правило можно повысить до фильтра только после отдельной проверки на будущих данных.")]
    await update.effective_message.reply_text("\n".join(lines),parse_mode=ParseMode.HTML,reply_markup=menu())


def _price_text(symbol, row):
    arrow = "🟢" if row["change"] >= 0 else "🔴"
    return f'{arrow} <b>{symbol.removesuffix("USDT")}/USDT</b>  {row["price"]:.8g}  <b>{row["change"]:+.2f}%</b>'


async def prices(update, context):
    rows = await get_prices([f"{coin}USDT" for coin in TOP_COINS])
    text = "💹 <b>РЫНОК СЕЙЧАС</b>\n━━━━━━━━━━━━━━━━━━\n" + "\n".join(_price_text(s, r) for s, r in rows.items())
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu())


async def movers(update, context):
    rows = await get_tickers()
    liquid = [(s, r) for s, r in rows.items() if s.endswith("USDT") and r["quote_volume"] >= 15_000_000]
    up = sorted(liquid, key=lambda item: item[1]["change"], reverse=True)[:5]
    down = sorted(liquid, key=lambda item: item[1]["change"])[:5]
    text = "🚀 <b>ЛИДЕРЫ РОСТА 24Ч</b>\n" + "\n".join(_price_text(s, r) for s, r in up)
    text += "\n\n📉 <b>ЛИДЕРЫ ПАДЕНИЯ 24Ч</b>\n" + "\n".join(_price_text(s, r) for s, r in down)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu())


async def alerts_on(update, context):
    subscribe(update.effective_chat.id, True)
    await update.effective_message.reply_text(
        f"✅ <b>АВТОСКАН ВКЛЮЧЁН</b>\nПроверка каждые {AUTO_SCAN_INTERVAL_MIN} минут. "
        "Сообщение придёт только при появлении нового сильного сигнала или важной свежей новости.",
        parse_mode=ParseMode.HTML, reply_markup=menu())


async def alerts_off(update, context):
    subscribe(update.effective_chat.id, False)
    await update.effective_message.reply_text(
        "🔕 Автоматические сигналы и алерты важных новостей выключены.",
        reply_markup=menu())


async def clear_chat(update, context):
    chat_id, last_id, deleted = update.effective_chat.id, update.effective_message.message_id, 0
    for message_id in range(last_id, max(0, last_id - 100), -1):
        try:
            await context.bot.delete_message(chat_id, message_id)
            deleted += 1
        except Exception as exc:  # noqa: BLE001 - Telegram can raise several API errors here.
            log.debug("chat cleanup skipped message %s: %s",message_id,exc)
    await context.bot.send_message(chat_id, f"🧹 Очищено сообщений: {deleted}", reply_markup=menu())


async def _deliver_pending(bot):
    """Deliver the durable outbox with at-least-once semantics."""
    async with _delivery_lock:
        expire_pending_deliveries(SIGNAL_COOLDOWN_HOURS)
        delivered=0
        for delivery_id,signal_id,chat_id,payload,attempts,symbol in pending_deliveries(100):
            try:
                await bot.send_message(
                    chat_id,payload,parse_mode=ParseMode.HTML,reply_markup=menu(symbol))
            except Exception as exc:
                mark_delivery_failed(delivery_id,exc)
                log.exception(
                    "signal delivery failed: signal=%s chat=%s attempt=%s",
                    signal_id,chat_id,attempts+1)
            else:
                mark_delivery_sent(delivery_id)
                delivered+=1
        return delivered


async def _deliver_news_pending(bot):
    async with _news_delivery_lock:
        delivered=0
        for delivery_id,event_id,chat_id,payload,attempts in pending_news_alerts(100):
            try:
                await bot.send_message(chat_id,payload,parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True,reply_markup=menu())
            except Exception as exc:
                mark_news_alert_failed(delivery_id,exc)
                log.exception("news alert delivery failed: event=%s chat=%s attempt=%s",
                              event_id,chat_id,attempts+1)
            else:
                mark_news_alert_sent(delivery_id)
                delivered+=1
        return delivered


async def retry_signal_deliveries(context):
    """Retry signal and breaking-news Telegram deliveries independently."""
    try:
        signals=await _deliver_pending(context.bot)
        news=await _deliver_news_pending(context.bot)
        if signals or news:
            log.info("retried deliveries: signals=%s news=%s",signals,news)
    except Exception:
        log.exception("Telegram outbox retry failed")


async def _run_automatic_scan(context,scanner,label):
    try:
        chats=subscribers()
        if not chats:
            return
        if _scan_lock.locked():
            log.info("automatic %s scan skipped: another scan is running",label)
            return
        async with _scan_lock:
            all_results = await scanner()
        fresh = [r for r in all_results if not was_sent_recently(r.symbol, r.side, SIGNAL_COOLDOWN_HOURS, r.timeframe)]
        if not fresh:
            log.info("automatic %s scan completed: no new signals",label)
            return
        for index,result in enumerate(fresh):
            signal_id=save_pending(result)
            payload=fmt(result,index==0)
            for chat_id in chats:
                enqueue_delivery(signal_id,chat_id,payload)
        delivered=await _deliver_pending(context.bot)
        log.info("automatic %s signals queued=%s delivered=%s",
                 label,len(fresh)*len(chats),delivered)
    except Exception:
        log.exception("automatic %s scan failed",label)


async def _run_news_triggered_scans(context):
    for _ in range(18):
        if not _scan_lock.locked():
            break
        await asyncio.sleep(5)
    if _scan_lock.locked():
        log.warning("breaking-news scan skipped after waiting 90 seconds")
        return
    await _run_automatic_scan(context,scan_short,"новостной 15M")
    await _run_automatic_scan(context,scan,"новостной 1H")


async def watch_breaking_news(context):
    if _news_watch_lock.locked():
        return
    async with _news_watch_lock:
        try:
            snapshot=await get_news_sentiment(force=True)
            if int(snapshot.get("sources",0))<1:
                log.warning("breaking-news radar has no available sources")
                return
            events=register_breaking_news(snapshot.get("breaking_events",[]))
            chats=subscribers()
            for event in events:
                payload=_breaking_news_payload(event)
                for chat_id in chats:
                    enqueue_news_alert(event["id"],chat_id,payload)
            delivered=await _deliver_news_pending(context.bot)
            if events:
                log.info("breaking news: new=%s alerts_delivered=%s",len(events),delivered)
            if events and chats:
                await _run_news_triggered_scans(context)
        except Exception:
            log.exception("breaking-news radar failed")


async def auto_scan(context):
    await _run_automatic_scan(context,scan,"1H")


async def auto_short_scan(context):
    await _run_automatic_scan(context,scan_short,"15M")


async def track_signal_outcomes(context):
    """Silent background quality journal; never sends lifecycle notifications."""
    try:
        events=await update_outcomes()
    except Exception:
        log.exception("signal outcome tracking failed")
        return
    if events:
        log.info("quality journal updated: %s lifecycle events",len(events))


async def callback(update, context):
    query = update.callback_query
    await query.answer()
    actions = {"prices": prices, "movers": movers, "scan": scan_cmd, "short_scan": short_scan_cmd, "alerts_on": alerts_on,
               "alerts_off": alerts_off, "clear_chat": clear_chat, "status": status,
               "memory": memory, "system": system_status, "lab": lab_status,"news":news_status}
    if query.data in actions:
        return await actions[query.data](update, context)
    if query.data.startswith("analyze:"):
        context.args = [query.data.split(":", 1)[1]]
        return await signal(update, context)
    if query.data.startswith("price:"):
        symbol = query.data.split(":", 1)[1]
        row = (await get_prices([symbol])).get(symbol)
        return await query.message.reply_text(
            _price_text(symbol, row) if row else "Цена временно недоступна.",
            parse_mode=ParseMode.HTML, reply_markup=menu(symbol))


async def post_init(application):
    global _liquidation_task
    identity=await application.bot.get_me()
    log.info("Korkovts Signal AI V%s started via app.bot as @%s",
             APP_VERSION,identity.username or identity.id)
    await application.bot.set_my_commands([
        BotCommand("start", "главное меню"), BotCommand("scan", "сканировать весь рынок"),
        BotCommand("short", "краткосрочные сделки"),
        BotCommand("news", "важные новости крипторынка"),
        BotCommand("signal", "анализ монеты: /signal BTC"), BotCommand("prices", "цены топ-монет"),
        BotCommand("movers", "лидеры роста и падения"), BotCommand("status", "история сигналов"),
        BotCommand("memory", "память сигналов за 24 часа"),
        BotCommand("system", "состояние фильтров и рынка"),
        BotCommand("lab", "статистика исследовательской версии"),
        BotCommand("alerts_on", "включить автоскан"), BotCommand("alerts_off", "выключить автоскан"),
    ])
    _liquidation_task=asyncio.create_task(monitor_liquidations(),name="binance-liquidation-telemetry")


async def post_shutdown(application):
    global _liquidation_task
    if _liquidation_task:
        _liquidation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _liquidation_task
        _liquidation_task=None
    await close_http_client()


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Add TELEGRAM_BOT_TOKEN to .env")
    init()
    application = (Application.builder().token(TELEGRAM_BOT_TOKEN)
                   .post_init(post_init).post_shutdown(post_shutdown).build())
    for command, handler in [
        ("start", start), ("help", start), ("signal", signal), ("scan", scan_cmd), ("short", short_scan_cmd),
        ("status", status), ("memory", memory), ("system", system_status), ("lab", lab_status),
        ("news",news_status),
        ("prices", prices), ("movers", movers),
        ("alerts_on", alerts_on), ("alerts_off", alerts_off),
    ]:
        application.add_handler(CommandHandler(command, handler))
    application.add_handler(CallbackQueryHandler(callback))
    application.job_queue.run_repeating(retry_signal_deliveries, interval=60, first=15,
                                        name="signal-delivery-outbox")
    application.job_queue.run_repeating(
        watch_breaking_news,interval=NEWS_POLL_INTERVAL_SEC,first=20,
        name="breaking-news-radar")
    application.job_queue.run_repeating(auto_scan, interval=AUTO_SCAN_INTERVAL_MIN * 60, first=30, name="market-auto-scan")
    application.job_queue.run_repeating(
        auto_short_scan,interval=AUTO_SCAN_INTERVAL_MIN*60,
        first=max(60,AUTO_SCAN_INTERVAL_MIN*30+30),name="short-market-auto-scan")
    application.job_queue.run_repeating(track_signal_outcomes, interval=120, first=90, name="signal-quality-journal")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
