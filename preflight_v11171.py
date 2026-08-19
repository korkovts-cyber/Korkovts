"""Pure-stdlib preflight for V11.17.1 Execution & Risk Hardening."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={
 'bot_v11171.py','v11170_snapshot.py','v11170_execution.py','v11170_risk.py',
 'v11170_validation.py','v11170_challenger.py','v11170_attribution.py',
 'test_v11171_core.py','release_check_v11171.py','preflight_v11170.py','railway.toml','v11_ui.py'}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.17.1 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11170.py'),run_name='__v11170_preflight__')
src=(ROOT/'bot_v11171.py').read_text()
for token in ('APP_VERSION="11.17.1"','await asyncio.to_thread(challenger_report_text)','record_v11170_challenger','assess_final_risk(','bot_v11171.py'):
    if token not in src and token!='bot_v11171.py': raise SystemExit('V11.17.1 PREFLIGHT FAILED: '+token)
print('V11.17.1 PREFLIGHT: OK')
