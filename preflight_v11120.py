"""Pure-stdlib preflight for Korkovts V11.12."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={"bot_v11120.py","v11120_entry_now.py","v11120_contract.py","v11120_replay.py","test_v11120_core.py","preflight_v11110.py","release_check_v11110.py","railway.toml","requirements.txt"}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit("V11.12 PREFLIGHT FAILED: missing: "+", ".join(missing))
runpy.run_path(str(ROOT/"preflight_v11110.py"),run_name="__v11110_preflight__")
src=(ROOT/"bot_v11120.py").read_text(encoding="utf-8")
for token in ('APP_VERSION="11.12.0"','from v11120_entry_now import (','SELF-HEALING EDGE'):
    if token not in src: raise SystemExit("V11.12 PREFLIGHT FAILED: runtime contract missing "+token)
contract=(ROOT/"v11120_contract.py").read_text(encoding="utf-8")
for token in ('Futures bookTicker unavailable','active_seconds','max_bucket_share','liquidity pulled','non-finite live market data'):
    if token not in contract: raise SystemExit("V11.12 PREFLIGHT FAILED: live contract missing "+token)
live=(ROOT/"v11_live.py").read_text(encoding="utf-8")
for token in ('_ROUTE_STALL_SEC','application data stalled','active_seconds','max_bucket_share'):
    if token not in live: raise SystemExit("V11.12 PREFLIGHT FAILED: live self-heal contract missing "+token)
print("V11.12 PREFLIGHT: OK")
