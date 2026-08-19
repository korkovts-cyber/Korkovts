"""V11.13 GLOBAL INTELLIGENCE source-contract checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 'v11130_news_intel.py':['BLS','ECB','CFTC Enforcement','BBC World','MARKET_STRESS','trade_bias','AMBIGUOUS','По одному заголовку не входить','def for_symbol','official=bool(row.get("official")) and not social','raw_age=row.get("age_minutes")'],
 'v11130_geometry.py':['material_geometry_change','GEOMETRY_ENTRY_R','GEOMETRY_STOP_R'],
 'v1142_entry_now.py':['material_geometry_change','confirm_streak=0','confirmation reset'],
 'bot_v11130.py':['APP_VERSION="11.13.0"','core.NEWS_POLL_INTERVAL_SEC=60','core._breaking_news_payload=breaking_news_payload_v11130','core._run_news_triggered_scans=schedule_news_triggered_scans_v11130','_fast_analyze_short_symbol','PRIME_LABEL_LOCK_SEC=120','triggered_window=len(_recent_auto_triggers(600))','Если входов нет, 10-минутный статус всё равно придёт'],
 'test_v11130_core.py':['directional_breaking_can_be_market_confirmed','ambiguous_breaking_stays_fail_closed','arm_resets_confirmation_on_geometry_change','legacy_x_official_flag_is_demoted_to_social_trust','fast_radar_covers_15m_without_extra_candidate_budget','spot_outbox_news_veto_order_is_stable_in_current_and_compat_runtime','startup_does_not_await_meta_or_db_maintenance_jobs'],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append('missing '+name); continue
    src=p.read_text(encoding='utf-8')
    for t in tokens:
        if t not in src: issues.append(f'{name}: missing {t}')
rail=(ROOT/'railway.toml').read_text(encoding='utf-8')
for t in ('preflight_v11130.py','test_v11130_core.py','release_check_v11130.py','bot_v11130.py'):
    if t not in rail: issues.append('railway missing '+t)
if issues: raise SystemExit('V11.13 RELEASE CHECK FAILED:\n- '+'\n- '.join(issues))
print('V11.13 RELEASE CHECK: OK')
print('AUTO heartbeat + geometry-reset + global shock/news contracts: OK')
