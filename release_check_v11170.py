"""V11.17 source-contract checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
'v11170_snapshot.py':['class SnapshotDecision','sequence-synchronised','snapshot_fingerprint','negative_only'],
'v11170_execution.py':['class ExecutionDecision','trigger_freshness','HARD_TTL_SEC=60.0','$1k impact'],
'v11170_risk.py':['class FinalRiskDecision','Strong Consensus','Indicator Edge','Adaptive Edge'],
'v11170_validation.py':['no_future_rows','recursive_stability','future row detected'],
'v11170_challenger.py':['v11170_challenger','Challenger не может отправлять торговые сигналы'],
'v11170_attribution.py':['ENTRY_TIMING','LIQUIDITY','FLOW_REVERSAL'],
'bot_v11170.py':['APP_VERSION="11.17.0"','final_gate=assess_final_risk(','signal_id=core.save_pending(fresh)','record_v11170_challenger','v1117:risk'],
'test_v11170_core.py':['test_final_gateway_before_save_pending','test_expired_trigger_blocks','test_future_row_detected'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists():issues.append('missing '+name);continue
    s=p.read_text()
    for t in tokens:
        if t not in s:issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text()
for t in ('preflight_v11170.py','test_v11170_core.py','release_check_v11170.py','bot_v11170.py'):
    if t not in rail:issues.append('railway missing '+t)
if issues:raise SystemExit('V11.17 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.17 RELEASE CHECK: OK')
