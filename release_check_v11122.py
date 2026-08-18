"""V11.12.2 AUTO PULSE source-contract checks."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 'v11122_auto.py':['AUTO_FULL_SCAN_MIN = 10','FAST_RADAR_INTERVAL_SEC = 60','choose_radar_symbols','СИГНАЛОВ ДЛЯ ВХОДА НЕТ'],
 'bot_v11122.py':['APP_VERSION="11.12.2"','run_automatic_scan_v11122','fast_radar_job','entry_now_monitor_job,interval=30','core.AUTO_SCAN_INTERVAL_MIN=AUTO_FULL_SCAN_MIN'],
 'test_v11122_core.py':['heartbeat_explicitly_says_no_signal','fast_radar_every_60s'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append('missing '+name); continue
    s=p.read_text(encoding='utf-8')
    for t in tokens:
        if t not in s: issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text(encoding='utf-8')
for t in ('test_v11122_core.py','release_check_v11122.py'):
    if t not in rail: issues.append('railway missing '+t)
if not any(x in rail for x in ('preflight_v11122.py','preflight_v11130.py')): issues.append('railway missing V11.12.2+ preflight')
if not any(x in rail for x in ('bot_v11122.py','bot_v11130.py')): issues.append('railway missing V11.12.2+ entrypoint')
if issues: raise SystemExit('V11.12.2 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.12.2 RELEASE CHECK: OK')
