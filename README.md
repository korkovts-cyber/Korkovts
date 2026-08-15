# Universal Crypto Signal Bot

Telegram-only crypto signal bot. It reads public Binance Futures market data and does not place trades.

Features:
- full liquid Binance USDⓈ-M perpetual market scan (not a fixed coin list)
- 15m/1h/4h confirmation and BTC market-regime filter
- EMA, RSI, MACD, ATR, ADX, volume z-score and structure breakout
- funding-rate crowding penalty and open-interest snapshot
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
- /signal BTC
- /prices
- /movers
- /status
- /backtest BTC 1h

`MAX_SYMBOLS_TO_SCAN=0` scans every liquid USDT perpetual. Use
`MIN_24H_QUOTE_VOLUME` and `SCAN_CONCURRENCY` to control quality and API load.

This is a research/educational system, not a guarantee of profit. Validate the
strategy out of sample and paper-trade it before risking money.
