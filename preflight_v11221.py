from pathlib import Path
import ast
req=["bot_v11221.py","v11221_production_reachability.py","bot_v11220.py","v11220_deep_audit.py","bot_v11219.py","v11219_trade_engine_audit.py","bot_v11218.py","v11218_spot_entry_fix.py","bot_v11217.py","v11217_reliability.py","bot_v11191.py","v11191_futures_engine.py","v1141_governor.py","v11200_data_architecture.py","v11196_api_resilience.py","railway.toml"]
miss=[x for x in req if not Path(x).is_file()]
if miss: raise SystemExit("V11.22.1 PREFLIGHT missing: "+", ".join(miss))
for x in ("bot_v11221.py","v11221_production_reachability.py"): ast.parse(Path(x).read_text(),filename=x)
o=Path("v11221_production_reachability.py").read_text(); r=Path("railway.toml").read_text()
checks=["governor.market._get=governed_get_v11221","_reserve_weight","WEIGHT_BUDGET_PER_MIN=780","_wait_for_shared_cooldown","Semaphore(2)","strategy_issue_counts","actual_adl=='high'","recovered.adl_risk='unknown'","_near_original_zone_v11221"]
bad=[x for x in checks if x not in o]
if bad or not any(x in r for x in ("python bot_v11221.py","python bot_v11222.py","python bot_v11223.py","python bot_v11224.py","python bot_v11225.py","python bot_v11226.py")) or "test_v11221_contract.py" not in r: raise SystemExit("V11.22.1 PREFLIGHT contracts failed")
print("V11.22.1 PREFLIGHT: OK")
