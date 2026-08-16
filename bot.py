import asyncio,logging
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application,CommandHandler,CallbackQueryHandler
from .config import (TELEGRAM_BOT_TOKEN,MIN_SIGNAL_SCORE,TOP_COINS,
    AUTO_SCAN_INTERVAL_MIN,MAX_AUTO_SIGNALS,SIGNAL_COOLDOWN_HOURS,ROUND_TRIP_COST_PCT)
from .db import (init,save,recent,subscribe,subscribers,was_sent_recently,quality_stats,
    calibration_penalty,set_risk_profile,get_risk_profile,daily_risk_guard,
    portfolio_allowed,register_paper_trade,paper_stats,reset_paper_account,paper_trade_chats)
from .market import get_klines,get_prices,get_tickers,get_derivatives_snapshot
from .strategy import analyze,fmt
from .scanner import scan,market_state
from .tracker import update_outcomes
from .backtest import run as bt
from .risk import conservative_plan

logging.basicConfig(level=logging.INFO)

def menu(analyze_symbol=None):
    rows=[]
    if analyze_symbol:
        coin=analyze_symbol.replace("USDT","")
        rows.append([InlineKeyboardButton(f"🔎 Подробный анализ {coin}",callback_data=f"analyze:{analyze_symbol}")])
    rows += [
        [InlineKeyboardButton("🔥 СИЛЬНЫЕ СИГНАЛЫ",callback_data="scan")],
        [InlineKeyboardButton(f"{coin}/USDT",callback_data=f"price:{coin}USDT") for coin in TOP_COINS[:3]],
        [InlineKeyboardButton(f"{coin}/USDT",callback_data=f"price:{coin}USDT") for coin in TOP_COINS[3:6]],
        [InlineKeyboardButton("📊 РЫНОК",callback_data="prices"),InlineKeyboardButton("🚀 ЛИДЕРЫ",callback_data="movers")],
        [InlineKeyboardButton("📜 ИСТОРИЯ",callback_data="status"),InlineKeyboardButton("📈 СТАТИСТИКА",callback_data="stats")],
        [InlineKeyboardButton("🧪 ТЕСТ BTC",callback_data="backtest_btc")],
        [InlineKeyboardButton("🔔 ВКЛ. АВТО",callback_data="alerts_on"),InlineKeyboardButton("🔕 ВЫКЛ. АВТО",callback_data="alerts_off")],
        [InlineKeyboardButton("🛡 МОЙ РИСК",callback_data="risk_info")],
        [InlineKeyboardButton("🧾 PAPER-ПОРТФЕЛЬ",callback_data="paper")],
        [InlineKeyboardButton("🧹 ОЧИСТИТЬ ЧАТ",callback_data="clear_chat")]
    ]
    return InlineKeyboardMarkup(rows)

async def start(u,c):
    subscribe(u.effective_chat.id,True)
    await u.message.reply_text("🤖 <b>Universal Crypto Signal Bot</b>\n\n"
        "/scan — сильные сигналы всего Binance Futures\n/signal BTC — анализ монеты\n"
        "/prices — цены топ-монет\n/movers — рост и падение\n/status — история\n/stats — статистика\n"
        "/alerts_on — включить автосигналы\n/alerts_off — выключить\n"
        "/risk 1000 0.5 — капитал и риск 0,5%\n"
        "/paper — виртуальный портфель\n"
        "/backtest BTC 1h — тест",parse_mode=ParseMode.HTML,reply_markup=menu())

def guarded_text():
    state=daily_risk_guard()
    return ("🛡 <b>ЗАЩИТНАЯ ПАУЗА</b>\n\nНовые сигналы временно остановлены: "
        f"{state['reason']}. Возобновление произойдёт автоматически после выхода результатов из окна 24 часов."
        "\n\nНе пытайся отыгрывать убыток.")

def signal_text(s,chat_id,priority=False):
    text=fmt(s,priority); profile=get_risk_profile(chat_id)
    if not profile:
        return text+"\n\n🛡 Для расчёта безопасного размера: <code>/risk 1000 0.5</code>"
    plan=conservative_plan(s,profile["balance"],profile["risk_pct"],ROUND_TRIP_COST_PCT)
    return (text+f"\n\n🛡 <b>КОНСЕРВАТИВНЫЙ РИСК-ПЛАН</b>"
        f"\nКапитал: <b>{profile['balance']:.2f} USDT</b>"
        f"\nБазовый риск: <b>{profile['risk_pct']:.2f}%</b>"
        f"\nРиск с поправкой на волатильность: <b>{plan['effective_risk_pct']:.2f}% / {plan['risk_budget']:.2f} USDT</b>"
        f"\nМаксимальный размер: <b>{plan['qty']:.6g} {s.symbol.removesuffix('USDT')}</b>"
        f"\nОбъём позиции: <b>{plan['notional']:.2f} USDT</b>"
        f"\nФактический риск до стопа: <b>≈ {plan['actual_risk']:.2f} USDT</b>"
        f"\nВ расчёте заложено на комиссии/проскальзывание: <b>{ROUND_TRIP_COST_PCT:.2f}%</b>"
        "\nБез усреднения и переноса стоп-лосса.")

async def risk(u,c):
    msg=u.effective_message
    if not c.args:
        p=get_risk_profile(u.effective_chat.id)
        if p:
            return await msg.reply_text(f"🛡 Капитал: {p['balance']:.2f} USDT\nРиск: {p['risk_pct']:.2f}%\n\nИзменить: /risk 1000 0.5",reply_markup=menu())
        return await msg.reply_text("🛡 Укажи капитал и риск на сделку:\n/risk 1000 0.5\n\nМаксимально разрешено 1%.",reply_markup=menu())
    try:
        balance=float(c.args[0].replace(",",".")); risk_pct=float(c.args[1].replace(",",".")) if len(c.args)>1 else .5
        set_risk_profile(u.effective_chat.id,balance,risk_pct)
        await msg.reply_text(f"✅ Риск-профиль сохранён.\nКапитал: {balance:.2f} USDT\nРиск: {risk_pct:.2f}%",reply_markup=menu())
    except (ValueError,IndexError) as e:
        await msg.reply_text(f"Не удалось сохранить: {e}\nПример: /risk 1000 0.5",reply_markup=menu())

async def paper(u,c):
    account,summary,opened=paper_stats(u.effective_chat.id)
    initial,balance,peak,max_dd=account
    trades,wins,total_pnl,gains,losses=summary
    trades=trades or 0; wins=wins or 0; total_pnl=total_pnl or 0; gains=gains or 0; losses=losses or 0
    pf=gains/losses if losses else (999 if gains else 0)
    text=("🧾 <b>PAPER-ПОРТФЕЛЬ</b>\n\n"
        f"Стартовый баланс: <b>{initial:.2f} USDT</b>\nТекущий баланс: <b>{balance:.2f} USDT</b>\n"
        f"Результат: <b>{total_pnl:+.2f} USDT ({(balance/initial-1)*100:+.2f}%)</b>\n"
        f"Закрытых сделок: <b>{trades}</b> | Открытых/ожидающих: <b>{opened}</b>\n"
        f"Доля прибыльных: <b>{wins/trades*100 if trades else 0:.1f}%</b>\nProfit Factor: <b>{pf:.2f}</b>\n"
        f"Максимальная просадка: <b>{max_dd:.2f} USDT</b>\n\n"
        "Сбросить симуляцию: <code>/paper_reset 1000</code>")
    await u.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=menu())

async def paper_reset(u,c):
    try:
        balance=float(c.args[0].replace(",",".")) if c.args else 1000
        reset_paper_account(u.effective_chat.id,balance)
        await u.effective_message.reply_text(f"✅ Paper-портфель сброшен. Баланс: {balance:.2f} USDT",reply_markup=menu())
    except ValueError as e: await u.effective_message.reply_text(f"Ошибка: {e}",reply_markup=menu())

async def signal(u,c):
    msg=u.effective_message
    if daily_risk_guard()["locked"]:
        return await msg.reply_text(guarded_text(),parse_mode=ParseMode.HTML,reply_markup=menu())
    sym=(c.args[0] if c.args else "BTCUSDT").upper()
    if not sym.endswith("USDT"): sym+="USDT"
    try:
        from .news import get_news_sentiment,for_symbol
        lower,a,b,d,state,news=await asyncio.gather(get_klines(sym,"15m",300),get_klines(sym,"1h",400),
            get_klines(sym,"4h",400),get_derivatives_snapshot(sym),market_state(),get_news_sentiment())
        threshold=min(92,MIN_SIGNAL_SCORE+state["score_adjustment"])
        first=analyze(sym,"1H",a,b,threshold,lower,state["bias"],d,for_symbol(news,sym))
        penalty=calibration_penalty(sym,first.side) if first else 0
        s=analyze(sym,"1H",a,b,min(95,threshold+penalty),lower,state["bias"],d,for_symbol(news,sym))
        if s:
            allowed,reason=portfolio_allowed(u.effective_chat.id,s)
            if not allowed: return await msg.reply_text(f"🛡 Сигнал найден, но paper-портфель его заблокировал: {reason}.",reply_markup=menu())
            signal_id=save(s); register_paper_trade(u.effective_chat.id,signal_id,s)
            await msg.reply_text(signal_text(s,u.effective_chat.id,True),parse_mode=ParseMode.HTML,reply_markup=menu())
        else: await msg.reply_text(f"⚪ {sym}: сильного сигнала нет.",reply_markup=menu())
    except Exception as e: await msg.reply_text(f"Ошибка: {e}",reply_markup=menu())

async def scan_cmd(u,c):
    msg=u.effective_message
    if daily_risk_guard()["locked"]:
        return await msg.reply_text(guarded_text(),parse_mode=ParseMode.HTML,reply_markup=menu())
    await msg.reply_text("🔎 Сканирую рынок...")
    try:
        rs=await scan()
        if not rs: return await msg.reply_text("⚪ Сильных сигналов нет.",reply_markup=menu())
        selected=None
        for s in rs:
            allowed,_=portfolio_allowed(u.effective_chat.id,s)
            if allowed: selected=s; break
        if not selected: return await msg.reply_text("🛡 Сигналы найдены, но портфельные лимиты запретили новую позицию.",reply_markup=menu())
        signal_id=save(selected); register_paper_trade(u.effective_chat.id,signal_id,selected)
        await msg.reply_text(signal_text(selected,u.effective_chat.id,True),parse_mode=ParseMode.HTML,reply_markup=menu())
    except Exception as e: await msg.reply_text(f"Ошибка: {e}",reply_markup=menu())

async def status(u,c):
    rows=recent()
    def line(r):
        if r[6] and r[7] is not None: result=f" → {r[6]} ({r[7]:+.1f}R)"
        elif r[5]=="WAITING": result=" → ожидает вход"
        else: result=" → активен"
        return f"{r[0][:16]} | {r[1]} {r[3]} | {r[4]:.0f}{result}"
    await u.effective_message.reply_text("\n".join(line(r) for r in rows) or "История пуста.",reply_markup=menu())

async def stats(u,c):
    row,by_side,opened=quality_stats(30)
    total,tp1,tp2,tp3,sl,expired,avg_r,sum_r,avg_mae=row
    if not total:
        return await u.effective_message.reply_text("📈 Закрытых сигналов пока нет. Статистика появится после TP/SL.",reply_markup=menu())
    wins=(tp1 or 0)+(tp2 or 0)+(tp3 or 0)
    lines=["📈 <b>СТАТИСТИКА ЗА 30 ДНЕЙ</b>","",
        f"Закрыто: <b>{total}</b> | Открыто: <b>{opened}</b>",
        f"Достигли цели: <b>{wins}</b> | SL: <b>{sl or 0}</b>",
        f"Истекли по времени: <b>{expired or 0}</b>",
        f"Доля прибыльных: <b>{wins/total*100:.1f}%</b>",
        f"Средний результат: <b>{(avg_r or 0):+.2f}R</b>",
        f"Суммарный результат: <b>{(sum_r or 0):+.2f}R</b>",
        f"Средняя максимальная просадка: <b>{(avg_mae or 0):.2f}R</b>"]
    for side,count,side_r,winrate in by_side:
        lines.append(f"{side}: {count} | {(winrate or 0):.1f}% | {(side_r or 0):+.2f}R")
    lines.append("\n⚠️ Статистика модели не гарантирует будущий результат.")
    await u.effective_message.reply_text("\n".join(lines),parse_mode=ParseMode.HTML,reply_markup=menu())

def _price_text(symbol,row):
    arrow="🟢" if row["change"]>=0 else "🔴"
    return f'{arrow} <b>{symbol.replace("USDT","")}/USDT</b>: {row["price"]:.8g} ({row["change"]:+.2f}%)'

async def prices(u,c):
    rows=await get_prices([f"{x}USDT" for x in TOP_COINS])
    await u.effective_message.reply_text("<b>Актуальные цены Binance Futures</b>\n\n"+
        "\n".join(_price_text(s,r) for s,r in rows.items()),parse_mode=ParseMode.HTML,reply_markup=menu())

async def movers(u,c):
    rows=await get_tickers()
    liquid=[(s,r) for s,r in rows.items() if s.endswith("USDT") and r["quote_volume"]>=15_000_000]
    up=sorted(liquid,key=lambda x:x[1]["change"],reverse=True)[:5]
    down=sorted(liquid,key=lambda x:x[1]["change"])[:5]
    text="🚀 <b>Топ роста 24ч</b>\n"+"\n".join(_price_text(s,r) for s,r in up)
    text+="\n\n📉 <b>Топ падения 24ч</b>\n"+"\n".join(_price_text(s,r) for s,r in down)
    await u.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=menu())

async def alerts_on(u,c):
    subscribe(u.effective_chat.id,True)
    await u.effective_message.reply_text(f"🔔 Автосигналы включены. Проверка каждые {AUTO_SCAN_INTERVAL_MIN} минут.",reply_markup=menu())

async def alerts_off(u,c):
    subscribe(u.effective_chat.id,False)
    await u.effective_message.reply_text("🔕 Автосигналы выключены.",reply_markup=menu())

async def clear_chat(u,c):
    chat_id=u.effective_chat.id
    last_id=u.effective_message.message_id
    deleted=0
    for message_id in range(last_id,max(0,last_id-80),-1):
        try:
            await c.bot.delete_message(chat_id,message_id); deleted+=1
        except Exception:
            pass
    await c.bot.send_message(chat_id,f"🧹 Чат очищен. Удалено сообщений: {deleted}",reply_markup=menu())

async def auto_scan(c):
    chats=subscribers()
    if not chats: return
    if daily_risk_guard()["locked"]:
        logging.warning("automatic signals paused by daily risk guard"); return
    try: rows=await scan()
    except Exception as e:
        logging.exception("automatic scan failed: %s",e); return
    fresh=[s for s in rows if not was_sent_recently(s.symbol,s.side,SIGNAL_COOLDOWN_HOURS)][:MAX_AUTO_SIGNALS]
    for i,s in enumerate(fresh):
        signal_id=None
        for chat_id in chats:
            allowed,reason=portfolio_allowed(chat_id,s)
            if not allowed: continue
            if signal_id is None: signal_id=save(s)
            register_paper_trade(chat_id,signal_id,s)
            try: await c.bot.send_message(chat_id,signal_text(s,chat_id,i==0),parse_mode=ParseMode.HTML,reply_markup=menu())
            except Exception as e: logging.warning("send to %s failed: %s",chat_id,e)

async def track_signals(c):
    try: closed=await update_outcomes()
    except Exception as e:
        logging.exception("signal tracking failed: %s",e); return
    if not closed: return
    for event,signal_id,symbol,result,settled in closed:
        for chat_id in paper_trade_chats(signal_id):
            if event=="ACTIVE": text=f"✅ {symbol} · цена вошла в зону. Сигнал #{signal_id} теперь <b>АКТИВЕН</b>."
            elif result=="ENTRY_EXPIRED": text=f"⌛ {symbol} · зона входа сигнала #{signal_id} не была достигнута. Сигнал отменён без сделки."
            else: text=f"📍 {symbol} · сигнал #{signal_id} закрыт: <b>{result}</b>"
            try: await c.bot.send_message(chat_id,text,parse_mode=ParseMode.HTML,reply_markup=menu())
            except Exception as e: logging.warning("outcome send failed: %s",e)
        for chat_id,pnl,balance in settled:
            try: await c.bot.send_message(chat_id,f"🧾 Paper PnL: <b>{pnl:+.2f} USDT</b> · Баланс: <b>{balance:.2f} USDT</b>",parse_mode=ParseMode.HTML)
            except Exception as e: logging.warning("paper outcome send failed: %s",e)

async def callback(u,c):
    q=u.callback_query; await q.answer()
    if q.data=="prices": return await prices(u,c)
    if q.data=="movers": return await movers(u,c)
    if q.data=="scan": return await scan_cmd(u,c)
    if q.data=="alerts_on": return await alerts_on(u,c)
    if q.data=="alerts_off": return await alerts_off(u,c)
    if q.data=="clear_chat": return await clear_chat(u,c)
    if q.data=="status": return await status(u,c)
    if q.data=="stats": return await stats(u,c)
    if q.data=="risk_info":
        c.args=[]; return await risk(u,c)
    if q.data=="paper": return await paper(u,c)
    if q.data=="backtest_btc":
        c.args=["BTC","1h"]; return await backtest(u,c)
    if q.data.startswith("analyze:"):
        c.args=[q.data.split(":",1)[1]]; return await signal(u,c)
    if q.data.startswith("price:"):
        sym=q.data.split(":",1)[1]; row=(await get_prices([sym])).get(sym)
        await q.message.reply_text(_price_text(sym,row) if row else "Цена недоступна.",parse_mode=ParseMode.HTML,reply_markup=menu(sym))

async def backtest(u,c):
    sym=(c.args[0] if c.args else "BTCUSDT").upper()
    if not sym.endswith("USDT"): sym+="USDT"
    tf=c.args[1] if len(c.args)>1 else "1h"
    msg=u.effective_message
    await msg.reply_text("🧪 Проверяю стратегию на истории...")
    try:
        r=await bt(sym,tf)
        warning="\n⚠️ Выборка мала — выводы делать рано." if r['trades']<20 else ""
        await msg.reply_text(f"🧪 <b>{sym} {tf}</b>\nСделок: {r['trades']}\nУспешных: {r['wins']}\n"
            f"Убыточных: {r['losses']}\nДоля прибыльных: {r['win_rate']:.1f}%\n"
            f"Результат: {r['net_r']:+.2f}R\nProfit Factor: {r['profit_factor']:.2f}\n"
            f"Макс. просадка: {r['max_drawdown_r']:.2f}R\nУчтённые издержки: {r['cost_pct']:.2f}%{warning}",
            parse_mode=ParseMode.HTML,reply_markup=menu())
    except Exception as e: await msg.reply_text(f"Ошибка: {e}",reply_markup=menu())

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Add TELEGRAM_BOT_TOKEN to .env")

    init()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("risk", risk))
    app.add_handler(CommandHandler("paper", paper))
    app.add_handler(CommandHandler("paper_reset", paper_reset))
    app.add_handler(CommandHandler("prices", prices))
    app.add_handler(CommandHandler("movers", movers))
    app.add_handler(CommandHandler("alerts_on", alerts_on))
    app.add_handler(CommandHandler("alerts_off", alerts_off))
    app.add_handler(CommandHandler("backtest", backtest))
    app.add_handler(CallbackQueryHandler(callback))

    app.job_queue.run_repeating(auto_scan,interval=AUTO_SCAN_INTERVAL_MIN*60,first=60,name="market-auto-scan")
    app.job_queue.run_repeating(track_signals,interval=120,first=90,name="signal-outcome-tracker")

    app.run_polling()


if __name__ == "__main__":
    main()
