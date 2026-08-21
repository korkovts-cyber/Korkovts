from pathlib import Path
import ast

required = [
    "bot_v11220.py","v11220_deep_audit.py",
    "bot_v11219.py","v11219_trade_engine_audit.py",
    "bot_v11218.py","v11218_spot_entry_fix.py",
    "bot_v11217.py","v11217_reliability.py",
    "bot_v11191.py","bot_v11180.py",
    "spot_watch.py","spot_scanner.py","v11191_spot_engine.py","railway.toml",
]
missing=[p for p in required if not Path(p).is_file()]
if missing:
    raise SystemExit("V11.22.0 PREFLIGHT missing: "+", ".join(missing))

for p in ("bot_v11220.py","v11220_deep_audit.py"):
    ast.parse(Path(p).read_text(encoding="utf-8"), filename=p)

o=Path("v11220_deep_audit.py").read_text(encoding="utf-8")
r=Path("railway.toml").read_text(encoding="utf-8")
checks=[
    ("full BUY-ready alignment", 'min(before_score, 78.0)' in o and 'min(before_rp, 70.0)' in o),
    ("broad WATCH preservation", 'incoming == "WATCH"' in o and "confirm_streak=1" in o),
    ("only existing 1/2 preserved", 'int(before.get("confirm_streak") or 0) == 1' in o),
    ("material geometry guard", "_materially_same_geometry" in o and "overlap_ratio" in o),
    ("watchtower reset remains", "reset_spot_ready" in o),
    ("no synthetic 2/2", "Never create or preserve 2/2" in o),
    ("final layered entrypoint", ("python bot_v11220.py" in r) or (("python bot_v11221.py" in r or ("python bot_v11222.py" in r or ("python bot_v11223.py" in r or ("python bot_v11224.py" in r or ("python bot_v11225.py" in r or "python bot_v11226.py" in r))))))),
    ("final contract", "test_v11220_contract.py" in r),
]
bad=[name for name,ok in checks if not ok]
if bad:
    raise SystemExit("V11.22.0 PREFLIGHT contracts failed: "+", ".join(bad))
print("V11.22.0 PREFLIGHT: OK")
