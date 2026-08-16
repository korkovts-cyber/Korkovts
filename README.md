# Korkovts Signal AI · v10 Research

Russian-language Telegram research bot for liquid Binance USDⓈ-M perpetuals.
It uses public market data, never places orders and does not need an exchange
API key. v10 Research is a separate frozen cohort; the v9 archive remains the
control version.

## Decision engine

- scans every liquid USDT perpetual and returns every setup that clears the gate
- supports only two explicit setups: volume/OI breakout and trend pullback
- regular path: 15m entry, 1H setup, 4H confirmation
- short path: 5m entry, 15m setup, 1H confirmation
- persistent BTC 1H/4H regime; neutral regimes do not create trades
- extreme market-breadth disagreement turns the regime neutral
- EMA structure, RSI, MACD, ATR, ADX/DMI, Stochastic RSI, Bollinger, VWAP,
  OBV, volume z-score, efficiency, CVD, Ichimoku, Supertrend, MFI and CMF
- funding, open interest direction, taker flow, basis, crowding, spread and
  order-book context
- Binance symbol ADL risk is an independent safety gate: `high`, stale or
  missing ADL data blocks a final signal; `medium` tightens it and caps leverage
  at 1×
- three RSS feeds provide a news-risk filter; headlines never create a trade
- fees/slippage above 0.25R, incomplete derivatives data and spread above
  5 bps block a signal

## Research instrumentation

- the official Binance all-market liquidation WebSocket is collected in the
  background; v10 only records it and does **not** infer direction from it yet
- every issued signal stores a JSON snapshot of technical, derivatives, news,
  breadth, ADL and liquidation fields
- candidates blocked only by ADL or extreme breadth are followed silently as a
  shadow control cohort; they are never sent to Telegram and never affect the
  24-hour live-signal memory or portfolio limits
- 24-hour perpetual-basis change is recorded as a research factor; it does not
  change a trade until its value is confirmed on future outcomes
- recent high-impact regulatory, macro and market-structure headlines produce a
  freshness-weighted event-risk field; it is visible and recorded, but cannot
  create a direction or silently change the frozen rules in this cohort
- same-direction signals with 1H return correlation >= 0.82 are labelled as one
  risk cluster; none are hidden
- `/lab` reports the frozen cohort by timeframe, setup and direction
- `/system` shows BTC regime, breadth, ADL state, liquidation-stream warm-up and
  the first 100-outcome test progress
- closed candle sets are cached only until the next UTC close of their
  timeframe, reducing Binance request weight without serving a stale candle
- the outcome journal batches every live and shadow signal for the same symbol
  into one 1-minute history request per cycle

## Telegram

- `/start` — styled menu and automatic reports
- `/scan` — regular full-market scan
- `/short` — short-term full-market scan
- `/signal BTC` — one-coin analysis
- `/prices`, `/movers`, `/status`, `/memory`, `/system`, `/lab`
- `/alerts_on`, `/alerts_off`

The automatic scan runs every 10 minutes but sends only a genuinely new signal.
Duplicate memory is persistent for 24 hours by symbol, side and timeframe.
Lifecycle events are journalled silently and do not spam Telegram.

## Forward test

This version is ready for observation/paper testing, not for assuming
profitability.

1. Keep v10 Research parameters unchanged for the whole cohort.
2. Collect at least 100 closed **activated** signals. Track 1H and 15M
   separately.
3. Compare net R after costs, profit factor and max drawdown with the untouched
   v9 control. Win rate alone is not sufficient.
4. Liquidation telemetry may become a filter only after an out-of-sample test.
5. Compare the live cohort with the silent shadow cohort to measure whether the
   new safety gates prevented losses or merely removed profitable trades.
6. Reject the release if net R is negative after costs or the agreed drawdown
   limit is exceeded.

The displayed strength index is not a probability. Suggested leverage remains
1–2× and risk per trade 0.25–0.5%. Never average down, move a stop farther away,
or trade borrowed money.

## Evidence used

- Binance USD-M market-data documentation: ADL risk, basis, OI, taker and depth
- Binance USD-M public WebSocket documentation: all-market liquidation stream
- Chi et al., *Journal of Futures Markets*: basis is a strong cross-sectional
  crypto-futures factor, strongest at daily rather than monthly frequency
- Gbadebo (2026): time-series momentum outperformed cross-sectional momentum;
  high crypto correlations increased drawdown
- De Nicola (2021): 1–4 hour Bitcoin returns showed mean reversion after large
  moves, supporting the no-chasing rule

Full design decisions and rejected ideas are in `RESEARCH_V10_RU.md`.
