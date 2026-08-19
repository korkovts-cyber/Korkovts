"""V11.17.1 hardening source-contract checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
'v11170_snapshot.py':['SCHEMA="11.17-market-snapshot-v2"','coherence timestamps unobservable','derivatives timing metadata unavailable','clock skew'],
'v11170_execution.py':['SCHEMA="11.17-execution-reality-v2"','MAX_FUTURE_TRIGGER_SKEW_SEC=2.0','$5k executable depth unavailable','execution depth ladder invalid/unsorted'],
'v11170_risk.py':['SCHEMA="11.17-final-risk-gateway-v2"','weakest_component','weakest=min(components)'],
'v11170_validation.py':['timestamp column unavailable','duplicate timestamps','future row detected'],
'v11170_challenger.py':['MAX_ROWS=50000','bounded Railway volume growth'],
'bot_v11171.py':['APP_VERSION="11.17.1"','await asyncio.to_thread(challenger_report_text)','await asyncio.to_thread(attribution_report_text)','record_v11160_replay,signal_id'],
'test_v11171_core.py':['test_missing_coherence_fails_closed','test_future_trigger_timestamp_blocks','test_final_score_reflects_weakest_component','test_replay_persistence_is_off_event_loop'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append('missing '+name); continue
    s=p.read_text()
    for t in tokens:
        if t not in s: issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text()
for t in ('preflight_v11171.py','test_v11171_core.py','release_check_v11171.py','bot_v11171.py'):
    if t not in rail: issues.append('railway missing '+t)
if issues: raise SystemExit('V11.17.1 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.17.1 RELEASE CHECK: OK')
