# Universal Crypto Signal Bot

Telegram-only crypto signal bot. It reads public Binance Futures market data and does not place trades.

Features:
- full liquid Binance USDⓈ-M perpetual market scan (not a fixed coin list)
- 15m/1h/4h confirmation and BTC market-regime filter
- EMA, RSI, MACD, ATR, ADX/DMI, Stochastic RSI, Bollinger Bands, VWAP,
  OBV, volume z-score and structure breakout
- trend-efficiency, 24-hour momentum and historical taker-flow/CVD confirmation
  calculated from Binance's taker-buy kline field
- two-stage selection: full technical scan followed by deep analysis of the
  strongest candidates
- funding, open-interest change, taker buy/sell flow, futures basis,
  long/short crowding, top-position ratio, spread and order-book imbalance
- cached Russian-language news-context filter using three independent RSS feeds
- LONG/SHORT scoring
- entry/SL/TP1/TP2/TP3
- SQLite signal history
- automatic TP/SL/expiry outcome tracking and 30-day quality statistics in R
- realistic signal lifecycle: `WAITING` until the entry price is actually
  traded, then `ACTIVE`; an untouched entry zone expires without a fake trade
- per-chat paper portfolio with simulated quantity, costs, realized PnL,
  balance, Profit Factor and drawdown
- forward-result calibration that can tighten weak symbol/direction setups after
  enough samples (it never lowers the quality threshold)
- simple walk-forward backtest
- Russian inline-button menu, automatic alerts every 10 minutes, cooldown and
  chat cleanup button
- conservative risk profile (`/risk 1000 0.5`) with position sizing from the
  worst edge of the entry zone, a cost/slippage buffer and a 1x notional cap
- volatility-scaled risk: configured risk is automatically reduced by 25–50%
  when ATR volatility is elevated
- circuit breaker: one automatic signal per scan, at most two open signals,
  pause after two consecutive stops or -2R over the rolling 24-hour window
- portfolio guard blocks a second same-direction crypto exposure and caps total
  planned risk at 1.5% of paper equity

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
- /alerts_on
- /alerts_off
- /status
- /stats
- /risk 1000 0.5
- /paper
- /paper_reset 1000
- /backtest BTC 1h

`MAX_SYMBOLS_TO_SCAN=0` scans every liquid USDT perpetual. Use
`MIN_24H_QUOTE_VOLUME` and `SCAN_CONCURRENCY` to control quality and API load.

This is a research/educational system, not a guarantee of profit. Validate the
strategy out of sample and paper-trade it before risking money.

Automatic scans run every 10 minutes by default. `/start` and `/alerts_on`
subscribe the current chat; `/alerts_off` disables automatic delivery.

For Railway persistence, attach a Volume mounted at `/data` and set
`DATABASE_PATH=/data/signals.db`. Without a Volume, Railway can discard SQLite
history and subscriptions during a redeploy.

The news and order-book layers are confirmation filters, not standalone trade
triggers. Historical backtests cover the candle-based core only. Paper-trade
and collect forward results before changing the score threshold or risking money.

The in-bot backtest is a quick, conservative diagnostic. It aligns lower and
higher timeframes to the historical decision time, assumes the stop is hit first
when stop and target share a candle, and includes a configurable round-trip cost.
Small samples are explicitly marked as insufficient.

The risk controls reduce exposure; they cannot eliminate market, liquidity,
exchange or execution risk. Do not average down or move the stop farther away.

`/risk` configures the sizing model. `/paper_reset` starts a fresh simulation
and should only be used deliberately. Signals are not sent to Binance and no
exchange API key is required.

The paper model uses one deterministic exit at TP2 so live forward statistics
and the in-bot backtest follow the same 1:2 rule. TP1 and TP3 remain reference
levels. When stop and TP2 occur inside the same candle, the stop is counted first.
