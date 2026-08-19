"""Pure-stdlib preflight for V11.17 Execution & Risk Core."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={
 'bot_v11170.py','v11170_snapshot.py','v11170_execution.py','v11170_risk.py',
 'v11170_validation.py','v11170_challenger.py','v11170_attribution.py',
 'test_v11170_core.py','release_check_v11170.py','preflight_v11160.py','railway.toml','v11_ui.py'}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.17 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11160.py'),run_name='__v11160_preflight__')
src=(ROOT/'bot_v11170.py').read_text()
for token in ('APP_VERSION="11.17.0"','final_gate=assess_final_risk(','assess_execution_reality','assess_market_snapshot','v1117:risk','v1117:challenger','v1117:attribution'):
    if token not in src: raise SystemExit('V11.17 PREFLIGHT FAILED: '+token)
print('V11.17 PREFLIGHT: OK')
