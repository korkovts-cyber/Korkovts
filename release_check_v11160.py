"""V11.16 source-contract checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
'v11160_adaptive.py':['class AdaptiveDecision','def assess_stats','QUARANTINE','DEGRADED','negative-only'],
'v11160_replay.py':['v11160_entry_replay','def record','def fingerprint','immutable'],
'bot_v11160.py':['APP_VERSION="11.16.0"','ADAPTIVE_EDGE_REJECT','record_v11160_replay(signal_id','not _adaptive_gate(fresh)[0]'],
'test_v11160_core.py':['test_small_sample_never_blocks','test_severe_mature_negative_quarantines','test_replay_before_delivery'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists():issues.append('missing '+name);continue
    s=p.read_text()
    for t in tokens:
        if t not in s:issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text()
for t in ('preflight_v11160.py','test_v11160_core.py','release_check_v11160.py','bot_v11160.py'):
    if t not in rail:issues.append('railway missing '+t)
if issues:raise SystemExit('V11.16 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.16 RELEASE CHECK: OK')
