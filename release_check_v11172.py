from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 'v11160_adaptive.py':['ORDER BY COALESCE(closed_at,created_at) DESC,id DESC','reversed(rows)','ROW_CACHE_TTL_SEC=30.0','sqlite3.connect(path,timeout=.25)'],
 'v11100_data.py':['observable=all(x.observable for x in frames)','timestamps duplicate/non-monotonic'],
 'v11170_snapshot.py':['derivatives timing metadata unavailable/inconsistent','timing_consistent='],
 'v11170_execution.py':['target*.005','invalid signal side for execution simulation'],
 'bot_v11172.py':['APP_VERSION="11.17.2"','pending=list(_ui_background_tasks)','DEEP AUDIT HARDENING','def _spawn_sync_text_reply'],
 'test_v11172_core.py':['test_adaptive_uses_latest_window_not_oldest','test_snapshot_requires_all_three_timeframes_observable','test_derivative_timing_must_be_complete_and_consistent'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append('missing '+name); continue
    s=p.read_text()
    for t in tokens:
        if t not in s: issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text()
for t in ('preflight_v11172.py','test_v11172_core.py','release_check_v11172.py','bot_v11172.py'):
    if t not in rail: issues.append('railway missing '+t)
if issues: raise SystemExit('V11.17.2 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.17.2 RELEASE CHECK: OK')
