from pathlib import Path
import ast

req = [
    "bot_v11235.py", "v11235_signal_delivery_ui.py", "bot_v11234.py",
    "v11234_unified_final_gate.py", "v11231_compact_output.py", "railway.toml",
]
missing = [x for x in req if not Path(x).is_file()]
if missing:
    raise SystemExit("V11.23.5 PREFLIGHT missing: " + ", ".join(missing))
for x in req:
    if x.endswith(".py"):
        ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)
p = Path("v11235_signal_delivery_ui.py").read_text(encoding="utf-8")
checks = [
    "FUTURES SIGNAL", "SPOT SIGNAL · БЕЗ ПЛЕЧА", "СИГНАЛ НАЙДЕН",
    "legacy_text_reconstruction_repaired", "strong_auto_eligible",
]
bad = [x for x in checks if x not in p]
if bad:
    raise SystemExit("V11.23.5 PREFLIGHT checks: " + ", ".join(bad))
r = Path("railway.toml").read_text(encoding="utf-8")
if "python bot_v11235.py" not in r:
    raise SystemExit("V11.23.5 PREFLIGHT railway entrypoint missing")
print("V11.23.5 PREFLIGHT: OK")
