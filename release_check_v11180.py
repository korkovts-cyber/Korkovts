from pathlib import Path

ROOT=Path(__file__).resolve().parent
issues=[]
checks={
    'v11180_defense.py':[
        'negative_only','market regime SHOCK','multi-family contradiction',
        'runtime health circuit breaker active','pre-entry adverse selection detected',
        'def required_confirmations','PERSISTENCE_CONFIRMATIONS',
    ],
    'v11170_risk.py':['defense=None','Regime/Defense PASS','V11.18 defense failed'],
    'v11110_futures_orderbook.py':[
        'self.history.append((now,spread,b,a,imb,micro))','microprice_drift_bps_2s',
    ],
    'v11170_snapshot.py':[
        'V11180_EVIDENCE_SCHEMA="11.18-market-snapshot-evidence-v1"','microprice_drift_bps_2s',
        'adverse_long_share_5s','adverse_short_share_5s',
    ],
    'bot_v11180.py':[
        'APP_VERSION="11.18.0"','assess_v11180_defense(','defense=defense_decision',
        'except _EntryRevalidationWait as exc:','return "WAIT",fresh,None,None',
        '[:V11_CANDIDATE_POOL]','FINAL RISK GATE · V11.18.0',
        'YK CONTROL CENTER · V11.18.0',
    ],
    'test_v11180_core.py':[
        'test_shock_blocks','test_two_independent_conflicts_block',
        'test_health_circuit_breaker_blocks','test_negative_only_contract',
        'test_regime_confirmation_contract','test_final_gateway_waits_without_destroying_arm',
        'test_transient_health_and_derivatives_keep_arm_alive',
        'test_futures_l2_exports_real_microprice_drift',
        'test_indicator_edge_does_not_silently_drop_second_half_of_pool',
        'test_snapshot_fingerprint_captures_v11180_microstructure',
        'test_user_visible_version_labels_are_current',
        'test_early_watch_does_not_claim_two_of_two_for_protected_regimes',
    ],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists():
        issues.append('missing '+name)
        continue
    s=p.read_text()
    for t in tokens:
        if t not in s:
            issues.append(f'{name}: missing {t}')

bot=(ROOT/'bot_v11180.py').read_text() if (ROOT/'bot_v11180.py').exists() else ''
if bot:
    final_start=bot.find('if not final_gate.eligible:')
    final_end=bot.find('priority_label=',final_start)
    final_block=bot[final_start:final_end] if final_start>=0 and final_end>final_start else ''
    if 'cancel_entry_arm(arm_id,reason)' in final_block:
        issues.append('bot_v11180.py: final-risk temporary veto destroys ARMED setup')
    indicator_start=bot.find('async def _annotate_indicator_edge_many')
    indicator_end=bot.find('def _indicator_gate',indicator_start)
    indicator_block=bot[indicator_start:indicator_end] if indicator_start>=0 and indicator_end>indicator_start else ''
    if '[:10]' in indicator_block:
        issues.append('bot_v11180.py: Indicator Edge silently truncates qualified pool to 10')

rail=(ROOT/'railway.toml').read_text()
for t in ('preflight_v11180.py','test_v11180_core.py','release_check_v11180.py','bot_v11180.py'):
    if t not in rail:
        issues.append('railway missing '+t)

if issues:
    raise SystemExit('V11.18 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.18 RELEASE CHECK: OK')
print('post-audit persistence + transient WAIT + microprice + candidate-pool + snapshot contracts: OK')
