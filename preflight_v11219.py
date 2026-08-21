from pathlib import Path
import ast
req=["bot_v11219.py","v11219_trade_engine_audit.py","bot_v11218.py","v11218_spot_entry_fix.py","bot_v11217.py","v11217_reliability.py","bot_v11191.py","bot_v11180.py","v11191_spot_engine.py","spot_watch.py","railway.toml"]
miss=[x for x in req if not Path(x).is_file()]
if miss: raise SystemExit("V11.21.9 PREFLIGHT missing: "+", ".join(miss))
for x in ("bot_v11219.py","v11219_trade_engine_audit.py"): ast.parse(Path(x).read_text(),filename=x)
o=Path("v11219_trade_engine_audit.py").read_text(); r=Path("railway.toml").read_text()
checks=["buy_ready_threshold_alignment","prioritized_spot_rows","spot_orderbook_symbols_v11219","spot_recheck_watch","record_spot_ready","price invalidated before BUY","spot_active_correlation_risk","_deliver_spot_pending","3.2","900","700"]
bad=[x for x in checks if x not in o]
if bad or not any(x in r for x in ("python bot_v11219.py","python bot_v11220.py","python bot_v11221.py","python bot_v11222.py","python bot_v11223.py","python bot_v11224.py","python bot_v11225.py","python bot_v11226.py")) or "test_v11219_contract.py" not in r: raise SystemExit("V11.21.9 PREFLIGHT contracts failed")
print("V11.21.9 PREFLIGHT: OK")
