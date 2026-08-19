Korkovts V11.18.0 DEPLOY FIX

Upload v11150_strong.py to the ROOT of the GitHub repository and replace the existing file.

Fixes:
1. Restores V11.15 preflight compatibility token for the normal support>=5 contract.
2. Fixes runtime NameError in Strong Consensus market-context lookup.
3. Keeps V11.18 independent-mode relief: only breadth-divergence independent mode may use 4 families.
4. Normal regimes still require 5 independent evidence families.

Verified:
- test_v11150_core.py + test_v11180_core.py: 31/31 PASS
- release_check_v11150.py: PASS
- release_check_v11180.py: PASS
- v11150_strong.py py_compile: PASS
