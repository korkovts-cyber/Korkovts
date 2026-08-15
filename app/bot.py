import asyncio,logging
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application,CommandHandler,CallbackQueryHandler
from .config import TELEGRAM_BOT_TOKEN,MIN_SIGNAL_SCORE,TOP_COINS
from .db import init,save,recent
from .market import get_klines,get_prices,get_tickers,get_derivatives_snapshot
from .strategy import analyze,fmt
from .scanner import scan,market_regime
from .backtest import run as bt

logging.basicConfig(level=logging.INFO)

async def start(u,c):
    buttons=[[InlineKeyboardButton(f"{coin}/USDT",callback_data=f"price:{coin}USDT") for coin in TOP_COINS[:3]],
             [InlineKeyboardButton(f"{coin}/USDT",callback_data=f"price:{coin}USDT") for coin in TOP_COINS[3:6]],
             [InlineKeyboardButton("📊 Все цены",callback_data="prices"),
              InlineKeyboardButton("🚀 Лидеры",callback_data="movers")]]
    await u.message.reply_text("🤖 <b>Universal Crypto Signal Bot</b>\n\n"
        "/scan — сильные сигналы всего Binance Futures\n/signal BTC — анализ монеты\n"
        "/prices — цены топ-монет\n/movers — рост и падение\n/status — история\n"
        "/backtest BTC 1h — тест",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(buttons))

async def signal(u,c):
    sym=(c.args[0] if c.args else "BTCUSDT").upper()
    if not sym.endswith("USDT"): sym+="USDT"
    try:
        lower,a,b,d,bias=await asyncio.gather(get_klines(sym,"15m",300),get_klines(sym,"1h",400),
            get_klines(sym,"4h",400),get_derivatives_snapshot(sym),market_regime())
        s=analyze(sym,"1H",a,b,MIN_SIGNAL_SCORE,lower,bias,d)
        if s: save(s); await u.message.reply_text(fmt(s),parse_mode=ParseMode.HTML)
        else: await u.message.reply_text(f"⚪ {sym}: сильного сигнала нет.")
    except Exception as e: await u.message.reply_text(f"Ошибка: {e}")

async def scan_cmd(u,c):
    await u.message.reply_text("🔎 Сканирую рынок...")
    try:
        rs=await scan()
        if not rs: return await u.message.reply_text("⚪ Сильных сигналов нет.")
        for s in rs[:5]:
            save(s); await u.message.reply_text(fmt(s),parse_mode=ParseMode.HTML)
    except Exception as e: await u.message.reply_text(f"Ошибка: {e}")

async def status(u,c):
    rows=recent()
    await u.message.reply_text("\n".join(f"{r[0]} | {r[1]} {r[2]} | {r[3]} | {r[4]:.0f}" for r in rows) or "История пуста.")

def _price_text(symbol,row):
    arrow="🟢" if row["change"]>=0 else "🔴"
    return f'{arrow} <b>{symbol.replace("USDT","")}/USDT</b>: {row["price"]:.8g} ({row["change"]:+.2f}%)'

async def prices(u,c):
    rows=await get_prices([f"{x}USDT" for x in TOP_COINS])
    await u.effective_message.reply_text("<b>Актуальные цены Binance Futures</b>\n\n"+
        "\n".join(_price_text(s,r) for s,r in rows.items()),parse_mode=ParseMode.HTML)

async def movers(u,c):
    rows=await get_tickers()
    liquid=[(s,r) for s,r in rows.items() if s.endswith("USDT") and r["quote_volume"]>=15_000_000]
    up=sorted(liquid,key=lambda x:x[1]["change"],reverse=True)[:5]
    down=sorted(liquid,key=lambda x:x[1]["change"])[:5]
    text="🚀 <b>Топ роста 24ч</b>\n"+"\n".join(_price_text(s,r) for s,r in up)
    text+="\n\n📉 <b>Топ падения 24ч</b>\n"+"\n".join(_price_text(s,r) for s,r in down)
    await u.effective_message.reply_text(text,parse_mode=ParseMode.HTML)

async def callback(u,c):
    q=u.callback_query; await q.answer()
    if q.data=="prices": return await prices(u,c)
    if q.data=="movers": return await movers(u,c)
    if q.data.startswith("price:"):
        sym=q.data.split(":",1)[1]; row=(await get_prices([sym])).get(sym)
        await q.message.reply_text(_price_text(sym,row) if row else "Цена недоступна.",parse_mode=ParseMode.HTML)

async def backtest(u,c):
    sym=(c.args[0] if c.args else "BTCUSDT").upper()
    if not sym.endswith("USDT"): sym+="USDT"
    tf=c.args[1] if len(c.args)>1 else "1h"
    await u.message.reply_text("🧪 Backtest...")
    try:
        t,w,l,r=await bt(sym,tf)
        await u.message.reply_text(f"🧪 {sym} {tf}\nTrades: {t}\nWins: {w}\nLosses: {l}\nWin rate: {r:.1f}%")
    except Exception as e: await u.message.reply_text(f"Ошибка: {e}")

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
    app.add_handler(CommandHandler("prices", prices))
    app.add_handler(CommandHandler("movers", movers))
    app.add_handler(CommandHandler("backtest", backtest))
    app.add_handler(CallbackQueryHandler(callback))

    app.run_polling()


if __name__ == "__main__":
    main()
