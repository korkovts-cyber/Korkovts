from pathlib import Path
import ast
required=[
 "bot_v11222.py","v11222_verification_hardening.py",
 "bot_v11221.py","v11221_production_reachability.py",
 "bot_v11220.py","v11220_deep_audit.py",
 "bot_v11219.py","v11219_trade_engine_audit.py",
 "bot_v11218.py","v11218_spot_entry_fix.py",
 "bot_v11217.py","v11217_reliability.py",
 "railway.toml"]
missing=[x for x in required if not Path(x).is_file()]
if missing: raise SystemExit("V11.22.2 PREFLIGHT missing: "+", ".join(missing))
for x in ("bot_v11222.py","v11222_verification_hardening.py"):
 ast.parse(Path(x).read_text(encoding="utf-8"),filename=x)
o=Path("v11222_verification_hardening.py").read_text(encoding="utf-8")
r=Path("railway.toml").read_text(encoding="utf-8")
checks=[
 ("actual binding identity","governor.market._get is p221.governed_get_v11221" in o),
 ("startup fail closed","startup blocked: final Binance governor is not bound" in o),
 ("newline literal fix",'text.replace("\\\\n", "\\n")' in o),
 ("runtime bound/error diagnostic",'state = "BOUND" if _binding_ok() else "ERROR"' in o),
 ("final entrypoint","python bot_v11222.py" in r),
 ("final contract","test_v11222_contract.py" in r),
]
bad=[n for n,ok in checks if not ok]
if bad: raise SystemExit("V11.22.2 PREFLIGHT contracts failed: "+", ".join(bad))
print("V11.22.2 PREFLIGHT: OK")
