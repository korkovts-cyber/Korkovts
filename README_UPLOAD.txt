KORKOVTS SIGNAL AI — V11.4.1 PRECISION AUDIT

UPLOAD ALL FILES IN THIS ARCHIVE TO THE REPOSITORY ROOT.
Do not upload older V11.4 / V11.3 runtime files as replacements for these.

RAILWAY START
release_check_v1141.py -> test_v1141.py -> bot_v1141.py

WHAT V11.4.1 CHANGES

1. CONCURRENCY-SAFE NEWS FAILOVER
Each scan owns a task-local mutable news context. Main, Short, manual /signal and
news-triggered scans cannot leak HEALTHY/DEGRADED state into each other.
When all real news sources fail:
- directional news score is neutral
- real_sources = 0
- no headline/event is invented
- the Binance scanner stays alive
- Production quality threshold is +2 stricter

2. FULL QUALIFIED REVALIDATION POOL
Every candidate that already passed the core strategy, Production and Alpha
layers goes through final exchange-metadata and execution revalidation.
The old arbitrary top-12 execution cutoff is removed. Telegram is capped only
AFTER final execution/meta/portfolio selection.

3. TWO-SIDED EXECUTION COST
LONG: BUY entry impact + SELL exit impact.
SHORT: SELL entry impact + BUY exit impact.
The strategy's static round-trip fee/cost allowance remains included.
For 1H signals, one currently adverse funding payment is reserved conservatively.
This replaces the old approximation that doubled only the entry-side impact.

4. SCALE-AWARE LIQUIDITY STATE
THIN/DEEP is no longer based on one absolute $20,000 near-book cutoff.
Depth is measured relative to the standardized $5k execution probe, together
with live impact and spread.

5. BINANCE SERVER-CLOCK GUARD
A cached public /fapi/v1/time check measures midpoint clock offset and RTT.
Production pauses when absolute clock offset exceeds 2 seconds or the timing
request is excessively slow.

6. EXCHANGEINFO / TICK-SIZE CONTRACT
A cached public /fapi/v1/exchangeInfo snapshot validates:
- TRADING status
- PERPETUAL contract type
- PRICE_FILTER tickSize
- LOT_SIZE / MIN_NOTIONAL metadata
Final Entry/Stop/TP levels are rounded conservatively to Binance tick size,
then LONG/SHORT geometry invariants are rechecked.

7. DATA ACQUISITION FRESHNESS
Derivatives bundle acquisition time is measured. A deep derivatives snapshot
taking more than:
- 12 seconds for 1H
- 8 seconds for 15M
is treated as stale and cannot create a Production signal.
The exact acquisition timing and ADL age are stored in feature_json.

8. ENTRY QUALITY — NEGATIVE ONLY
After >=30 resolved forward observations for the same
setup x timeframe x side:
- activation rate <50% -> -1.0 PRO
- activation rate <35% -> -2.0 PRO
High activation rate never adds points.
Current WAITING signals and ambiguous OHLC observations are excluded.

9. AMBIGUOUS 1M CANDLES
A 1-minute OHLC candle cannot prove intrabar event order.
If one candle contains:
- Entry + pre-entry Stop, or
- active Stop + TP2
the result becomes AMBIGUOUS_ENTRY_STOP / AMBIGUOUS_SL_TP with 0R.
It is shown in history but excluded from Meta, Factor, Drift/Cohort and
Robustness learning.

10. CENTRAL BINANCE REQUEST GOVERNOR
The existing app.market retry/429/418 logic remains the source of truth.
V11.4.1 additionally adds a process-wide request-concurrency ceiling and a
stricter low-priority ceiling for aggTrades research. Repeated rate-limit
failures create a shared cooldown. Critical execution/ADL/OI/depth/time/
exchangeInfo endpoints are categorized as high priority.

11. DECISION LINEAGE HASH
Before a Production signal is persisted, a deterministic SHA256 is written to
feature_json together with feature_schema_version=11.4.1. The hash covers the
signal geometry, core score, final PRO and decision features and is idempotent.

12. MANUAL /SIGNAL PARITY
Manual analysis uses the same market_analysis_state path as the whole-market
scanner. Breadth conflict no longer causes the old manual-only hard rejection.
Manual signals also receive tick-size normalization, two-sided execution costs,
Entry Quality and Meta checks.

13. CLEAN V11.4.1 LEARNING COHORT
Meta, Factor Lab, Cohort/Drift, Challenger summary and Block Robustness use
release_version LIKE '11.4.1%'. Ambiguous observations are excluded from
adaptive learning.

14. EXISTING SAFETY RETAINED
- HTF mandatory confirmation
- OI/taker/ADL/spread/funding/basis/crowd gates
- L2 state-first microstructure
- Meta walk-forward gate and OOD ABSTAIN
- FDR factor control
- delivery-aware tracker
- durable Telegram outbox
- SQLite WAL + busy timeout + online backups
- Railway /data persistence fail-fast
- correlation/portfolio filtering
- automatic no-edge silence

IMPORTANT
This release does not guarantee profit. It is designed to reduce false
confidence, stale execution, statistical contamination and hidden runtime
failure modes. Live Binance/Telegram/Railway conditions can still reveal
environment-specific problems that cannot be reproduced by static/unit tests.
