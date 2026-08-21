from pathlib import Path
import ast
req=[
 "bot_v11226.py","v11226_stable_deep_engine.py",
 "bot_v11225.py","v11225_pipeline_repair.py",
 "bot_v11224.py","v11224_full_deep_repair.py",
 "bot_v11223.py","v11223_end_to_end_repair.py",
 "bot_v11222.py","v11222_verification_hardening.py",
 "bot_v11221.py","v11221_production_reachability.py",
 "app/market.py","v11191_futures_engine.py","v11191_spot_engine.py","railway.toml"]
miss=[x for x in req if not Path(x).is_file()]
if miss: raise SystemExit("V11.22.6 PREFLIGHT missing: "+", ".join(miss))
for x in ("bot_v11226.py","v11226_stable_deep_engine.py"):
 ast.parse(Path(x).read_text(encoding="utf-8"),filename=x)
o=Path("v11226_stable_deep_engine.py").read_text(encoding="utf-8")
r=Path("railway.toml").read_text(encoding="utf-8")
checks=[
 ("queue-safe timeout","async with sem:" in o and "timeout=65.0" in o and "_noop_sem" in o),
 ("deep concurrency 3","DEEP_CONCURRENCY = 3" in o),
 ("540 scan budget","540" in o and "FULL_SCAN_BUDGET_SEC" in o),
 ("futures-data limiter","FUTURES_DATA_RPS = 1.6" in o),
 ("snapshot dedupe","_snapshot_inflight" in o and "_snapshot_cache" in o),
 ("timeout verification","DEEP_CANDIDATE_TIMEOUT" in o and "deep_verification_v11226" in o),
 ("counter reset","_reset_deep_stats" in o),
 ("entrypoint","python bot_v11226.py" in r),
 ("contract","test_v11226_contract.py" in r),
]
bad=[n for n,ok in checks if not ok]
if bad: raise SystemExit("V11.22.6 PREFLIGHT failed: "+", ".join(bad))
print("V11.22.6 PREFLIGHT: OK")
