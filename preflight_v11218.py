from pathlib import Path
import ast

required = [
    "bot_v11218.py",
    "v11218_spot_entry_fix.py",
    "bot_v11217.py",
    "v11217_reliability.py",
    "bot_v11191.py",
    "bot_v11180.py",
    "spot_watch.py",
    "spot_scanner.py",
    "railway.toml",
]
missing = [p for p in required if not Path(p).is_file()]
if missing:
    raise SystemExit("V11.21.8 PREFLIGHT FAILED missing: " + ", ".join(missing))

for p in ("bot_v11218.py", "v11218_spot_entry_fix.py"):
    ast.parse(Path(p).read_text(encoding="utf-8"), filename=p)

overlay = Path("v11218_spot_entry_fix.py").read_text(encoding="utf-8")
railway = Path("railway.toml").read_text(encoding="utf-8")
watch = Path("spot_watch.py").read_text(encoding="utf-8")

contracts = [
    ("Legacy exact-float reset exists", "spot_watchlist.entry_low<>excluded.entry_low" in watch),
    ("Material continuity helper", "_same_spot_setup" in overlay),
    ("Prior streak restored only for BUY", 'incoming == "BUY"' in overlay and "prior_streak" in overlay),
    ("No synthetic second confirmation", "never increase it here" in overlay),
    ("Fresh BUY still mandatory", '!= "BUY"' in overlay and "spot_recheck_watch" in overlay),
    ("Invalidation still hard", "price invalidated before BUY" in overlay),
    ("Correlation still hard", "spot_active_correlation_risk" in overlay),
    ("Portfolio cap still hard", "active_count >= 2" in overlay),
    ("2/2 still mandatory", "streak < 2" in overlay and "record_spot_ready" in overlay),
    ("Live delivery revalidation retained", "_deliver_spot_pending" in overlay),
    ("Near-zone only permits recheck", "_near_original_zone" in overlay),
    ("Research gate retained", "_v11205_research_gate" in overlay),
    ("Heartbeat blocker diagnostics", "Spot top:" in overlay and "last_reason" in overlay),
    ("V11.21.8+ layered entrypoint", any(x in railway for x in ("python bot_v11218.py","python bot_v11219.py","python bot_v11220.py","python bot_v11221.py","python bot_v11222.py"))),
    ("V11.21.8 contract test executed", "test_v11218_contract.py" in railway),
    ("V11.21.8 files explicitly compiled", "v11218_spot_entry_fix.py" in railway and "bot_v11218.py" in railway),
    ("Final health version sync", "health_text_v11218" in overlay),
]
failed = [name for name, ok in contracts if not ok]
if failed:
    raise SystemExit("V11.21.8 PREFLIGHT FAILED contracts: " + ", ".join(failed))
print("V11.21.8 PREFLIGHT: OK")
