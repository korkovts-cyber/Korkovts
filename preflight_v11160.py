"""Pure-stdlib preflight for V11.16 Adaptive Edge + Full Replay."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={'bot_v11160.py','v11160_adaptive.py','v11160_replay.py','test_v11160_core.py','release_check_v11160.py','preflight_v11151.py','railway.toml','v11_ui.py'}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.16 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11151.py'),run_name='__v11151_preflight__')
src=(ROOT/'bot_v11160.py').read_text()
for token in ('APP_VERSION="11.16.0"','ADAPTIVE_EDGE_REJECT','record_v11160_replay','v1116:adaptive','v1116:replay'):
    if token not in src: raise SystemExit('V11.16 PREFLIGHT FAILED: '+token)
print('V11.16 PREFLIGHT: OK')
