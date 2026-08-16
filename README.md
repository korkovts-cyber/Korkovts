# Korkovts Signal AI

Russian-language Telegram research bot for the liquid Binance USDⓈ-M perpetual
market. It reads public market data and never places orders or requires an
exchange API key.

## What it does

- scans every liquid USDT perpetual (`MAX_SYMBOLS_TO_SCAN=0`)
- sends every setup that passes the quality gate, strongest first
- regular mode: 15m entry, 1H setup, 4H confirmation, expected 6–48 hours
- short-term mode: 5m entry, 15m setup, 1H confirmation, expected 30m–4h
- EMA structure, RSI, MACD, ATR, ADX/DMI, Stochastic RSI, Bollinger, VWAP,
  OBV, volume z-score, breakout, efficiency and CVD
- independent Ichimoku, Supertrend, MFI and CMF confirmations
- funding, open interest, taker flow, basis, long/short ratios, spread and
  order-book imbalance
- cached Russian news-sentiment context from independent RSS sources
- automatic full-market reports every 10 minutes, including explicit empty or
  data-source-error reports
- background signal lifecycle journal: waiting zone, activation, TP2, stop,
  expiry and R-result; regular and short-term modes are evaluated separately
- after at least 20 forward outcomes, weak symbol/direction/timeframe groups can
  only receive a stricter score threshold, never an easier one
- stylish inline controls, top-coin prices, movers, history and chat cleanup

## Commands

- `/start` — menu and enable automatic reports
- `/scan` — regular full-market scan
- `/short` — short-term full-market scan
- `/signal BTC` — analysis of one coin
- `/prices` — prices of top coins
- `/movers` — liquid 24-hour movers
- `/status` — signal history
- `/alerts_on` and `/alerts_off` — automatic reports

The old `/backtest`, `/paper`, `/paper_reset`, `/stats` and `/risk` interfaces
are intentionally not registered in this version.

## Deploy

1. Add `TELEGRAM_BOT_TOKEN` to Railway Variables.
2. Keep `AUTO_SCAN_INTERVAL_MIN=10`.
3. Set `MAX_SYMBOLS_TO_SCAN=0` and `DEEP_ANALYSIS_LIMIT=0` for the full market.
4. For persistent subscriptions/history attach a Railway volume at `/data` and
   set `DATABASE_PATH=/data/signals.db`.
5. Deploy, then send `/start` or press `🔔 АВТО: ВКЛ` once.

The bot reports a maximum suggested leverage of 1–2× based on volatility and a
time window, not a promised closing time. No indicator can guarantee profit.
Do not use borrowed money, average down, or move a stop farther from entry.
