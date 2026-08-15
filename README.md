# Universal Crypto Signal Bot

Telegram-only crypto signal bot. It reads public Binance Futures market data and does not place trades.

Features:
- market scan for USDT perpetuals
- 5m/15m/1h/4h/1d data
- EMA, RSI, MACD, ATR, volume
- higher-timeframe confirmation
- LONG/SHORT scoring
- entry/SL/TP1/TP2/TP3
- risk-based position sizing
- SQLite signal history
- simple walk-forward backtest

## Install
1. Create a Telegram bot using BotFather.
2. Copy `.env.example` to `.env` and add the Telegram token.
3. `pip install -r requirements.txt`
4. `python -m app.bot`

Commands:
- /start
- /scan
- /signal ARB
- /status
- /backtest ARB 1h

This is a research/educational system, not a guarantee of profit.
