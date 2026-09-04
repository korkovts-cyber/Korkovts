from pathlib import Path
import ast

req=[
 "bot_v11233.py","v11233_geometry_repair.py","v11232_integrated_freshness.py",
 "v11231_compact_output.py","v11230_signal_core.py","v11229_pipeline_repair.py",
 "railway.toml"
]
missing=[x for x in req if not Path(x).is_file()]
if missing:
    raise SystemExit("V11.23.3 PREFLIGHT missing: "+", ".join(missing))

for x in req[:-1]:
    if x.endswith(".py"):
        ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

p=Path("v11233_geometry_repair.py").read_text(encoding="utf-8")
checks=[
 "MIN_RISK_ATR = 0.90",
 "TARGET_RISK_ATR = 1.35",
 "MAX_RISK_ATR = 1.85",
 "candidate = max(structural, atr_stop)",
 "candidate = min(structural, atr_stop)",
 "risk_widened",
 "_ACTIVE_ANALYZE = futures.legacy.analyze",
]
bad=[x for x in checks if x not in p]
if bad:
    raise SystemExit("V11.23.3 PREFLIGHT geometry contracts: "+", ".join(bad))

e=Path("bot_v11233.py").read_text(encoding="utf-8")
if e.index("i232()") >= e.index("i233()"):
    raise SystemExit("V11.23.3 PREFLIGHT install order invalid")

r=Path("railway.toml").read_text(encoding="utf-8")
if "python bot_v11233.py" not in r:
    raise SystemExit("V11.23.3 PREFLIGHT railway entrypoint missing")

print("V11.23.3 PREFLIGHT: OK")
