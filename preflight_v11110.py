"""Pure-stdlib preflight for Korkovts V11.11."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={
 "bot_v11110.py","v11110_futures_orderbook.py","v11110_contract.py","v11110_tape.py","v11110_entry_now.py",
 "v11110_replay.py","v11110_lease.py","test_v11110_core.py","preflight_v11100.py",
 "release_check_v11100.py","test_v11100.py","railway.toml","requirements.txt",
}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit("V11.11 PREFLIGHT FAILED: missing: "+", ".join(missing))
runpy.run_path(str(ROOT/"preflight_v11100.py"),run_name="__v11100_preflight__")
src=(ROOT/"bot_v11110.py").read_text(encoding="utf-8")
for token in ('APP_VERSION="11.11.0"','futures_l2_monitor','acquire_v11110_lease','init_v11110_tape'):
    if token not in src: raise SystemExit("V11.11 PREFLIGHT FAILED: runtime contract missing "+token)
entry=(ROOT/"v11110_entry_now.py").read_text(encoding="utf-8")
if "evaluate_entry_contract" not in entry or 'state="WAIT"' not in entry:
    raise SystemExit("V11.11 PREFLIGHT FAILED: fail-closed L2 entry gate missing")

live=(ROOT/"v11_live.py").read_text(encoding="utf-8")
for token in ("/public/stream?streams=","/market/stream?streams=","@bookTicker","@aggTrade"):
    if token not in live: raise SystemExit("V11.11 PREFLIGHT FAILED: routed Futures WS contract missing "+token)
if "wss://fstream.binance.com/stream?streams=" in live:
    raise SystemExit("V11.11 PREFLIGHT FAILED: legacy unrouted Futures WS URL returned")

print("V11.11 PREFLIGHT: OK")
