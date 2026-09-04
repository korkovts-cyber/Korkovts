from pathlib import Path
import ast

req = [
    "bot_v11232.py", "v11232_integrated_freshness.py",
    "v11231_compact_output.py", "v11230_signal_core.py",
    "v11229_pipeline_repair.py", "v11228_modern_ui.py",
    "railway.toml",
]
missing=[x for x in req if not Path(x).is_file()]
if missing:
    raise SystemExit("V11.23.2 PREFLIGHT missing: " + ", ".join(missing))

for x in (
    "bot_v11232.py",
    "v11232_integrated_freshness.py",
    "v11230_signal_core.py",
):
    ast.parse(Path(x).read_text(encoding="utf-8"), filename=x)

p=Path("v11232_integrated_freshness.py").read_text(encoding="utf-8")
checks=[
    "_ACTIVE_ANALYZE = futures.legacy.analyze",
    "acquire_ms > legacy_limit",
    "acquire_ms <= safe_limit",
    "and complete",
    "and quality >= 8",
    "and adl_fresh",
    '"1H": 30000.0',
    '"15M": 22000.0',
    "false_stale_repaired",
]
bad=[x for x in checks if x not in p]
if bad:
    raise SystemExit("V11.23.2 PREFLIGHT contracts: " + ", ".join(bad))

e=Path("bot_v11232.py").read_text(encoding="utf-8")
if e.index("i230()") >= e.index("i232()"):
    raise SystemExit("V11.23.2 PREFLIGHT install order invalid")

r=Path("railway.toml").read_text(encoding="utf-8")
if "python bot_v11232.py" not in r:
    raise SystemExit("V11.23.2 PREFLIGHT railway entrypoint missing")

print("V11.23.2 PREFLIGHT: OK")
