Korkovts V11.18.0 · FINAL NUANCE FIX

This package supersedes the previous Spot final-send and compatibility patches.

Included fixes:
1. Spot final-send ordering:
   L2/flow -> EXTREME crowding terminal veto -> terminal news -> DEGRADED crowding retry.
2. Strong Consensus compatibility + runtime NameError fix.
3. Legacy exchange-time release-check compatibility.
4. Futures safety migration no longer clears a real CIRCUIT_PAUSE or recovery baselines on redeploy.
5. Effective Futures live cap is 2 without breaking the legacy V11.7 MAX_CONCURRENT_LIVE=1 contract.
6. Final Telegram delivery now respects the effective cap: one existing live Futures idea allows a second;
   a third is blocked. Removed stale 'other_live_count > 0' behavior from all runtime compatibility files.
7. V11.18 regression coverage extended for migration and delivery-cap consistency.

Verification:
- compileall PASS
- test_v11130_core.py + test_v11150_core.py + test_v11180_core.py: 86/86 PASS
- release_check_v11130.py: PASS
- release_check_v11150.py: PASS
- release_check_v11180.py: PASS
- explicit SQLite migration probe preserved active CIRCUIT_PAUSE and baseline ids across init/redeploy

Upload all .py files to GitHub repository ROOT / main and replace existing files.
