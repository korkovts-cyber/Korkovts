KORKOVTS SIGNAL AI · V11.18.0
REGIME + PORTFOLIO DEFENSE · POST-AUDIT FIXED

UPLOAD
1. Upload ALL files from this archive to the ROOT of the existing Korkovts GitHub repository.
2. Replace files with the same names.
3. Do NOT delete the existing app/ directory. This release is an overlay and intentionally does not include app/.
4. Railway must run the chained preflights, compileall, 435 tests/release gates, then start bot_v11180.py.
5. Do not treat Railway "Active" alone as proof. In logs confirm the final V11.18 release check passes, the inherited production regression finishes, and bot_v11180.py starts Telegram polling.

WHAT WAS FIXED IN THIS POST-AUDIT BUILD
- Protected regimes can actually reach their required 3/3 ENTRY persistence; they are no longer killed at 2/3.
- Temporary data/health problems keep an ARMED opportunity waiting rather than permanently deleting it, while ENTRY remains impossible until fresh checks pass.
- Futures L2 now calculates real 2-second microprice drift for V11.18 adverse-selection defense.
- Qualified Futures candidates #11-20 are no longer silently lost before Indicator Edge; the bounded pool remains max 20 and publication remains capped later.
- Final snapshot fingerprints now include the V11.18 microstructure inputs used in the decision.
- Telegram version labels are consistent with V11.18.0.

IMPORTANT QUALITY POLICY
- This build does NOT lower the core quality threshold or force a trade every 10 minutes.
- Full AUTO still scans the liquid universe on schedule; the 10-minute message is a heartbeat/status, not a promise of a trade.
- Spot local order-book monitoring remains intentionally capped at 10 highest-priority WATCH symbols because each monitored symbol needs a sequence-synchronised live book and REST snapshot/resync support.
- V11.18 remains negative-only: it can block/demote unsafe entries but cannot manufacture alpha or increase professional rank.

LOCAL AUDIT BOUNDARY
The archive intentionally excludes app/. Therefore the 205 overlay/focused tests can be executed locally from this package, while the 16 app-dependent core tests and 214 inherited production regression tests run in Railway only after the chained preflight verifies the existing repository app/ fingerprints.
