"""V11.15.1 source-contract checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 'v11151_indicators.py':['class IndicatorEdge','def evaluate','def annotate','_anchored_vwap','_volume_profile_poc','_cvd10','_rvol','_oi_matrix','_sweep','anti_duplication'],
 'bot_v11151.py':['APP_VERSION="11.15.1"','indicator_checked=await _annotate_indicator_edge_many(protection_valid)','if not strong.auto_eligible or not _indicator_gate(signal):','_indicator_gate(fresh,prime=True)','await _annotate_indicator_edge_one(result)'],
 'test_v11151_core.py':['test_long_alignment_quality_first_pass','test_opposite_live_flow_blocks','test_adverse_sweep_blocks','test_runtime_quality_first_gate_is_before_entry'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append('missing '+name); continue
    src=p.read_text(encoding='utf-8')
    for t in tokens:
        if t not in src: issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text(encoding='utf-8')
for t in ('preflight_v11151.py','test_v11151_core.py','release_check_v11151.py','bot_v11151.py'):
    if t not in rail: issues.append('railway missing '+t)
if issues: raise SystemExit('V11.15.1 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.15.1 RELEASE CHECK: OK')
print('Indicator Edge quality-first contracts: OK')
