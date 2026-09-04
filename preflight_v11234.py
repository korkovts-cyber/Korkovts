from pathlib import Path
import ast

req=[
 "bot_v11234.py","v11234_unified_final_gate.py","v11233_geometry_repair.py",
 "v11232_integrated_freshness.py","v11231_compact_output.py",
 "v11230_signal_core.py","v11229_pipeline_repair.py","railway.toml"
]
missing=[x for x in req if not Path(x).is_file()]
if missing:
    raise SystemExit("V11.23.4 PREFLIGHT missing: "+", ".join(missing))

for x in req[:-1]:
    if x.endswith(".py"):
        ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

p=Path("v11234_unified_final_gate.py").read_text(encoding="utf-8")
checks=[
 'aligned == 5',
 'aligned >= 4',
 'not location',
 'legacy_score_diagnostic_only',
 'signal.strong_prime_eligible = bool(prime)',
 'signal.strong_auto_eligible = bool(strong_entry)',
]
bad=[x for x in checks if x not in p]
if bad:
    raise SystemExit("V11.23.4 PREFLIGHT final gate: "+", ".join(bad))

r=Path("railway.toml").read_text(encoding="utf-8")
if "python bot_v11234.py" not in r:
    raise SystemExit("V11.23.4 PREFLIGHT railway entrypoint missing")

print("V11.23.4 PREFLIGHT: OK")
