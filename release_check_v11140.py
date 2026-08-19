"""V11.14 source-contract checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 'v11140_performance.py':['class PerfWindow','def report_text','def annotate_decision_margin','CLEAR_PRIME','NEAR_TIE','status=\'CLOSED\'','activated_at IS NOT NULL'],
 'bot_v11140.py':['APP_VERSION="11.14.0"','annotate_decision_margin(chosen,protection_valid)','v1114:performance','v1114:early','DECISION MARGIN'],
 'v11_ui.py':['LIVE PERFORMANCE','v1114:performance','EARLY WATCH','v1114:early'],
 'test_v11140_core.py':['test_near_tie_does_not_reorder','test_clear_prime','test_filters_non_production_rows','test_drawdown_and_pf','test_report_empty_is_safe'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append('missing '+name); continue
    src=p.read_text(encoding='utf-8')
    for t in tokens:
        if t not in src: issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text(encoding='utf-8')
for t in ('preflight_v11140.py','test_v11140_core.py','release_check_v11140.py','bot_v11140.py'):
    if t not in rail: issues.append('railway missing '+t)
if issues: raise SystemExit('V11.14 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.14 RELEASE CHECK: OK')
print('Live Performance + Decision Margin + Early Watch contracts: OK')
