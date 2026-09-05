from pathlib import Path
import ast

required = [
    "bot_v11237.py",
    "v11237_button_reliability.py",
    "bot_v11236.py",
    "v11236_signal_activation.py",
]
for name in required:
    p = Path(__file__).with_name(name)
    if not p.exists():
        raise SystemExit(f"V11.23.7 PREFLIGHT FAIL: missing {name}")
    ast.parse(p.read_text(encoding="utf-8"), filename=name)

src = Path(__file__).with_name("v11237_button_reliability.py").read_text(encoding="utf-8")
for needle in [
    '"scan": core.scan_cmd',
    '"short_scan": core.short_scan_cmd',
    '"alerts_on": core.alerts_on',
    '"alerts_off": core.alerts_off',
    'data == "v11:menu"',
    'core.callback = callback_v11237',
]:
    if needle not in src:
        raise SystemExit(f"V11.23.7 PREFLIGHT FAIL: missing contract {needle}")
print("V11.23.7 PREFLIGHT: OK")

rail = Path(__file__).with_name("railway.toml").read_text(encoding="utf-8")
if "python bot_v11237.py" not in rail:
    raise SystemExit("V11.23.7 PREFLIGHT FAIL: railway entrypoint is not V11.23.7")
