from pathlib import Path
import ast

req = [
    "bot_v11236.py", "v11236_signal_activation.py", "bot_v11235.py",
    "v11235_signal_delivery_ui.py", "v11230_signal_core.py", "railway.toml",
]
missing = [x for x in req if not Path(x).is_file()]
if missing:
    raise SystemExit("V11.23.6 PREFLIGHT missing: " + ", ".join(missing))
for x in req:
    if x.endswith(".py"):
        ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

activation = Path("v11236_signal_activation.py").read_text(encoding="utf-8")
for token in ["scan_v11236", "scan_short_v11236", "_finalize_rows", "strong_auto_eligible", "base.core.scan"]:
    if token not in activation:
        raise SystemExit("V11.23.6 PREFLIGHT activation missing: " + token)

core = Path("v11230_signal_core.py").read_text(encoding="utf-8")
checks = [
    "taker >= 1.01", "taker <= 0.99", "-0.25 <= dist <= 1.85",
    "spread <= 7.0", "family direction gap {fam_score-opposite:.1f} < 6",
]
for token in checks:
    if token not in core:
        raise SystemExit("V11.23.6 PREFLIGHT family profile missing: " + token)

rail = Path("railway.toml").read_text(encoding="utf-8")
if "python bot_v11236.py" not in rail:
    raise SystemExit("V11.23.6 PREFLIGHT railway entrypoint missing")
print("V11.23.6 PREFLIGHT: OK")
