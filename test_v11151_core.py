import unittest
from types import SimpleNamespace
import numpy as np
import pandas as pd
from v11151_indicators import evaluate, annotate


def frame(side="LONG", rvol=1.5, adverse=False):
    n=140; i=np.arange(n,dtype=float)
    if side=="LONG":
        close=100+.12*i
    else:
        close=120-.12*i
    open_=close-(.04 if side=="LONG" else -.04)
    high=np.maximum(open_,close)+.18
    low=np.minimum(open_,close)-.18
    vol=np.full(n,1000.0); vol[-1]=1000*rvol
    taker=vol*(.60 if side=="LONG" else .40)
    if adverse:
        if side=="LONG":
            high[-1]=high[-22:-1].max()+2; close[-1]=high[-22:-1].max()-.5; open_[-1]=close[-1]-.1
        else:
            low[-1]=low[-22:-1].min()-2; close[-1]=low[-22:-1].min()+.5; open_[-1]=close[-1]+.1
    return pd.DataFrame({"open":open_,"high":high,"low":low,"close":close,"volume":vol,"taker_buy_base":taker})


def snap(side="LONG"):
    return {"derivatives":{"oi_change_pct":1.5,"price_change_pct":.8 if side=="LONG" else -.8}}

class IndicatorEdgeTests(unittest.TestCase):
    def test_long_alignment_quality_first_pass(self):
        r=evaluate(frame("LONG"),"LONG",snap("LONG"),.25)
        self.assertTrue(r.auto_eligible); self.assertTrue(r.prime_eligible)
        self.assertGreaterEqual(r.support,4)

    def test_short_alignment_quality_first_pass(self):
        r=evaluate(frame("SHORT"),"SHORT",snap("SHORT"),-.25)
        self.assertTrue(r.auto_eligible)

    def test_opposite_live_flow_blocks(self):
        r=evaluate(frame("LONG"),"LONG",snap("LONG"),-.35)
        self.assertFalse(r.auto_eligible); self.assertTrue(any("flow" in x.lower() for x in r.blockers))

    def test_adverse_sweep_blocks(self):
        r=evaluate(frame("LONG",adverse=True),"LONG",snap("LONG"),.25)
        self.assertFalse(r.auto_eligible); self.assertTrue(any("sweep" in x.lower() for x in r.blockers))

    def test_low_rvol_blocks(self):
        r=evaluate(frame("LONG",.4),"LONG",snap("LONG"),.25)
        self.assertFalse(r.auto_eligible); self.assertTrue(any("rvol" in x.lower() for x in r.blockers))

    def test_adverse_oi_build_blocks(self):
        s={"derivatives":{"oi_change_pct":2.0,"price_change_pct":-.8}}
        r=evaluate(frame("LONG"),"LONG",s,.25)
        self.assertFalse(r.auto_eligible)
        self.assertEqual(r.oi_matrix,"ADVERSE_POSITION_BUILD")

    def test_missing_data_fails_closed(self):
        r=evaluate(pd.DataFrame(),"LONG",snap("LONG"),.2)
        self.assertFalse(r.available); self.assertFalse(r.auto_eligible)

    def test_annotation_never_changes_rank(self):
        s=SimpleNamespace(side="LONG",professional_rank=91.0,feature_snapshot=snap("LONG"))
        annotate(s,frame("LONG"),.25)
        self.assertEqual(s.professional_rank,91.0)
        self.assertIn("indicator_edge_v11151",s.feature_snapshot)

if __name__=="__main__": unittest.main()

class RuntimeContractTests(unittest.TestCase):
    def test_runtime_quality_first_gate_is_before_entry(self):
        from pathlib import Path
        src=Path('bot_v11151.py').read_text(encoding='utf-8')
        self.assertIn('indicator_checked=await _annotate_indicator_edge_many(protection_valid)',src)
        self.assertIn('if not strong.auto_eligible or not _indicator_gate(signal):',src)
        self.assertIn('_indicator_gate(fresh,prime=True)',src)

    def test_manual_symbol_has_no_indicator_bypass(self):
        from pathlib import Path
        src=Path('bot_v11151.py').read_text(encoding='utf-8')
        self.assertIn('await _annotate_indicator_edge_one(result)',src)
        self.assertIn('setup rejected by Indicator Edge',src)

    def test_indicator_pack_does_not_mutate_professional_rank(self):
        from pathlib import Path
        src=Path('v11151_indicators.py').read_text(encoding='utf-8')
        self.assertNotIn('professional_rank +=',src)
        self.assertNotIn('professional_rank=',src)
        self.assertIn('professional_rank_changed":False',src)

class TelegramResponsivenessTests(unittest.TestCase):
    def test_heavy_buttons_are_acknowledged_then_detached(self):
        from pathlib import Path
        src=Path('bot_v11151.py').read_text(encoding='utf-8')
        self.assertIn('await query.answer("Запущено")',src)
        self.assertIn('_spawn_ui_task(heavy[data](update,context),data)',src)
        self.assertIn('_spawn_ui_task(spot_cmd(update,context),"spot")',src)

    def test_detached_task_exceptions_are_observed(self):
        from pathlib import Path
        src=Path('bot_v11151.py').read_text(encoding='utf-8')
        self.assertIn('task.add_done_callback(done)',src)
        self.assertIn('Detached UI task %s failed',src)
