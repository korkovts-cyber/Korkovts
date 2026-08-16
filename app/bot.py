import asyncio
import contextlib
import logging
from datetime import datetime, timezone

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from .config import (APP_VERSION, AUTO_SCAN_INTERVAL_MIN, MIN_SIGNAL_SCORE,
                     SIGNAL_COOLDOWN_HOURS, STRATEGY_VERSION, TELEGRAM_BOT_TOKEN, TOP_COINS)
from .db import (forward_test_stats, init, recent, research_stats, save,
                 signal_memory_stats, subscribe, subscribers, was_sent_recently)
from .market import (get_adl_risks, get_derivatives_snapshot, get_klines,
                     get_prices, get_tickers, kline_cache_status, close_http_client)
from .scanner import market_state, scan, scan_short
from .strategy import analyze, fmt
from .tracker import update_outcomes
from .liquidations import (monitor as monitor_liquidations,
                           snapshot as liquidation_snapshot,
                           stream_status as liquidation_stream_status)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
_scan_lock = asyncio.Lock()
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
        "⚡ <b>KORKOVTS SIGNAL AI · V10 RESEARCH</b>\n━━━━━━━━━━━━━━━━━━\n"
        "📡 Весь рынок Binance Futures USDT-M\n"
        "🧠 Многофакторный анализ 15m · 1H · 4H\n"
        "🧯 ADL-риск Binance · ширина рынка · кластеры корреляции\n"
        f"⏱ Автоскан каждые {AUTO_SCAN_INTERVAL_MIN} минут — уведомления только о новых сигналах\n\n"
        f"🧠 Повтор {SIGNAL_COOLDOWN_HOURS}ч блокируется по монете, направлению и таймфрейму\n"
        "Бот показывает <b>все найденные сильные сигналы</b>, а лучший ставит первым. "
        "Если условий для входа нет — бот честно сообщает об этом.\n\n"
        "⚠️ Это исследовательский инструмент, а не гарантия прибыли. "
        "Не увеличивай ставку после убытка и не торгуй заёмными деньгами."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu())


async def _analyze_symbol(symbol):
    from .news import for_symbol, get_news_sentiment
    lower, hourly, higher, derivatives, state, news = await asyncio.gather(
        get_klines(symbol, "15m", 300), get_klines(symbol, "1h", 400),
        get_klines(symbol, "4h", 400), get_derivatives_snapshot(symbol),
        market_state(), get_news_sentiment(),
    )
    oi_notional=float(derivatives.get("open_interest",0))*float(derivatives.get("mark_price",0))
    derivatives.update(liquidation_snapshot(symbol,oi_notional))
    threshold = min(94, MIN_SIGNAL_SCORE + state["score_adjustment"])
    return analyze(symbol, "1H", hourly, higher, threshold, lower, state["bias"], derivatives,
                   for_symbol(news, symbol),state)


async def signal(update, context):
    symbol = (context.args[0] if context.args else "BTCUSDT").upper().replace("/", "")
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    msg = update.effective_message
    await msg.reply_text(f"🧠 Анализирую <b>{symbol}</b>…", parse_mode=ParseMode.HTML)
    try:
        result = await _analyze_symbol(symbol)
        if result:
            if not was_sent_recently(result.symbol, result.side, SIGNAL_COOLDOWN_HOURS, result.timeframe):
                save(result, update.effective_chat.id)
            await msg.reply_text(fmt(result, True), parse_mode=ParseMode.HTML, reply_markup=menu(symbol))
        else:
            await msg.reply_text(
                f"⚪ <b>{symbol}</b>: сейчас нет достаточно сильного подтверждённого входа.\n"
                "Лучшее действие — пропустить слабую сделку.",
                parse_mode=ParseMode.HTML, reply_markup=menu(symbol))
    except Exception:
        log.exception("symbol analysis failed for %s", symbol)
        await msg.reply_text("⚠️ Не удалось получить все рыночные данные. Попробуй ещё раз через минуту.", reply_markup=menu())


async def _send_results(bot, chat_id, results, automatic=False, short=False):
    label = "КОРОТКИЕ СДЕЛКИ" if short else ("АВТОСКАН" if automatic else "СКАН РЫНКА")
    stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if not results:
        return await bot.send_message(
            chat_id,
            f"📡 <b>{label} ЗАВЕРШЁН</b> · {stamp}\n━━━━━━━━━━━━━━━━━━\n"
            "⚪ Сильных подтверждённых сигналов сейчас нет.\n"
            "Бот продолжит наблюдение — отсутствие сделки тоже является результатом анализа.",
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
        for result in results:
            if not was_sent_recently(result.symbol, result.side, SIGNAL_COOLDOWN_HOURS, result.timeframe):
                save(result, update.effective_chat.id)
        await _send_results(context.bot, update.effective_chat.id, results)
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
        for result in results:
            if not was_sent_recently(result.symbol, result.side, SIGNAL_COOLDOWN_HOURS, result.timeframe):
                save(result, update.effective_chat.id)
        await _send_results(context.bot, update.effective_chat.id, results, short=True)
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


async def system_status(update, context):
    try:
        from .news import get_news_sentiment
        state,news,adl=await asyncio.gather(market_state(),get_news_sentiment(),get_adl_risks("BTCUSDT"))
        stats=signal_memory_stats(); test=forward_test_stats()
        liq=liquidation_stream_status(); breadth=state.get("breadth",{})
        cache=kline_cache_status()
        btc_adl=adl.get("BTCUSDT",{})
        names={"LONG":"🟢 ВОСХОДЯЩИЙ","SHORT":"🔴 НИСХОДЯЩИЙ","NEUTRAL":"⚪ НЕЙТРАЛЬНЫЙ"}
        threshold=min(94,MIN_SIGNAL_SCORE+state["score_adjustment"])
        pf="∞" if test['profit_factor']>=999 else f"{test['profit_factor']:.2f}"
        text=(f"🛡 <b>KORKOVTS V{APP_VERSION}</b>\n━━━━━━━━━━━━━━━━━━\n"
              f"Когорта стратегии: <b>{STRATEGY_VERSION}</b>\n"
              f"Режим BTC: <b>{names.get(state['bias'],state['bias'])}</b>\n"
              f"Состояние: <b>{state['label']}</b>\n"
              f"ATR BTC 1H: <b>{state['btc_atr_pct']:.2f}%</b>\n"
              f"Ширина рынка: <b>{float(breadth.get('up_ratio',.5))*100:.0f}% растут</b> · медиана <b>{float(breadth.get('median_change',0)):+.2f}%</b>\n"
              f"Текущий порог: <b>{threshold:.0f}/100</b>\n"
              f"ADL-риск BTC: <b>{str(btc_adl.get('risk','unknown')).upper()}</b>\n"
              f"Поток ликвидаций: <b>{'ПРОГРЕТ' if liq.get('warm') and liq.get('connected') else 'ПРОГРЕВ / НЕТ ДАННЫХ'}</b>\n"
              f"Новостные источники: <b>{news.get('sources',0)}/3</b>\n"
              f"Событийный риск: <b>{'ПОВЫШЕННЫЙ' if float(news.get('event_risk',0))>=.67 else 'НОРМАЛЬНЫЙ'}</b>"
              f" · важных заголовков: <b>{int(news.get('high_impact_count',0))}</b>\n"
              f"Кэш закрытых свечей: <b>{cache['fresh']} актуальных наборов</b>\n"
              f"Автоскан: <b>каждые {AUTO_SCAN_INTERVAL_MIN} мин</b>\n"
              f"Память 24ч: <b>{stats['last_24h']} сигналов</b>\n\n"
              f"🧪 Закрыто в тесте v{APP_VERSION}: <b>{test['closed']}/100</b>\n"
              f"Чистый результат: <b>{test['net_r']:+.2f}R</b>\n"
              f"Profit factor: <b>{pf}</b>\n"
              f"Макс. просадка: <b>{test['max_drawdown_r']:.2f}R</b>\n\n"
              "До 100 закрытых активированных сигналов результат считается недостаточной выборкой.\n"
              "При нейтральном режиме новые сделки блокируются — бот ждёт устойчивого направления.")
    except Exception:
        log.exception("system status failed")
        text="⚠️ Не удалось подтвердить состояние Binance. Новые сигналы лучше не использовать до восстановления данных."
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=menu())


async def lab_status(update, context):
    data=research_stats(); test=forward_test_stats()
    shadow=data["shadow"]
    shadow_pf="∞" if shadow["profit_factor"]>=999 else f"{shadow['profit_factor']:.2f}"
    lines=["🧪 <b>ЛАБОРАТОРИЯ V10 RESEARCH</b>","━━━━━━━━━━━━━━━━━━",
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
    lines += ["","Ликвидации пока записываются как телеметрия и не угадывают направление. "
              "Правило можно повысить до фильтра только после отдельной проверки на будущих данных."]
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
        "Сообщение придёт только при появлении нового сильного сигнала.",
        parse_mode=ParseMode.HTML, reply_markup=menu())


async def alerts_off(update, context):
    subscribe(update.effective_chat.id, False)
    await update.effective_message.reply_text("🔕 Автоматические отчёты выключены.", reply_markup=menu())


async def clear_chat(update, context):
    chat_id, last_id, deleted = update.effective_chat.id, update.effective_message.message_id, 0
    for message_id in range(last_id, max(0, last_id - 100), -1):
        try:
            await context.bot.delete_message(chat_id, message_id)
            deleted += 1
        except Exception:
            pass
    await context.bot.send_message(chat_id, f"🧹 Очищено сообщений: {deleted}", reply_markup=menu())


async def auto_scan(context):
    chats = subscribers()
    if not chats:
        return
    if _scan_lock.locked():
        log.info("automatic scan skipped: another scan is running")
        return
    try:
        async with _scan_lock:
            all_results = await scan()
        fresh = [r for r in all_results if not was_sent_recently(r.symbol, r.side, SIGNAL_COOLDOWN_HOURS, r.timeframe)]
        if not fresh:
            log.info("automatic scan completed: no new signals")
            return
        for result in fresh:
            save(result)
        for chat_id in chats:
            try:
                await _send_results(context.bot, chat_id, fresh, automatic=True)
            except Exception:
                log.exception("automatic report delivery failed for chat %s", chat_id)
    except Exception:
        log.exception("automatic scan failed")


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
               "memory": memory, "system": system_status, "lab": lab_status}
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
    await application.bot.set_my_commands([
        BotCommand("start", "главное меню"), BotCommand("scan", "сканировать весь рынок"),
        BotCommand("short", "краткосрочные сделки"),
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
        ("prices", prices), ("movers", movers),
        ("alerts_on", alerts_on), ("alerts_off", alerts_off),
    ]:
        application.add_handler(CommandHandler(command, handler))
    application.add_handler(CallbackQueryHandler(callback))
    application.job_queue.run_repeating(auto_scan, interval=AUTO_SCAN_INTERVAL_MIN * 60, first=30, name="market-auto-scan")
    application.job_queue.run_repeating(track_signal_outcomes, interval=120, first=90, name="signal-quality-journal")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
