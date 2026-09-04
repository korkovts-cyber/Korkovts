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

for x in ("bot_v11228.py", "v11228_modern_ui.py"):
    ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

ui = Path("v11228_modern_ui.py").read_text(encoding="utf-8")
rail = Path("railway.toml").read_text(encoding="utf-8")
checks = [
    "ui:signals", "ui:more", "ui:analytics", "ui:labs",
    "ui:history", "ui:system", "ui:home",
    "base.core.callback = callback_v11228",
    "base.main_menu = main_menu",
]
bad = [x for x in checks if x not in ui]
if bad:
    raise SystemExit("V11.22.8 PREFLIGHT UI contracts: " + ", ".join(bad))

if "python bot_v11228.py" not in rail:
    raise SystemExit("V11.22.8 PREFLIGHT railway entrypoint missing")

print("V11.22.8 PREFLIGHT: OK")
