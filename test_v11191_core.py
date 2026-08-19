import ast
import unittest
from pathlib import Path

class V11193Contracts(unittest.TestCase):
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
        self.assertIn('sorted(ranked,key=lambda r:r[4]',s)
        self.assertIn('sorted(ranked,key=lambda r:r[5]',s)

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

if __name__=="__main__":
    unittest.main()
