import asyncio
import logging
from datetime import datetime, timezone

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from .config import AUTO_SCAN_INTERVAL_MIN, MIN_SIGNAL_SCORE, SIGNAL_COOLDOWN_HOURS, TELEGRAM_BOT_TOKEN, TOP_COINS
from .db import init, recent, save, subscribe, subscribers, was_sent_recently
from .market import get_derivatives_snapshot, get_klines, get_prices, get_tickers
from .scanner import market_state, scan, scan_short
from .strategy import analyze, fmt
from .tracker import update_outcomes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
_scan_lock = asyncio.Lock()


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
        [InlineKeyboardButton("🗂 ИСТОРИЯ", callback_data="status"), InlineKeyboardButton("🧹 ОЧИСТИТЬ", callback_data="clear_chat")],
    ]
    return InlineKeyboardMarkup(rows)


async def start(update, context):
    subscribe(update.effective_chat.id, True)
    text = (
        "⚡ <b>KORKOVTS SIGNAL AI</b>\n━━━━━━━━━━━━━━━━━━\n"
        "📡 Весь рынок Binance Futures USDT-M\n"
        "🧠 Многофакторный анализ 15m · 1H · 4H\n"
        f"⏱ Автоскан каждые {AUTO_SCAN_INTERVAL_MIN} минут\n\n"
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
    threshold = min(94, MIN_SIGNAL_SCORE + state["score_adjustment"])
    return analyze(symbol, "1H", hourly, higher, threshold, lower, state["bias"], derivatives, for_symbol(news, symbol))


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
    await bot.send_message(
        chat_id,
        f"📡 <b>{label} ЗАВЕРШЁН</b> · {stamp}\n━━━━━━━━━━━━━━━━━━\n"
        f"Найдено сильных сигналов: <b>{len(results)}</b>\n"
        "Они отсортированы по силе; первый — приоритетный.",
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
    rows = recent(15)
    if not rows:
        text = "🗂 История сигналов пока пуста."
    else:
        lines = ["🗂 <b>ПОСЛЕДНИЕ СИГНАЛЫ</b>", "━━━━━━━━━━━━━━━━━━"]
        result_names={"TP2":"✅ TP2","SL":"🛑 STOP","ENTRY_EXPIRED":"⌛ НЕ АКТИВИРОВАН","EXPIRED":"⏱ ЗАВЕРШЁН"}
        for created, symbol, timeframe, side, score, state, result, pnl_r in rows:
            icon = "🟢" if side == "LONG" else "🔴"
            outcome=result_names.get(result,"🟡 ОЖИДАЕТ" if state=="SENT" else "🔵 АКТИВЕН")
            if pnl_r is not None and result not in ("ENTRY_EXPIRED",None):
                outcome+=f" · {pnl_r:+.2f}R"
            lines.append(f"{icon} {created[:16]} · <b>{symbol}</b> · {timeframe} · {side} · {score:.0f}/100\n└ {outcome}")
        text = "\n".join(lines)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu())


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
        f"✅ <b>АВТОСКАН ВКЛЮЧЁН</b>\nОтчёт будет приходить каждые {AUTO_SCAN_INTERVAL_MIN} минут — даже если сигналов нет.",
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
        for result in fresh:
            save(result)
        for chat_id in chats:
            try:
                if all_results and not fresh:
                    stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
                    await context.bot.send_message(
                        chat_id,
                        f"📡 <b>АВТОСКАН ЗАВЕРШЁН</b> · {stamp}\n━━━━━━━━━━━━━━━━━━\n"
                        f"Сильных условий на рынке: <b>{len(all_results)}</b>.\n"
                        f"Новых сигналов нет: эти пары уже отправлялись в последние {SIGNAL_COOLDOWN_HOURS} ч.",
                        parse_mode=ParseMode.HTML, reply_markup=menu())
                else:
                    await _send_results(context.bot, chat_id, fresh, automatic=True)
            except Exception:
                log.exception("automatic report delivery failed for chat %s", chat_id)
    except Exception:
        log.exception("automatic scan failed")
        for chat_id in chats:
            try:
                await context.bot.send_message(
                    chat_id,
                    "⚠️ <b>АВТОСКАН НЕ ВЫПОЛНЕН</b>\nИсточник рыночных данных временно недоступен. Следующая попытка будет автоматически.",
                    parse_mode=ParseMode.HTML, reply_markup=menu())
            except Exception:
                log.exception("automatic error delivery failed for chat %s", chat_id)


async def track_signal_outcomes(context):
    """Background quality journal; no paper account and no test commands."""
    try:
        events=await update_outcomes()
    except Exception:
        log.exception("signal outcome tracking failed")
        return
    if not events:
        return
    all_subscribers=subscribers()
    labels={"TP2":"✅ достигнута цель 2 (+2R)","SL":"🛑 достигнут стоп-лосс (-1R)",
            "ENTRY_EXPIRED":"⌛ зона входа не была достигнута — сигнал отменён",
            "EXPIRED":"⏱ время сигнала истекло"}
    for event,signal_id,symbol,result,source_chat_id in events:
        recipients=[source_chat_id] if source_chat_id is not None else all_subscribers
        if event=="ACTIVE":
            text=f"🎯 <b>{symbol}</b> · цена вошла в зону. Сигнал #{signal_id} активирован."
        else:
            text=f"📍 <b>{symbol}</b> · {labels.get(result,result)} · сигнал #{signal_id}."
        for chat_id in recipients:
            try:
                await context.bot.send_message(chat_id,text,parse_mode=ParseMode.HTML,reply_markup=menu())
            except Exception:
                log.exception("outcome delivery failed for chat %s",chat_id)


async def callback(update, context):
    query = update.callback_query
    await query.answer()
    actions = {"prices": prices, "movers": movers, "scan": scan_cmd, "short_scan": short_scan_cmd, "alerts_on": alerts_on,
               "alerts_off": alerts_off, "clear_chat": clear_chat, "status": status}
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
    await application.bot.set_my_commands([
        BotCommand("start", "главное меню"), BotCommand("scan", "сканировать весь рынок"),
        BotCommand("short", "краткосрочные сделки"),
        BotCommand("signal", "анализ монеты: /signal BTC"), BotCommand("prices", "цены топ-монет"),
        BotCommand("movers", "лидеры роста и падения"), BotCommand("status", "история сигналов"),
        BotCommand("alerts_on", "включить автоскан"), BotCommand("alerts_off", "выключить автоскан"),
    ])


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Add TELEGRAM_BOT_TOKEN to .env")
    init()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    for command, handler in [
        ("start", start), ("help", start), ("signal", signal), ("scan", scan_cmd), ("short", short_scan_cmd),
        ("status", status), ("prices", prices), ("movers", movers),
        ("alerts_on", alerts_on), ("alerts_off", alerts_off),
    ]:
        application.add_handler(CommandHandler(command, handler))
    application.add_handler(CallbackQueryHandler(callback))
    application.job_queue.run_repeating(auto_scan, interval=AUTO_SCAN_INTERVAL_MIN * 60, first=30, name="market-auto-scan")
    application.job_queue.run_repeating(track_signal_outcomes, interval=120, first=90, name="signal-quality-journal")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
