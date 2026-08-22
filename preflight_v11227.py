from pathlib import Path
import ast
req=["bot_v11227.py","v11227_stability_core.py","bot_v11226.py","v11226_stable_deep_engine.py","bot_v11225.py","v11225_pipeline_repair.py","bot_v11191.py","v11191_futures_engine.py","v1141_governor.py","v112_health.py","railway.toml"]
miss=[x for x in req if not Path(x).is_file()]
if miss: raise SystemExit("V11.22.7 PREFLIGHT missing: "+", ".join(miss))
for x in ("bot_v11227.py","v11227_stability_core.py"):
    ast.parse(Path(x).read_text(encoding="utf-8"),filename=x)
o=Path("v11227_stability_core.py").read_text(encoding="utf-8")
r=Path("railway.toml").read_text(encoding="utf-8")
checks=["timeout=90.0","_scan_context","0<cooldown<=180","REQUESTS_PER_SEC=3.2","FUTURES_DATA_RPS=1.0","WEIGHT_BUDGET_PER_MIN=620","FRAME_STAGE_MAX_SEC","FULL_SCAN_BUDGET_SEC"]
bad=[x for x in checks if x not in o]
if bad or "python bot_v11227.py" not in r or "test_v11227_contract.py" not in r:
    raise SystemExit("V11.22.7 PREFLIGHT failed: "+", ".join(bad))
print("V11.22.7 PREFLIGHT: OK")
