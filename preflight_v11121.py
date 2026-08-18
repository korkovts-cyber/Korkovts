"""Pure-stdlib control preflight for Korkovts V11.12.1."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={"bot_v11121.py","test_v11121_core.py","release_check_v11121.py","preflight_v11120.py","railway.toml","requirements.txt"}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit("V11.12.1 PREFLIGHT FAILED: missing: "+", ".join(missing))
runpy.run_path(str(ROOT/"preflight_v11120.py"),run_name="__v11120_preflight__")
src=(ROOT/"bot_v11121.py").read_text(encoding="utf-8")
for token in ('APP_VERSION="11.12.1"','V11.12.1 CONTROL AUDIT','YK CONTROL CENTER · V11.12.1'):
    if token not in src: raise SystemExit("V11.12.1 PREFLIGHT FAILED: runtime contract missing "+token)
live=(ROOT/"v11_live.py").read_text(encoding="utf-8")
for token in ('timestamp_source','quote_event_ts','recv_time_books'):
    if token not in live: raise SystemExit("V11.12.1 PREFLIGHT FAILED: quote resilience missing "+token)
print("V11.12.1 PREFLIGHT: OK")
