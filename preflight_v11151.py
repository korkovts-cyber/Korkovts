"""Pure-stdlib preflight for V11.15.1 Indicator Edge Quality-First."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={
    'bot_v11151.py','v11151_indicators.py','test_v11151_core.py','release_check_v11151.py',
    'preflight_v11150.py','railway.toml','requirements.txt','v11_ui.py',
}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.15.1 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11150.py'),run_name='__v11150_preflight__')
src=(ROOT/'bot_v11151.py').read_text(encoding='utf-8')
for token in (
    'APP_VERSION="11.15.1"','_annotate_indicator_edge_many','_indicator_gate(signal)',
    '_indicator_gate(fresh,prime=True)','setup rejected by Indicator Edge',
):
    if token not in src: raise SystemExit('V11.15.1 PREFLIGHT FAILED: '+token)
mod=(ROOT/'v11151_indicators.py').read_text(encoding='utf-8')
for token in ('_anchored_vwap','_volume_profile_poc','_cvd10','_rvol','_oi_matrix','_sweep','negative-only'):
    if token not in mod: raise SystemExit('V11.15.1 INDICATOR PREFLIGHT FAILED: '+token)
print('V11.15.1 PREFLIGHT: OK')
