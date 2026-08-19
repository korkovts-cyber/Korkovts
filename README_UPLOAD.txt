KORKOVTS SIGNAL AI V11.13.0 · GLOBAL INTELLIGENCE · AUDITED

UPLOAD
1. Upload/extract ALL files from this ZIP into the ROOT of the existing korkovts-cyber/Korkovts repository.
2. KEEP the existing app/ directory. This release is an overlay and intentionally does not ship app/.
3. Do not mix files from another base repository. preflight_v11100.py verifies exact Git-blob fingerprints of the audited app/ package.
4. railway.toml is the production entrypoint. Railway starts bot_v11130.py only after the full preflight / compile / test / release-check chain succeeds.

FUTURES AUTO CONTRACT
- MAIN full market scan + Telegram status: every 10 minutes.
- If the market was fully checked and there is no actionable entry, AUTO says so explicitly.
- If mandatory data/clock/health is unavailable, AUTO reports a safety pause instead of pretending that the market had no signals.
- Offset 15M cycle remains silent unless an actual ENTRY NOW fires, avoiding duplicate no-signal spam.
- Fast Radar: every 60 seconds, max 2 focused symbols/cycle. New impulse -> 15M production path; near/news candidate -> 1H production path.
- ENTRY NOW monitor: every 30 seconds. A confirmed entry is delivered immediately; it does not wait for the next 10-minute full scan.
- Material Entry/Stop/TP geometry changes reset READY confirmation to 0/2.
- Prime label lock prevents several near-simultaneous ENTRY NOW alerts from all being displayed as #1.

GLOBAL / CRYPTO NEWS INTELLIGENCE
- Existing crypto/regulatory sources are preserved.
- Added independent macro/world branch: BLS, ECB press/statistics, CFTC press/enforcement, BBC World and BBC Business feeds.
- Categories include rates, inflation, jobs, banking/liquidity, sanctions/tariffs, geopolitics, energy shock, stock-market stress, ETF/regulation, exchange incidents, stablecoins and hacks/exploits.
- A headline NEVER creates a trade on its own. It can trigger an alert + immediate market re-check. Final trade still requires the complete Production setup, HTF, derivatives, execution, L2, taker-flow and 2/2 ENTRY NOW gates.
- Ambiguous shock headlines are fail-closed for the first 3 minutes of price discovery; after that the bot stops guessing direction and lets market data decide.
- X/social posts are never treated as official primary evidence merely because the account is official; independent corroboration is required for trading use.
- Corroboration must refer to the same event, not just the same broad category.
- New sources have an onboarding guard so deployment does not spam older headlines.

SAFETY / DATA
- Futures L2 is sequence-synchronised and fail-closed on gaps.
- PUBLIC book/depth and MARKET aggTrade WebSocket branches are independently monitored/self-healed.
- Freshness checks treat age=0 as maximally fresh (not missing).
- Flow persistence, reversal, spread shock, liquidity-pull and cross-feed coherence remain veto gates.
- Durable signal/news delivery outboxes and singleton process lease remain enabled.
- Market tape/replay and Black Box remain enabled for post-trade diagnosis.

TEST MATRIX
- Locally runnable focused tests: 112/112 PASS with ResourceWarning promoted to error.
  V11.11: 18 | V11.12: 21 | V11.12.1: 4 | V11.12.2: 16 | V11.13: 53.
- App-dependent V11.10 core: 16 methods; runs on Railway after pinned app/ preflight.
- Inherited production regression: 214 methods; runs on Railway after the core/release gates.
- Full startup chain: 342 test methods.
- Static audit: 85 Python files, AST/compile clean, no duplicate top-level definitions, dependency lock covered, no literal Telegram callback_data above 64 bytes.

IMPORTANT
No trading system can guarantee profitable signals. Treat the first live period as validation: confirm Railway passes the full gate, use conservative exposure, and judge the system by net-R, drawdown, execution quality and replay evidence rather than one trade.
