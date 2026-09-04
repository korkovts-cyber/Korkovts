from pathlib import Path
import ast

req = [
    "bot_v11228.py",
    "v11228_modern_ui.py",
    "bot_v11227.py",
    "v11227_stability_core.py",
    "railway.toml",
]
missing = [x for x in req if not Path(x).is_file()]
if missing:
    raise SystemExit("V11.22.8 PREFLIGHT missing: " + ", ".join(missing))

for x in ("bot_v11228.py", "v11228_modern_ui.py", "v11227_stability_core.py"):
    ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

ui = Path("v11228_modern_ui.py").read_text(encoding="utf-8")
core = Path("v11227_stability_core.py").read_text(encoding="utf-8")
rail = Path("railway.toml").read_text(encoding="utf-8")
entry = Path("bot_v11228.py").read_text(encoding="utf-8")

ui_checks = [
    "ui:signals", "ui:more", "ui:analytics", "ui:labs",
    "ui:history", "ui:system", "ui:home",
    "base.core.callback = callback_v11228",
    "base.main_menu = main_menu",
]
bad_ui = [x for x in ui_checks if x not in ui]
if bad_ui:
    raise SystemExit("V11.22.8 PREFLIGHT UI contracts: " + ", ".join(bad_ui))

core_checks = [
    "timeout=90.0", "_scan_context", "0<cooldown<=180",
    "REQUESTS_PER_SEC=3.2", "FUTURES_DATA_RPS=1.0",
    "WEIGHT_BUDGET_PER_MIN=620", "FRAME_STAGE_MAX_SEC",
    "FULL_SCAN_BUDGET_SEC", "_rebind_scheduler_aliases_v11227",
]
bad_core = [x for x in core_checks if x not in core]
if bad_core:
    raise SystemExit("V11.22.8 PREFLIGHT V11.22.7 core contracts: " + ", ".join(bad_core))

order = ["i217()","i218()","i219()","i220()","i221()","i222()","i223()","i224()","i225()","i226()","i227()","i228()"]
for a,b in zip(order, order[1:]):
    if entry.index(a) >= entry.index(b):
        raise SystemExit(f"V11.22.8 PREFLIGHT layer order: {a} >= {b}")

if "python preflight_v11228.py" not in rail:
    raise SystemExit("V11.22.8 PREFLIGHT railway missing current preflight")
if "python bot_v11228.py" not in rail:
    raise SystemExit("V11.22.8 PREFLIGHT railway missing production entrypoint")

print("V11.22.8 PREFLIGHT: OK")
