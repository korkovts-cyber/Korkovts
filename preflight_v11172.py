from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={'bot_v11172.py','v11160_adaptive.py','v11100_data.py','v11170_snapshot.py','v11170_execution.py','test_v11172_core.py','release_check_v11172.py','preflight_v11171.py','railway.toml'}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.17.2 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11171.py'),run_name='__v11171_preflight__')
src=(ROOT/'bot_v11172.py').read_text()
for token in ('APP_VERSION="11.17.2"','pending=list(_ui_background_tasks)','assess_final_risk('):
    if token not in src: raise SystemExit('V11.17.2 PREFLIGHT FAILED: '+token)
print('V11.17.2 PREFLIGHT: OK')
