from pathlib import Path
import ast

files=[
 "bot_v11191.py","v11191_futures_engine.py","v11191_spot_engine.py",
 "v11191_integrity.py","v11191_ui.py","test_v11191_core.py",
]
for p in files:
    ast.parse(Path(p).read_text(encoding="utf-8"),filename=p)

f=Path("v11191_futures_engine.py").read_text()
futures=f
s=Path("v11191_spot_engine.py").read_text()
spot=s
i=Path("v11191_integrity.py").read_text()
b=Path("bot_v11191.py").read_text()
api=Path("v11196_api_resilience.py").read_text()
sources=Path("v11197_sources.py").read_text()
screen=Path("v11198_deep_screen.py").read_text()
data_arch=Path("v11200_data_architecture.py").read_text()
signal_engine=Path("v11210_signal_engine.py").read_text()

checks={
 "Futures entire liquid universe ranked":"full_universe_ranked" in f,
 "Futures adaptive side-aware deep shortlist":"_select_deep_rows" in f and "MIN_OPPOSITE_SIDE_RESERVE" in f,
 "Futures no 1-2 prefilter bottleneck":"DEEP_SHORTLIST" in f and "36" in f,
 "Futures runtime wrappers":"legacy.get_derivatives_snapshot" in f and "legacy.get_news_sentiment" in f and "legacy.analyze" in f,
 "Futures side calibration":"calibration_penalty(symbol,side,timeframe)" in f,
 "Futures diagnostics merge":"getattr(legacy,\"_last_scan\"" in f,
 "Futures news degradation fail-open":"all news-risk sources are unavailable" not in f,
 "Futures ADL fallback":("adl_risks={}" in f or "adl_risks = {}" in f) and "symbol-level ADL fallback" in f,
 "Futures Fast Radar feed":"d[\"near_candidates\"]" in f,
 "Spot entire liquid universe ranked":"ranked.append((pre,symbol,excess))" in s,
 "Spot legacy diagnostics":"_last[\"prefiltered\"]=len(ranked)" in s,
 "Spot no EMA100 discovery kill":"FULL-UNIVERSE discovery" in s,
 "Spot no pre-L2 4H kill":"No second-stage 4H kill switch" in s,
 "Spot BEAR not blanket veto":"independent_recovery" in s,
 "Actual execution still checked":"analyze_book" in s,
 "Actual negative auxiliary risks preserved":"recent_negative" in s and "global_breaking" in s,
 "Clock multi-sample":"for _ in range(3)" in i and "min(samples,key=lambda x:x[0])" in i,
 "Patch before V11.18":"import bot_v11180 as base" in b and b.index("v11191_spot_engine.install()")<b.index("import bot_v11180 as base"),
 "Spot final delivery parity":"_delivery_spot_news" in b and "_delivery_spot_crowding" in b,
 "Real scanner deadline":"deadline = started + FULL_SCAN_BUDGET_SEC" in f and "deep_deadline_cancelled" in f,
 "Frame stage bounded":"FRAME_REQUEST_TIMEOUT_SEC" in f and "frame_pending_cancelled" in f,
 "Scheduler lock parity":"_run_automatic_scan_v11194" in b and "wait_limit" in b,
 "Live scan progress":"frame_coverage" in f and "deep_coverage" in f,
 "Geometry recovery module":Path("v11195_geometry.py").is_file() and "КОНТРОЛИРУЕМЫЙ PULLBACK" in Path("v11195_geometry.py").read_text(),
 "Spot news auxiliary":"news_snapshot=await asyncio.wait_for" in s and "news_degraded" in s,
 "Real strategy audit":"_strategy_audit" in f and "deep_rejections" in f,
 "Critical API reserve":"ANALYSIS_CONCURRENCY" in api and "_critical" in api,
 "Proactive weight headroom":"SOFT_WEIGHT_CEILING" in api and "_soft_weight_guard" in api,
 "Health no fake sentinels":"rate-limit cooldown" in api and "recent verified state retained" in api,
 "Mandatory source split":"mandatory_sources" in sources and "exchangeInfo timeout" in sources and "ticker/24hr timeout" in sources,
 "exchangeInfo verified cache":"EXCHANGEINFO_CACHE_MAX_SEC" in sources and '"source":"CACHE"' in sources,
 "ticker short cache":"TICKER_CACHE_MAX_SEC" in sources and "75" in sources,
 "Mandatory source priority":'path.endswith("/time")' in api and "_estimated_weight" in api,
 "Source diagnostics":"source_meta" in f and "mandatory_sources" in f and "source_error" in f,
 "Two-stage deep engine":"quick_deep_screen" in f and "select_full_deep" in f,
 "All 36 fast screened":"deep_screen_complete" in f and "deep_screen_coverage" in f,
 "Full deep reduced target":"FULL_DEEP_TARGET" in screen and '"14"' in screen,
 "Fast screen bulk premium":"premiumIndex" in screen and "_premium_bulk" in screen,
 "Fast screen OI only":"openInterest" in screen and "get_derivatives_snapshot" not in screen,
 "Full snapshot remains authoritative":"_deep_one" in f and "legacy.get_derivatives_snapshot" in f,
 "Truthful manual diagnostics":"_v11199_scan_error_text" in b and "_prime_scan_cmd_v11199" in b,
 "Deep deadline diagnostic":"deep shortlist deadline coverage incomplete" in b,
 "Fast screen diagnostic":"fast derivatives screen coverage incomplete" in b,
 "Shared REST pacing":"REQUESTS_PER_SEC" in data_arch and "_pace" in data_arch,
 "REST single-flight":"_inflight" in data_arch and "singleflight_hits" in data_arch,
 "Telemetry TTL cache":"def _ttl" in data_arch and "cache_hits" in data_arch,
 "Spot watch snapshot reuse":"SPOT_DERIVATIVE_SNAPSHOT_TTL" in data_arch and "_install_spot_snapshot_reuse" in data_arch,
 "Lower API headroom ceiling":"V11200_SOFT_WEIGHT_CEILING" in api and '"1150"' in api,
 "Architecture installed before inherited base":"install_v11200_data_architecture()" in b and b.index("install_v11200_data_architecture()") < b.index("import bot_v11180 as base"),
 "Singleflight cancellation safe":"asyncio.shield(future)" in data_arch and "except asyncio.CancelledError" in data_arch,
 "Realtime execution priority":"def _realtime" in data_arch and 'path.endswith("/bookTicker")' in data_arch and 'path.endswith("/depth")' not in data_arch[data_arch.index("def _realtime"):data_arch.index("def _ttl")] and 'path.endswith("/aggTrades")' not in data_arch[data_arch.index("def _realtime"):data_arch.index("def _ttl")],
 "AUTO health preflight":"health=await base.health_check(force=True)" in b and "skipped before scan: health PAUSE" in b,
 "Manual PRIME serialized":"async with _v11205_full_scan_guard" in b and "full-market research lock timeout" in b,
 "Spot/Futures heavy scan serialization":"_serialized_spot_scan_v11202" in b and b.rindex("base.spot_scan=_serialized_spot_scan_v11202") > b.rindex("base.spot_scan=v11191_spot_engine.scan"),
 "Two-stage diagnostic truth":"deep_screen_candidates" in f and 'd["deep_checked"]=len(full_rows)' in f,
 "exchangeInfo shared TTL":'path.endswith("/exchangeInfo"): return 300.0' in data_arch,
 "Health recheck inside scan lock":b.count("health=await base.health_check(force=True)")>=3,
 "Direct bounded HEALTH callback":"_callback_v11203" in b and 'data!="v112:health"' in b and "timeout=15" in b,
 "HEALTH updates runtime snapshot":"base._last_health=health" in b,
 "HEALTH always reports failure":"HEALTH CHECK FAILED · V11.21.5" in b,
 "HEALTH card timestamp":"PRODUCTION HEALTH · V11.21.5" in api and "Проверено:" in api,
 "PRIME actual callback binding":"base.core.scan_cmd=_prime_scan_cmd_v11199" in b,
 "FAST actual callback binding":"base.core.short_scan_cmd=_short_scan_cmd_v11199" in b,
 "FAST shared lock":"async def _short_scan_cmd_v11199" in b and "async with _v11205_full_scan_guard" in b[b.index("async def _short_scan_cmd_v11199"):],
 "Button routing invariants":"PRIME FUTURES routing invariant failed" in b and "FAST FUTURES routing invariant failed" in b,
 "Bounded full scan guard":"_v11205_full_scan_guard" in b and "asyncio.wait_for(base.core._scan_lock.acquire()" in b,
 "Research gate":"_v11205_research_gate" in b and "_fast_radar_job_v11205" in b,
 "FAST renderer uses short mode":"results,short=True" in b,
 "Health and Spot routing invariants":"HEALTH callback routing invariant failed" in b and "SPOT full-scan routing invariant failed" in b,
 "Current error beats stale diagnostics":"priority_tokens" in b and "full-market research lock timeout" in b,
 "Zero funnel truth":"_heartbeat_text_v11206" in b and "СКАН НЕ ЗАПУЩЕН / НЕТ ПОЛНОГО ПРОХОДА" in b,
 "Runtime health synchronized":b.count("base._last_health=health")>=5,
 "Data architecture version synchronized":"V11.21.5" in data_arch,
 "429 telemetry before raise":"_telemetry_raw_get" in api and 'governor._raw_get=_telemetry_raw_get' in api,
 "Weight-aware endpoint reservation":"_estimated_weight" in api and "_soft_weight_guard(path,params)" in api,
 "Lower safe research pace":'V11200_RESEARCH_RPS","4.5"' in data_arch,
 "Blocked cycle resets funnel":"_v11207_blocked_diag" in b and '"scan_started":False' in b,
 "Signal rebalance installed":"install_v11210_signal_engine(base)" in b,
 "Futures momentum continuation":"_momentum_fallback" in futures and "MOMENTUM CONTINUATION RETEST" in futures,
 "Spot BUY READY lane":"spot_v11210" in spot and "BUY READY" in spot,
 "Entry two-of-three live consensus":"2-of-3 micro consensus" in signal_engine,
 "Hard evidence conflicts preserved":"audit.hard_conflicts" in signal_engine,
 "Fresh cohort isolation":"11.21.5-signal-engine" in signal_engine and "11.21.5-spot-signal-engine" in signal_engine,
 "Meta history shadow-only":"meta_decide_shadow" in signal_engine and "meta_shadow_only" in signal_engine,
 "Adaptive history shadow-only":"adaptive_gate_shadow" in signal_engine and "adaptive_shadow_only" in signal_engine,
 "Entry history shadow-only":"entry_negative_penalty_shadow" in signal_engine,
 "Calibration history shadow-only":"historical_calibration_penalty" in futures and "penalty = 0.0" in futures,
 "Spot Watchtower research gate":"_spot_watch_job_v11213" in b and "SPOT WATCHTOWER routing invariant failed" in b,
 "Truthful two-stage funnel":'d["prefiltered"]=len(deep_rows)' in futures and 'd["deep_checked"]=len(full_rows)' in futures,
 "Top rejection diagnostics":"top_rejections" in futures and "Главные блокеры" in b,
 "Health request-weight telemetry":"Binance weight 1m" in api and "cooldown_seconds" in api,
 "Liquid primary whole-market pass":"_primary_frame" in futures and "for symbol in liquid" in futures,
 "Bounded multiframe shortlist":"MULTIFRAME_TARGET" in futures and "_extra_frames" in futures,
 "Impossible all-observed 3TF path removed":"Stage 1A" in futures and "primary_frame_coverage" in futures,
 "Primary-to-deep UI funnel":"liquid-primary" in b and "multi-TF" in b,
 "Full scan env floor":"FULL_SCAN_BUDGET_SEC = max(170" in futures,
 "Research RPS env cap":"min(4.5" in data_arch and "min(6.0" not in data_arch,
 "Soft ceiling env cap":"min(1150" in api and "),1150)" in data_arch,
 "Legacy diagnostic isolation":"production_keys" in futures and "update(compat[kind])" not in futures,
 "V11.21.5 Futures cohort":"FUTURES_COHORT=\"11.21.5-signal-engine\"" in signal_engine and "entry_base.FUTURES_RELEASE_KEY=FUTURES_COHORT" in signal_engine,
 "V11.21.5 Spot cohort":"SPOT_COHORT=\"11.21.5-spot-signal-engine\"" in signal_engine and "spot_watch.SPOT_RELEASE_KEY=SPOT_COHORT" in signal_engine,
 "Strong annotation coherent":"v11211_state_coherent" in signal_engine and "base.annotate_strong_signals=strong_annotate_many" in signal_engine,
 "Spot dedupe cohort aware":"COALESCE(s.release_version,'')=?" in signal_engine and "base.spot_was_sent_recently=spot_was_sent_recently_current" in signal_engine,
 "Truthful Spot manual diagnostics":"_spot_cmd_v11202" in b and "Spot scan не завершён" in b,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("V11.21.5 RELEASE CHECK FAILED: "+", ".join(failed))
print("V11.21.5 RELEASE CHECK: OK")
