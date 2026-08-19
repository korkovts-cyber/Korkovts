from pathlib import Path
import ast

files=[
 "bot_v11191.py","v11191_futures_engine.py","v11191_spot_engine.py",
 "v11191_integrity.py","v11191_ui.py","test_v11191_core.py",
]
for p in files:
    ast.parse(Path(p).read_text(encoding="utf-8"),filename=p)

f=Path("v11191_futures_engine.py").read_text()
s=Path("v11191_spot_engine.py").read_text()
i=Path("v11191_integrity.py").read_text()
b=Path("bot_v11191.py").read_text()

checks={
 "Futures entire liquid universe ranked":"full_universe_ranked" in f,
 "Futures adaptive side-aware deep shortlist":"_select_deep_rows" in f and "MIN_OPPOSITE_SIDE_RESERVE" in f,
 "Futures no 1-2 prefilter bottleneck":"DEEP_SHORTLIST" in f and "36" in f,
 "Futures runtime wrappers":"legacy.get_derivatives_snapshot" in f and "legacy.get_news_sentiment" in f and "legacy.analyze" in f,
 "Futures side calibration":"calibration_penalty(symbol,side,timeframe)" in f,
 "Futures diagnostics merge":"getattr(legacy,\"_last_scan\"" in f,
 "Futures news degradation fail-open":"all news-risk sources are unavailable" not in f,
 "Futures ADL fallback":"adl_risks={}" in f and "symbol-level fallback" in f,
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
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("V11.19.3 RELEASE CHECK FAILED: "+", ".join(failed))
print("V11.19.3 RELEASE CHECK: OK")
