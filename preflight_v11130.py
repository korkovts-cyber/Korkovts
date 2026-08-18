"""Pure-stdlib preflight for V11.13 GLOBAL INTELLIGENCE."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parent
required={
    'bot_v11130.py','v11130_news_intel.py','v11130_geometry.py','test_v11130_core.py',
    'release_check_v11130.py','preflight_v11122.py','railway.toml','requirements.txt',
}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing: raise SystemExit('V11.13 PREFLIGHT FAILED: missing: '+', '.join(missing))
runpy.run_path(str(ROOT/'preflight_v11122.py'),run_name='__v11122_preflight__')
src=(ROOT/'bot_v11130.py').read_text(encoding='utf-8')
for token in ('APP_VERSION="11.13.0"','core.NEWS_POLL_INTERVAL_SEC=60','breaking_news_payload_v11130','schedule_news_triggered_scans_v11130','_fast_analyze_short_symbol','PRIME_LABEL_LOCK_SEC=120','_scan_pause_reason','_recent_auto_triggers'):
    if token not in src: raise SystemExit('V11.13 PREFLIGHT FAILED: '+token)
arm=(ROOT/'v1142_entry_now.py').read_text(encoding='utf-8')
for token in ('material_geometry_change','confirm_streak=0','confirmation reset'):
    if token not in arm: raise SystemExit('V11.13 PREFLIGHT FAILED geometry reset: '+token)
print('V11.13 PREFLIGHT: OK')
