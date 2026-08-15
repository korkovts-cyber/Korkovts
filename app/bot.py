import asyncio,logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application,CommandHandler,ContextTypes
from .config import TELEGRAM_BOT_TOKEN,MIN_SIGNAL_SCORE
from .db import init,save,recent
from .market import get_klines
from .strategy import analyze,fmt
from .scanner import scan
from .backtest import run as bt

logging.basicConfig(level=logging.INFO)

async def start(u,c):
    await u.message.reply_text("🤖 Universal Crypto Signal Bot\n\n/scan — рынок\n/signal ARB — анализ\n/status — история\n/backtest ARB 1h — тест\n/help")

async def signal(u,c):
    sym=(c.args[0] if c.args else "BTCUSDT").upper()
    if not sym.endswith("USDT"): sym+="USDT"
    try:
        a,b=await asyncio.gather(get_klines(sym,"1h",400),get_klines(sym,"4h",400))
        s=analyze(sym,"1H",a,b,MIN_SIGNAL_SCORE)
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
    app.add_handler(CommandHandler("backtest", backtest))

    app.run_polling()


if __name__ == "__main__":
    main()
