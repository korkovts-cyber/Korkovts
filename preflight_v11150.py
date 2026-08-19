"""Pure-stdlib preflight for V11.15 STRONG SIGNALS."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={
    'bot_v11150.py','v11150_strong.py','test_v11150_core.py','release_check_v11150.py',
    'preflight_v11140.py','railway.toml','requirements.txt','v11_ui.py',
}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.15 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11140.py'),run_name='__v11140_preflight__')
src=(ROOT/'bot_v11150.py').read_text(encoding='utf-8')
for token in (
    'APP_VERSION="11.15.0"','assess_strong_signal','annotate_strong_signals',
    'if not strong.auto_eligible:','strong.prime_eligible' ,
):
    if token not in src: raise SystemExit('V11.15 PREFLIGHT FAILED: '+token)
strong=(ROOT/'v11150_strong.py').read_text(encoding='utf-8')
for token in ('PRIME_STRONG','QUALIFIED_ONLY','rank>=84.0','support>=5','negative_only'):
    if token not in strong: raise SystemExit('V11.15 STRONG PREFLIGHT FAILED: '+token)
print('V11.15 PREFLIGHT: OK')
