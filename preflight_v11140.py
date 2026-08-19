"""Pure-stdlib preflight for V11.14 LIVE PERFORMANCE & PRECISION."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={
    'bot_v11140.py','v11140_performance.py','test_v11140_core.py','release_check_v11140.py',
    'preflight_v11130.py','railway.toml','requirements.txt','v11_ui.py',
}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.14 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11130.py'),run_name='__v11130_preflight__')
src=(ROOT/'bot_v11140.py').read_text(encoding='utf-8')
for token in (
    'APP_VERSION="11.14.0"','annotate_decision_margin','performance_report_text',
    'v1114:performance','v1114:early','early_watch_text','core.post_init=post_init_v112',
):
    if token not in src: raise SystemExit('V11.14 PREFLIGHT FAILED: '+token)
ui=(ROOT/'v11_ui.py').read_text(encoding='utf-8')
for token in ('LIVE PERFORMANCE','v1114:performance','EARLY WATCH','v1114:early'):
    if token not in ui: raise SystemExit('V11.14 PREFLIGHT UI FAILED: '+token)
print('V11.14 PREFLIGHT: OK')
