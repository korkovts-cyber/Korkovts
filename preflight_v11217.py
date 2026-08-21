from pathlib import Path
import ast

required = [
    "bot_v11217.py",
    "v11217_reliability.py",
    "bot_v11191.py",
    "bot_v11180.py",
    "v11191_futures_engine.py",
    "railway.toml",
]
missing = [p for p in required if not Path(p).is_file()]
if missing:
    raise SystemExit("V11.21.7 PREFLIGHT FAILED missing: " + ", ".join(missing))

for p in ("bot_v11217.py", "v11217_reliability.py"):
    ast.parse(Path(p).read_text(encoding="utf-8"), filename=p)

overlay = Path("v11217_reliability.py").read_text(encoding="utf-8")
railway = Path("railway.toml").read_text(encoding="utf-8")
base = Path("bot_v11180.py").read_text(encoding="utf-8")

contracts = [
    ("Production wrapper exists", "_raw_scan=core.scan" in base and "return await _run_scan_with_watchdog(\"main\",_raw_scan,480)" in base),
    ("Raw hook patched", "base._raw_scan = raw_scan_v11217" in overlay and "base._raw_short = raw_short_v11217" in overlay),
    ("Production not bypassed", "base.core.scan = scan_v11217" not in overlay and "base.core.scan=scan_v11217" not in overlay),
    ("Deep acquisition patched", "futures._deep_one = _deep_one_v11217" in overlay),
    ("Deep serial", "futures.DEEP_CONCURRENCY = 1" in overlay),
    ("Pre-deep budget wait", "_wait_for_deep_window" in overlay and "DEEP_START_WEIGHT_CEILING" in overlay),
    ("Truthful verification", "deep verification coverage incomplete" in overlay and "deep_verified" in overlay),
    ("Spot discovery 15m", "base.SPOT_AUTO_INTERVAL_MIN = 15" in overlay),
    ("Spot watch 1m", "base.SPOT_WATCH_INTERVAL_MIN = 1" in overlay),
    ("Spot watch waits for research slot", "timeout=45.0" in overlay),
    ("Spot bootstrap", "v11217-spot-auto-bootstrap" in overlay),
    ("Layered entrypoint", any(x in railway for x in ("python bot_v11217.py","python bot_v11218.py","python bot_v11219.py","python bot_v11220.py","python bot_v11221.py","python bot_v11222.py"))),
]
failed = [name for name, ok in contracts if not ok]
if failed:
    raise SystemExit("V11.21.7 PREFLIGHT FAILED contracts: " + ", ".join(failed))
print("V11.21.7 PREFLIGHT: OK")
