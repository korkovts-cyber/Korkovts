import py_compile
from pathlib import Path

required = [
    "bot_v11238.py",
    "v11238_activity_cleanup.py",
    "v11230_signal_core.py",
    "bot_v11237.py",
    "v11237_button_reliability.py",
]
for name in required:
    if not Path(name).exists():
        raise SystemExit(f"V11.23.8 PREFLIGHT FAIL: missing {name}")
    if name.endswith(".py"):
        py_compile.compile(name, doraise=True)
print("V11.23.8 PREFLIGHT: OK")
