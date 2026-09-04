from pathlib import Path
import ast

req = [
    "bot_v11231.py", "v11231_compact_output.py",
    "v11230_signal_core.py", "v11229_pipeline_repair.py",
    "v11228_modern_ui.py", "v11227_stability_core.py",
    "v11226_stable_deep_engine.py", "railway.toml"
]
missing = [x for x in req if not Path(x).is_file()]
if missing:
    raise SystemExit("V11.23.1 PREFLIGHT missing: " + ", ".join(missing))

for x in (
    "bot_v11231.py", "v11231_compact_output.py",
    "v11230_signal_core.py", "v11229_pipeline_repair.py"
):
    ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

ui = Path("v11231_compact_output.py").read_text(encoding="utf-8")
for token in (
    "СЕЙЧАС НЕ ВХОДИМ", "Почему:", "futures_card_compact",
    "spot_card_compact", "heartbeat_compact"
):
    if token not in ui:
        raise SystemExit("V11.23.1 PREFLIGHT compact UI missing: " + token)

rail = Path("railway.toml").read_text(encoding="utf-8")
if "python bot_v11231.py" not in rail:
    raise SystemExit("V11.23.1 PREFLIGHT railway entrypoint missing")

print("V11.23.1 PREFLIGHT: OK")
