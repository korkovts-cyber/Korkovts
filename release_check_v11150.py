"""V11.15 source-contract checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 'v11150_strong.py':['class StrongAssessment','def assess','def annotate_many','PRIME_STRONG','STRONG','QUALIFIED_ONLY','professional_rank_changed'],
 'bot_v11150.py':['APP_VERSION="11.15.0"','annotate_strong_signals(chosen)','strong=assess_strong_signal(signal)','if not strong.auto_eligible:','strong.prime_eligible'],
 'v11_ui.py':['YK PRIME STRONG','Strong Consensus','strong_prime'],
 'test_v11150_core.py':['test_clear_consensus_becomes_prime_strong','test_hard_evidence_conflict_blocks','test_near_tie_can_be_strong_but_not_prime','test_runtime_applies_strong_gate_before_arm_and_entry'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append('missing '+name); continue
    src=p.read_text(encoding='utf-8')
    for t in tokens:
        if t not in src: issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text(encoding='utf-8')
for t in ('preflight_v11150.py','test_v11150_core.py','release_check_v11150.py','bot_v11150.py'):
    if t not in rail: issues.append('railway missing '+t)
if issues: raise SystemExit('V11.15 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.15 RELEASE CHECK: OK')
print('Strong Consensus + PRIME STRONG contracts: OK')
