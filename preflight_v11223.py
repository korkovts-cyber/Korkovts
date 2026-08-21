from pathlib import Path
import ast

req=[
    "bot_v11223.py","v11223_end_to_end_repair.py",
    "bot_v11222.py","v11222_verification_hardening.py",
    "bot_v11221.py","v11221_production_reachability.py",
    "bot_v11220.py","v11220_deep_audit.py",
    "bot_v11219.py","v11219_trade_engine_audit.py",
    "bot_v11218.py","v11218_spot_entry_fix.py",
    "bot_v11217.py","v11217_reliability.py",
    "railway.toml"
]

miss=[x for x in req if not Path(x).is_file()]
if miss:
    raise SystemExit("V11.22.3 PREFLIGHT missing: "+", ".join(miss))

for x in ("bot_v11223.py","v11223_end_to_end_repair.py"):
    ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

o=Path("v11223_end_to_end_repair.py").read_text(encoding="utf-8")
r=Path("railway.toml").read_text(encoding="utf-8")

checks=[
    "data_arch.REQUESTS_PER_SEC=4.0",
    "FULL_SCAN_BUDGET_SEC",
    "MIN_FRAME_COVERAGE",
    "SPOT_DEEP_SHORTLIST=24",
    "SPOT_WEIGHT_BUDGET=900",
    "timeout=210.0",
    "FUTURES SCAN НЕ ЗАВЕРШЁН",
    "SPOT SCAN НЕ ЗАВЕРШЁН",
]
bad=[x for x in checks if x not in o]

valid_entrypoints = (
    "python bot_v11223.py",
    "python bot_v11224.py",
)

if bad or not any(x in r for x in valid_entrypoints) or "test_v11223_contract.py" not in r:
    raise SystemExit("V11.22.3 PREFLIGHT contracts failed")

print("V11.22.3 PREFLIGHT: OK")
