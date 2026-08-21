from pathlib import Path
import ast
req=["bot_v11225.py","v11225_pipeline_repair.py","bot_v11224.py","v11224_full_deep_repair.py","bot_v11191.py","v11191_futures_engine.py","v11191_spot_engine.py","railway.toml"]
miss=[x for x in req if not Path(x).is_file()]
if miss: raise SystemExit("V11.22.5 PREFLIGHT missing: "+", ".join(miss))
for x in ("bot_v11225.py","v11225_pipeline_repair.py"): ast.parse(Path(x).read_text(),filename=x)
o=Path("v11225_pipeline_repair.py").read_text(); r=Path("railway.toml").read_text()
checks=["base.spot_scan=spot_scan_independent_v11225","DAILY_TIMEOUT_45S","TECHNICAL_RANK_FALLBACK","full-deep remains mandatory","adaptive=20"]
bad=[x for x in checks if x not in o]
if bad or not any(x in r for x in ("python bot_v11225.py","python bot_v11226.py")) or "test_v11225_contract.py" not in r: raise SystemExit("V11.22.5 PREFLIGHT failed")
print("V11.22.5 PREFLIGHT: OK")
