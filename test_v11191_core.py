import ast
import unittest
from pathlib import Path

class V11199Contracts(unittest.TestCase):
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
        self.assertIn('path.endswith("/exchangeInfo")',s)
        self.assertIn('path.endswith("/ticker/24hr")',s)

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

if __name__=="__main__":
    unittest.main()
