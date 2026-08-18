"""Pure-stdlib preflight for V11.12.2 AUTO PULSE."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={'bot_v11122.py','v11122_auto.py','test_v11122_core.py','release_check_v11122.py','preflight_v11121.py','railway.toml','requirements.txt'}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.12.2 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11121.py'),run_name='__v11121_preflight__')
src=(ROOT/'bot_v11122.py').read_text(encoding='utf-8')
for token in ('APP_VERSION="11.12.2"','core.AUTO_SCAN_INTERVAL_MIN=AUTO_FULL_SCAN_MIN','fast_radar_job','_send_auto_heartbeat'):
    if token not in src: raise SystemExit('V11.12.2 PREFLIGHT FAILED: '+token)
print('V11.12.2 PREFLIGHT: OK')
