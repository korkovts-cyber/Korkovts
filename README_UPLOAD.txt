KORKOVTS SIGNAL AI — V11.2.1 AUDITED

FINAL PRE-RELEASE AUDIT PACKAGE.
Self-contained over the current app/ directory.

UPLOAD TO REPOSITORY ROOT:
- v11_engine.py
- v11_liquidity.py
- v11_live.py
- v11_manager.py
- v11_ui.py
- v112_alpha.py
- v112_lab.py
- v112_health.py
- bot_v1121.py
- test_v1121.py
- railway.toml

AUDIT FIXES
1. Wider internal candidate pool.
Core scanner now passes up to 8 already-qualified signals to Production.
Telegram still publishes only max 4 (or configured neutral-mode max).
This lets execution/liquidity/Alpha/portfolio ranking choose a genuinely better #1.

2. Reduced Binance REST load.
- one depth snapshot per symbol is reused for $1k and $5k impact;
- one all-market ticker snapshot per Alpha batch;
- one BTC candle snapshot per timeframe per Alpha batch;
- explicit timeouts on extra Production calls.

3. Liquidity failure is no longer treated as zero slippage.
Fallback is conservative and UI keeps it marked unavailable.

4. WebSocket uses the explicit websockets 15 asyncio client API.

5. Production Health now checks database availability as well as:
- Binance REST
- latency
- BTC 1m freshness
- server clock skew
- WebSocket health

6. Database persistence warning.
If DATABASE_PATH is not under /data/, HEALTH becomes DEGRADED (not PAUSE).
For Railway persistence, configure a Volume mounted at /data and set:
DATABASE_PATH=/data/signals.db

7. V11.2 ranking consistency fixes are preserved:
- Alpha cannot rescue a Production-rejected signal.
- Negative Alpha can remove a borderline signal below 75.
- Telegram displays the same frozen Alpha-adjusted rank that was selected.
- Factor weights stay 1.00 until enough closed forward observations exist.

8. Railway runs the test suite before bot startup.

IMPORTANT
No software can guarantee that no future runtime/API failure will ever occur.
This release is designed to fail safely: missing mandatory market/database data
causes PAUSE/error rather than manufacturing a signal.

9. Race-condition hardening.
Final Production regime/neutral limits are derived from the signal's own frozen
market_context, not from a mutable process-wide status variable. Pressing
SYSTEM during a scan therefore cannot alter that scan's final candidate limit.
