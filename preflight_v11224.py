from pathlib import Path
import ast
req=[
 "bot_v11224.py","v11224_full_deep_repair.py",
 "bot_v11223.py","v11223_end_to_end_repair.py",
 "bot_v11222.py","v11222_verification_hardening.py",
 "bot_v11221.py","v11221_production_reachability.py",
 "bot_v11191.py","v11191_futures_engine.py","railway.toml"]
miss=[x for x in req if not Path(x).is_file()]
if miss: raise SystemExit("V11.22.4 PREFLIGHT missing: "+", ".join(miss))
for x in ("bot_v11224.py","v11224_full_deep_repair.py"):
 ast.parse(Path(x).read_text(),filename=x)
o=Path("v11224_full_deep_repair.py").read_text()
r=Path("railway.toml").read_text()
checks=[
 ("analysis concurrency 4","ANALYSIS_CONCURRENCY=4" in o),
 ("deep concurrency 6","DEEP_CONCURRENCY=6" in o),
 ("420s scan budget","420" in o and "FULL_SCAN_BUDGET_SEC" in o),
 ("30s candidate timeout","timeout=30.0" in o),
 ("deep timeout reason","DEEP_CANDIDATE_TIMEOUT" in o),
 ("final entrypoint",("python bot_v11224.py" in r or ("python bot_v11225.py" in r or "python bot_v11226.py" in r))),
 ("contract","test_v11224_contract.py" in r),
]
bad=[n for n,ok in checks if not ok]
if bad: raise SystemExit("V11.22.4 PREFLIGHT contracts failed: "+", ".join(bad))
print("V11.22.4 PREFLIGHT: OK")
