import ast
import unittest
from pathlib import Path

class V112130Contracts(unittest.TestCase):
    def test_futures_full_universe_before_deep(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("full_universe_ranked",s)
        self.assertIn("long_ranked",s)
        self.assertIn("short_ranked",s)
        self.assertIn("_select_deep_rows",s)
        self.assertIn("MIN_OPPOSITE_SIDE_RESERVE",s)

    def test_spot_all_liquid_ranked(self):
        s=Path("v11191_spot_engine.py").read_text()
        self.assertIn("FULL-UNIVERSE discovery",s)
        self.assertIn("ranked.append((pre,symbol,excess))",s)
        self.assertIn("SPOT_DEEP_SHORTLIST=36",s)

    def test_spot_removes_duplicate_blanket_vetoes_only(self):
        s=Path("v11191_spot_engine.py").read_text()
        self.assertIn("independent_recovery",s)
        self.assertIn("auxiliary news telemetry degraded",s)
        self.assertIn("auxiliary futures crowding unavailable",s)
        self.assertIn("recent_negative",s)
        self.assertIn("global_breaking",s)

    def test_clock_is_multisample(self):
        s=Path("v11191_integrity.py").read_text()
        self.assertIn("for _ in range(3)",s)
        self.assertIn("Lowest RTT",s)
        self.assertIn("_last_good_at<=600",s.replace(" ",""))

    def test_launcher_patch_order(self):
        s=Path("bot_v11191.py").read_text()
        self.assertLess(s.index("scanner.scan=futures_scan"),s.index("import bot_v11180 as base"))
        self.assertLess(s.index("v11191_spot_engine.install()"),s.index("import bot_v11180 as base"))


    def test_futures_uses_runtime_patched_production_functions(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("legacy.get_derivatives_snapshot",s)
        self.assertIn("legacy.get_news_sentiment",s)
        self.assertIn("legacy.for_symbol",s)
        self.assertIn("legacy.analyze",s)
        self.assertNotIn("result = analyze(",s)

    def test_calibration_is_side_specific_after_discovery(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn('side = str(getattr(result,"side"',s)
        self.assertIn("calibration_penalty(symbol,side,timeframe)",s)
        self.assertIn('"CALIBRATION_REJECT"',s)

    def test_spot_diagnostics_keep_legacy_ui_contract(self):
        s=Path("v11191_spot_engine.py").read_text()
        self.assertIn('"prefiltered":0',s)
        self.assertIn('_last["prefiltered"]=len(ranked)',s)


    def test_futures_diagnostics_merge_with_production_pipeline(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn('getattr(legacy,"_last_scan"',s)
        self.assertIn('legacy._last_scan[d["kind"]]',s)

    def test_auxiliary_news_zero_does_not_kill_futures_scan(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertNotIn('raise RuntimeError("all news-risk sources are unavailable")',s)
        self.assertIn('d["news_sources"]',s)

    def test_bulk_adl_failure_has_symbol_level_fallback(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn('adl_risks={}',s)
        self.assertIn('adl = adl_risks.get(symbol) if isinstance(adl_risks,dict) else None',s)


    def test_full_universe_leaders_feed_fast_radar(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn('d["near_candidates"]',s)
        self.assertTrue('sorted(ranked,key=lambda r:r[4]' in s or 'sorted(ranked,key=lambda row:row[4]' in s)
        self.assertTrue('sorted(ranked,key=lambda r:r[5]' in s or 'sorted(ranked,key=lambda row:row[5]' in s)

    def test_adaptive_shortlist_does_not_force_fifty_fifty(self):
        tree=ast.parse(Path("v11191_futures_engine.py").read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_select_deep_rows")
        mod=ast.Module(body=[node],type_ignores=[])
        ns={"DEEP_SHORTLIST":36,"MIN_OPPOSITE_SIDE_RESERVE":8}
        exec(compile(mod,"<select>","exec"),ns)
        rows=[]
        # 30 strong LONGs, 10 weaker SHORTs.
        for i in range(30): rows.append((f"L{i}",None,None,None,100-i,10))
        for i in range(10): rows.append((f"S{i}",None,None,None,10,60-i))
        out=ns["_select_deep_rows"](rows,36,8)
        longs=sum(r[4]>=r[5] for r in out)
        shorts=sum(r[4]<r[5] for r in out)
        self.assertEqual(len(out),36)
        self.assertGreater(longs,shorts)
        self.assertGreaterEqual(shorts,8)

    def test_spot_final_delivery_only_softens_auxiliary_degradation(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn('row.get("block")',s)
        self.assertIn('row.get("recent_negative")',s)
        self.assertIn('row.get("global_breaking")',s)
        self.assertIn('not row.get("extreme")',s)
        self.assertIn('row["degraded"]=False',s)

    def test_no_stale_direct_production_imports_remain(self):
        tree=ast.parse(Path("v11191_futures_engine.py").read_text())
        imported=set()
        for node in tree.body:
            if isinstance(node,ast.ImportFrom):
                imported.update(a.name for a in node.names)
        for forbidden in ("analyze","get_derivatives_snapshot","get_news_sentiment","for_symbol","save_shadow","was_shadowed_recently"):
            self.assertNotIn(forbidden,imported)

    def test_all_new_files_parse(self):
        for p in (
            "bot_v11191.py","v11191_futures_engine.py","v11191_spot_engine.py",
            "v11191_integrity.py","v11191_ui.py"
        ):
            ast.parse(Path(p).read_text(),filename=p)

    def test_scan_has_real_deadline_not_post_gather_budget(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("deadline = started + FULL_SCAN_BUDGET_SEC",s)
        self.assertIn("asyncio.wait(frame_tasks",s)
        self.assertIn("frame_pending_cancelled",s)
        self.assertIn("deep_deadline_cancelled",s)
        self.assertNotIn("frame_rows = await asyncio.gather",s)

    def test_auto_lock_wait_matches_full_scan_budget(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_run_automatic_scan_v11194",s)
        self.assertIn("FULL_SCAN_BUDGET_SEC",s)
        self.assertIn("wait_limit",s)
        self.assertIn("base.core._run_automatic_scan=_run_automatic_scan_v11194",s)

    def test_running_scan_publishes_live_progress(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("_last[kind]=copy.deepcopy(d)",s)
        self.assertIn("frame_coverage",s)
        self.assertIn("deep_coverage",s)
        self.assertIn("MIN_FRAME_COVERAGE",s)

    def test_geometry_recovery_is_controlled_pullback(self):
        s=Path("v11195_geometry.py").read_text()
        self.assertIn("КОНТРОЛИРУЕМЫЙ PULLBACK",s)
        self.assertIn("price-.35*atr",s.replace(" ",""))
        self.assertIn("1.35*atr",s)
        self.assertIn("ENTRY NOW",s)

    def test_geometry_installed_before_v1118(self):
        s=Path("bot_v11191.py").read_text()
        self.assertLess(s.index("install_v11195_geometry(scanner,core)"),s.index("import bot_v11180 as base"))

    def test_spot_news_is_auxiliary_at_scan_start(self):
        s=Path("v11191_spot_engine.py").read_text()
        self.assertIn("metas,tickers=await asyncio.gather",s)
        self.assertIn("news_snapshot=await asyncio.wait_for",s)
        self.assertIn("v114_news_degraded",s)

    def test_futures_rejections_keep_strategy_audit(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("_strategy_audit",s)
        self.assertIn("deep_rejections",s)

    def test_api_resilience_reserves_critical_capacity(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn("ANALYSIS_CONCURRENCY",s)
        self.assertIn("_critical",s)
        self.assertIn("BTCUSDT",s)
        self.assertIn("/time",s)

    def test_api_resilience_has_proactive_weight_guard(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn("SOFT_WEIGHT_CEILING",s)
        self.assertIn("_soft_weight_guard",s)
        self.assertIn("60.25",s)

    def test_health_does_not_report_fake_9999_metrics(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn("rate-limit cooldown",s)
        self.assertIn("N/A",s)
        self.assertIn("recent verified state retained",s)

    def test_api_patch_installed_before_v1118(self):
        s=Path("bot_v11191.py").read_text()
        self.assertLess(s.index("install_v11196_api_resilience()"),s.index("import bot_v11180 as base"))

    def test_mandatory_sources_are_split_and_named(self):
        s=Path("v11197_sources.py").read_text()
        self.assertIn("exchangeInfo timeout",s)
        self.assertIn("ticker/24hr timeout",s)
        self.assertIn("mandatory_sources",s)
        self.assertIn("return_exceptions=True",s)

    def test_exchangeinfo_has_long_verified_cache(self):
        s=Path("v11197_sources.py").read_text()
        self.assertIn("EXCHANGEINFO_CACHE_MAX_SEC",s)
        self.assertIn("21600",s)
        self.assertIn('return cached,{',s)

    def test_ticker_cache_is_short_and_bounded(self):
        s=Path("v11197_sources.py").read_text()
        self.assertIn("TICKER_CACHE_MAX_SEC",s)
        self.assertIn("75",s)
        self.assertIn("_live_tickers",s)

    def test_source_endpoints_have_critical_priority(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn('path.endswith("/time")',s)
        self.assertIn('_estimated_weight',s)

    def test_futures_source_error_is_not_generic_timeout(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("mandatory Futures source stage failed",s)
        self.assertIn("source_error",s)
        self.assertIn("source_meta",s)

    def test_two_stage_deep_screens_all_wide_candidates(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("quick_deep_screen(deep_rows,tickers)",s)
        self.assertIn("deep_screen_complete",s)
        self.assertIn("deep_screen_coverage",s)

    def test_fast_screen_is_low_request_weight(self):
        s=Path("v11198_deep_screen.py").read_text()
        self.assertIn("/fapi/v1/premiumIndex",s)
        self.assertIn("/fapi/v1/openInterest",s)
        self.assertNotIn("basis",s)
        self.assertNotIn("globalLongShort",s)
        self.assertNotIn("depth",s)

    def test_full_deep_target_is_bounded(self):
        s=Path("v11198_deep_screen.py").read_text()
        self.assertIn("FULL_DEEP_TARGET",s)
        self.assertIn('"14"',s)
        self.assertIn("max(10,min(18",s.replace(" ",""))

    def test_fast_screen_never_delivers_signal(self):
        s=Path("v11198_deep_screen.py").read_text()
        self.assertNotIn("Signal(",s)
        self.assertIn("No signal may be delivered",s)

    def test_full_production_snapshot_still_runs(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("legacy.get_derivatives_snapshot",s)
        self.assertIn("_deep_one",s)

    def test_full_deep_is_adaptive_not_50_50(self):
        s=Path("v11198_deep_screen.py").read_text()
        self.assertIn("dominant=",s)
        self.assertIn("min_opposite",s)

    def test_manual_scan_diagnostics_are_truthful(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_v11199_scan_error_text",s)
        self.assertIn("deep shortlist deadline coverage incomplete",s)
        self.assertIn("fast derivatives screen coverage incomplete",s)
        self.assertIn("rate-limit cooldown",s)

    def test_manual_prime_handler_surfaces_scan_reason(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_prime_scan_cmd_v11199",s)
        self.assertIn('base.core.scan_status().get("main")',s)
        self.assertIn("manual market scan failed",s)

    def test_short_handler_surfaces_scan_reason(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_short_scan_cmd_v11199",s)
        self.assertIn('base.core.scan_status().get("short")',s)

    def test_v11200_paces_research_requests(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("REQUESTS_PER_SEC",s); self.assertIn("await _pace()",s)

    def test_v11200_singleflight(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("_inflight",s); self.assertIn("singleflight_hits",s)

    def test_v11200_does_not_cache_depth_or_klines(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("Never cache order book, candles",s)
        self.assertNotIn('if path.endswith("/depth"): return',s)

    def test_v11200_spot_snapshot_reuse(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("SPOT_DERIVATIVE_SNAPSHOT_TTL",s)
        self.assertIn("spot_scanner.get_derivatives_snapshot=spot_cached_snapshot",s)
        self.assertIn('"150"',s)

    def test_v11200_real_cooldown_still_pauses(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn('if cooldown>0:',s); self.assertIn('Health("PAUSE"',s)

    def test_v11200_weight_headroom(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn("V11200_SOFT_WEIGHT_CEILING",s); self.assertIn('"1150"',s)

    def test_v112001_follower_cancel_cannot_cancel_shared_future(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("asyncio.shield(future)",s)

    def test_v112001_leader_cancel_wakes_followers(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("except asyncio.CancelledError",s)
        self.assertIn("future.cancel()",s)
        self.assertIn("leader_cancellations",s)

    def test_v112001_execution_priority_is_narrow(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("def _realtime",s)
        segment=s[s.index("def _realtime"):s.index("def _ttl")]
        self.assertIn('/bookTicker',segment)
        self.assertNotIn('path.endswith("/depth")',segment)
        self.assertNotIn('path.endswith("/aggTrades")',segment)
        self.assertIn('_stats["realtime"]+=1',s)

    def test_v112002_auto_skips_full_scan_during_health_pause(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("health=await base.health_check(force=True)",s)
        self.assertIn("skipped before scan: health PAUSE",s)

    def test_v112002_manual_prime_uses_shared_scan_lock(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_v11205_full_scan_guard",s)
        self.assertIn("full-market research lock timeout",s)

    def test_v112002_spot_full_scan_is_serialized_with_futures(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_serialized_spot_scan_v11202",s)
        self.assertIn("base.spot_scan=_serialized_spot_scan_v11202",s)
        self.assertGreater(s.rindex("base.spot_scan=_serialized_spot_scan_v11202"),s.rindex("base.spot_scan=v11191_spot_engine.scan"))

    def test_v112002_depth_and_aggtrades_do_not_bypass_weight_guard(self):
        s=Path("v11200_data_architecture.py").read_text()
        segment=s[s.index("def _realtime"):s.index("def _ttl")]
        self.assertNotIn('path.endswith("/depth")',segment)
        self.assertNotIn('path.endswith("/aggTrades")',segment)
        self.assertIn('/bookTicker',segment)

    def test_v112002_funnel_reports_actual_full_deep_count(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn('d["deep_screen_candidates"]=len(deep_rows)',s)
        self.assertIn('d["deep_checked"]=len(full_rows)',s)

    def test_v112002_exchangeinfo_is_shared_cached(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn('path.endswith("/exchangeInfo"): return 300.0',s)

    def test_v112002_health_is_rechecked_after_waiting_for_lock(self):
        s=Path("bot_v11191.py").read_text()
        start=s.index("async def _run_automatic_scan")
        block=s[s.index("async with _v11205_full_scan_guard",start):]
        self.assertIn("health=await base.health_check(force=True)",block[:1500])

    def test_v112002_spot_manual_reports_actual_reason(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_spot_cmd_v11202",s)
        self.assertIn('reason=str(d.get("reason")',s)
        self.assertIn("base.spot_cmd=_spot_cmd_v11202",s)

    def test_v112003_health_callback_is_not_detached(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_callback_v11203",s)
        self.assertIn('data!="v112:health"',s)
        block=s[s.index("async def _callback_v11203"):s.index("base.core.callback=_callback_v11203")+80]
        self.assertNotIn("_spawn_ui_task",block)
        self.assertIn("wait_for",block)
        self.assertIn("timeout=15",block)

    def test_v112003_health_refresh_updates_last_health(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("base._last_health=health",s)

    def test_v112003_health_failure_is_visible(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("HEALTH CHECK FAILED · V11.21.3",s)
        self.assertIn("Старый PAUSE не считается новым результатом",s)

    def test_v112003_health_card_has_version_and_timestamp(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn("PRODUCTION HEALTH · V11.21.3",s)
        self.assertIn("Проверено:",s)
        self.assertIn("datetime.now(timezone.utc)",s)

    def test_v112004_prime_routes_through_app_bot_global(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("base.core.scan_cmd=_prime_scan_cmd_v11199",s)
        self.assertLess(s.index("async def _prime_scan_cmd_v11199"),s.index("base.core.scan_cmd=_prime_scan_cmd_v11199"))

    def test_v112004_fast_routes_through_app_bot_global(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("base.core.short_scan_cmd=_short_scan_cmd_v11199",s)
        self.assertLess(s.index("async def _short_scan_cmd_v11199"),s.index("base.core.short_scan_cmd=_short_scan_cmd_v11199"))

    def test_v112004_fast_has_shared_lock_and_health_preflight(self):
        s=Path("bot_v11191.py").read_text()
        block=s[s.index("async def _short_scan_cmd_v11199"):s.index("# Serialize scheduled Fast Radar")]
        self.assertIn("async with _v11205_full_scan_guard",block)
        self.assertIn("base.health_check(force=True)",block)
        self.assertIn("PRODUCTION HEALTH PAUSE",block)

    def test_v112004_button_routes_fail_fast_if_overwritten(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("PRIME FUTURES routing invariant failed",s)
        self.assertIn("FAST FUTURES routing invariant failed",s)

    def test_v112005_full_scan_lock_is_really_bounded(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_v11205_full_scan_guard",s)
        self.assertIn("asyncio.wait_for(base.core._scan_lock.acquire()",s)
        self.assertNotIn("while base.core._scan_lock.locked() and waited<wait_limit",s)

    def test_v112005_fast_radar_cannot_overlap_full_research(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_v11205_research_gate",s)
        self.assertIn("_fast_radar_job_v11205",s)
        self.assertIn("base.fast_radar_job=_fast_radar_job_v11205",s)

    def test_v112005_fast_ui_keeps_short_mode(self):
        s=Path("bot_v11191.py").read_text()
        block=s[s.index("async def _short_scan_cmd_v11199"):s.index("# Serialize scheduled Fast Radar")]
        self.assertIn("short=True",block)

    def test_v112005_health_and_spot_bindings_fail_fast(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("HEALTH callback routing invariant failed",s)
        self.assertIn("SPOT full-scan routing invariant failed",s)

    def test_v112006_current_pause_reason_beats_stale_diagnostics(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("priority_tokens",s)
        self.assertIn("PRODUCTION HEALTH PAUSE",s)
        self.assertIn("full-market research lock timeout",s)

    def test_v112006_zero_funnel_is_not_market_result(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("_heartbeat_text_v11206",s)
        self.assertIn("СКАН НЕ ЗАПУЩЕН / НЕТ ПОЛНОГО ПРОХОДА",s)
        self.assertIn("counts==(0,0,0,0)",s)

    def test_v112006_health_snapshot_is_synchronized(self):
        s=Path("bot_v11191.py").read_text()
        self.assertGreaterEqual(s.count("base._last_health=health"),5)

    def test_v112006_data_architecture_version_is_current(self):
        s=Path("v11200_data_architecture.py").read_text()
        self.assertIn("V11.21.3",s)
        self.assertNotIn("V11.20.2",s)

    def test_v112007_reads_weight_header_even_on_429(self):
        s=Path("v11196_api_resilience.py").read_text(); block=s[s.index("async def _telemetry_raw_get"):s.index("def _fmt_metric")]
        self.assertLess(block.index("x-mbx-used-weight-1m"),block.index("if response.status_code in (418,429)"))

    def test_v112007_reserves_heavy_endpoint_weight(self):
        s=Path("v11196_api_resilience.py").read_text(); self.assertIn("used + reserve < SOFT_WEIGHT_CEILING",s); self.assertIn('return 1 if p.get("symbol") else 40',s)

    def test_v112007_ticker_is_paced_and_cached(self):
        s=Path("v11200_data_architecture.py").read_text(); critical=s[s.index("def _critical"):s.index("def _realtime")]
        self.assertNotIn('path.endswith("/ticker/24hr")',critical); self.assertIn('if path.endswith("/ticker/24hr"): return 8.0',s)

    def test_v112007_blocked_auto_clears_stale_funnel(self):
        s=Path("bot_v11191.py").read_text(); self.assertIn("_v11207_blocked_diag",s); self.assertIn('"scan_started":False',s)

    def test_v112100_signal_rebalance_installed_after_base(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("install_v11210_signal_engine(base)",s)
        self.assertGreater(s.index("install_v11210_signal_engine(base)"),s.index("import bot_v11180 as base"))

    def test_v112100_futures_momentum_lane_preserves_hard_risk(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("_momentum_fallback",s)
        self.assertIn("MOMENTUM CONTINUATION RETEST",s)
        self.assertIn("spread<=5.0",s)
        self.assertIn('adl in ("low","medium")',s)

    def test_v112100_entry_uses_live_two_of_three_not_closed_context(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn("live_flow and non_opposing and checks>=2",s)
        self.assertIn('if state=="CANCEL" and "move escaped entry by >0.25R" in reason',s)
        self.assertIn("dist<=0.50",s)

    def test_v112100_hard_evidence_conflicts_cannot_be_relaxed(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn("if audit.eligible or audit.hard_conflicts",s)
        self.assertIn("not audit.hard_conflicts",s)

    def test_v112100_spot_buy_ready_needs_zone_and_execution(self):
        s=Path("v11191_spot_engine.py").read_text()
        self.assertIn("spot_v11210",s)
        self.assertIn("trend4 and in_zone",s)
        self.assertIn('spread_bps"),999)<=6.0',s)

    def test_v112110_new_decision_engine_has_fresh_futures_cohort(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn('FUTURES_COHORT="11.21.3-signal-engine"',s)
        self.assertIn("entry_base.FUTURES_RELEASE_KEY=FUTURES_COHORT",s)
        self.assertIn("base.db.STRATEGY_VERSION=FUTURES_COHORT",s)

    def test_v112110_spot_watch_and_signal_cohorts_are_synchronized(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn('SPOT_COHORT="11.21.3-spot-signal-engine"',s)
        self.assertIn("spot_db.SPOT_RELEASE_VERSION=SPOT_COHORT",s)
        self.assertIn("spot_watch.SPOT_RELEASE_KEY=SPOT_COHORT",s)

    def test_v112110_strong_annotation_matches_decision_assessment(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn("def strong_annotate(signal):",s)
        self.assertIn("a=strong_assess(signal)",s)
        self.assertIn("v11211_state_coherent",s)
        self.assertIn("base.annotate_strong_signals=strong_annotate_many",s)

    def test_v112110_spot_dedupe_is_release_scoped(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn("COALESCE(s.release_version,'')=?",s)
        self.assertIn("base.spot_was_sent_recently=spot_was_sent_recently_current",s)

    def test_v11212_meta_old_model_is_shadow_only(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn("meta_decide_shadow",s)
        self.assertIn("meta_shadow_only",s)
        self.assertIn("ready=False,eligible=True",s)

    def test_v11212_adaptive_old_model_is_shadow_only(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn("adaptive_gate_shadow",s)
        self.assertIn("adaptive_shadow_only",s)
        self.assertIn("return True,assessment",s)

    def test_v11212_old_entry_history_cannot_reject(self):
        s=Path("v11210_signal_engine.py").read_text()
        self.assertIn("entry_negative_penalty_shadow",s)
        self.assertIn("return 0.0,stats",s)

    def test_v11212_old_calibration_is_shadow_only(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn("historical_calibration_penalty",s)
        self.assertIn("calibration_shadow_only",s)
        self.assertIn("penalty = 0.0",s)

    def test_v11213_spot_watchtower_shares_research_gate(self):
        s=Path("bot_v11191.py").read_text()
        block=s[s.index("_original_spot_watch_job_v11213"):s.index("# Serialize scheduled Fast Radar")]
        self.assertIn("_v11205_research_gate.acquire()",block)
        self.assertIn("base.health_check(force=True)",block)
        self.assertIn("base.core._scan_lock.locked()",block)
        self.assertIn("base.spot_watch_job=_spot_watch_job_v11213",block)

    def test_v11213_entry_now_is_not_put_behind_research_gate(self):
        s=Path("bot_v11191.py").read_text()
        block=s[s.index("_original_spot_watch_job_v11213"):s.index("# Serialize scheduled Fast Radar")]
        self.assertNotIn("entry_monitor_job",block)
        self.assertNotIn("assess_entry_row",block)

    def test_v11213_funnel_preserves_fast_and_full_deep_counts(self):
        s=Path("v11191_futures_engine.py").read_text()
        self.assertIn('d["prefiltered"]=len(deep_rows)',s)
        self.assertIn('d["deep_checked"]=len(full_rows)',s)
        self.assertIn('"top_rejections"',s)

    def test_v11213_heartbeat_exposes_top_blockers(self):
        s=Path("bot_v11191.py").read_text()
        self.assertIn("🧬 Deep funnel",s)
        self.assertIn("🧱 Главные блокеры",s)
        self.assertIn("🔬 Ближайший",s)

    def test_v11213_health_exposes_request_weight(self):
        s=Path("v11196_api_resilience.py").read_text()
        self.assertIn("Binance weight 1m",s)
        self.assertIn("SOFT_WEIGHT_CEILING",s)
        self.assertIn("cooldown_seconds",s)

if __name__=="__main__":
    unittest.main()
