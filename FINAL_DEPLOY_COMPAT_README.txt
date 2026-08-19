Korkovts V11.18.0 FINAL DEPLOY COMPAT FIX

Upload all four .py files to the ROOT of GitHub main and replace existing files.

Included fixes:
- v11150_strong.py: V11.15 preflight compatibility + runtime NameError fix.
- v1142_risk.py:
  * preserves legacy V11.7 MAX_CONCURRENT_LIVE=1 contract;
  * V11.18 effective live-signal cap is 2;
  * one-time Telegram delivery bootstrap avoids hidden initial CANARY;
  * preserves legacy CANARY/circuit-breaker regression behavior;
  * safely normalizes the transitional 11.18.1-signal-delivery DB value.
- v11_live.py: restores exact legacy exchange-time release-check contract without changing behavior.
- test_v11180_core.py: regression coverage for the compatibility design.

Verification before packaging:
- 210/210 locally runnable core tests PASS (V11.11 through V11.18).
- V11.11 through V11.18 release checks PASS.
- V11.10 source-contract audit: 0 missing contracts.
- DB migration probes: brand-new LIVE, manual legacy CANARY, transitional migration,
  one-active allows second, two-active blocks third.
- compileall PASS.
- Railway's previous 226 core tests already passed with the pinned app/ base.
