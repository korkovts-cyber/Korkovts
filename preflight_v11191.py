from pathlib import Path
import ast

required=[
    "bot_v11191.py","v11191_futures_engine.py","v11191_spot_engine.py",
    "v11191_integrity.py","v11195_geometry.py","v11190_ui.py","v11191_ui.py",
    "bot_v11180.py","railway.toml",
]
missing=[p for p in required if not Path(p).is_file()]
if missing:
    raise SystemExit("V11.19.5 PREFLIGHT FAILED missing: "+", ".join(missing))

for p in required:
    if p.endswith(".py"):
        ast.parse(Path(p).read_text(encoding="utf-8"),filename=p)

railway=Path("railway.toml").read_text(encoding="utf-8")
if "python bot_v11191.py" not in railway:
    raise SystemExit("V11.19.5 PREFLIGHT FAILED railway entrypoint")

fut=Path("v11191_futures_engine.py").read_text()
spot=Path("v11191_spot_engine.py").read_text()
contracts=[
    ("Futures full-universe rank","full_universe_ranked" in fut),
    ("Futures wide deep","DEEP_SHORTLIST" in fut),
    ("Spot full-universe rank","full_universe_ranked" in spot),
    ("Spot wide deep","SPOT_DEEP_SHORTLIST=36" in spot),
    ("Spot recovery independent","independent_recovery" in spot),
    ("Auxiliary degradation not directional","auxiliary_degraded" in spot),
]
bad=[name for name,ok in contracts if not ok]
if bad:
    raise SystemExit("V11.19.5 PREFLIGHT FAILED contracts: "+", ".join(bad))
print("V11.19.5 FULL-UNIVERSE PREFLIGHT: OK")
