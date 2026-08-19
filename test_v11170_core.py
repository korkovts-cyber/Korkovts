import unittest,time
from types import SimpleNamespace
import pandas as pd
from v11170_snapshot import assess as assess_snapshot
from v11170_execution import trigger_freshness,assess as assess_execution
from v11170_risk import assess as assess_risk
from v11170_validation import no_future_rows,recursive_stability

class S:
    symbol='TESTUSDT'; timeframe='1H'; side='LONG'; professional_rank=92
    def __init__(self):
        self.feature_snapshot={
            'data_coherence_v11100':{'eligible':True,'observable':True},
            'data_freshness_v1142':{'derivatives_acquire_ms':1200},
            'strong_consensus_v11150':{'auto_eligible':True},
            'indicator_edge_v11151':{'auto_eligible':True},
            'adaptive_edge_v11160':{'eligible':True},
        }

def book(**kw):
    d={'sequence_synced':True,'healthy':True,'event_age_sec':.2,'exchange_lag_sec':.1,'fetched_at':time.time(),
       'spread_bps':1.0,'stability_score':90,'bid_replenishment_ratio':1.0,
       'ask_replenishment_ratio':1.0,'median_imbalance_20bps':.1,'book_samples':20,
       'book_coverage_sec':10,'gaps':0,'resyncs':1,'bid_depth_change_2s':0,
       'ask_depth_change_2s':0,'adverse_long_share_5s':0,'adverse_short_share_5s':0,
       'bids':[(99.9,100),(99.8,100)],'asks':[(100.0,100),(100.1,100)]}
    d.update(kw); return d

class SnapshotTests(unittest.TestCase):
    def test_coherent_snapshot_passes(self):
        d=assess_snapshot(S(),book()); self.assertTrue(d.eligible); self.assertGreaterEqual(d.score,80)
    def test_stale_book_blocks(self):
        d=assess_snapshot(S(),book(event_age_sec=8)); self.assertFalse(d.eligible); self.assertTrue(d.blockers)
    def test_unsynced_book_blocks(self):
        d=assess_snapshot(S(),book(sequence_synced=False)); self.assertFalse(d.eligible)
    def test_snapshot_fingerprint_deterministic_shape(self):
        d=assess_snapshot(S(),book()); self.assertEqual(len(d.snapshot_fingerprint),64)

class ExecutionTests(unittest.TestCase):
    def test_trigger_half_life_monotonic(self):
        xs=[trigger_freshness(x) for x in (0,15,30,45,60,61)]
        self.assertEqual(xs,sorted(xs,reverse=True)); self.assertEqual(xs[-1],0)
    def test_good_execution_passes(self):
        d=assess_execution(S(),book(),time.time()-5); self.assertTrue(d.eligible)
    def test_expired_trigger_blocks(self):
        d=assess_execution(S(),book(),time.time()-70); self.assertFalse(d.eligible); self.assertIn('expired',' '.join(d.blockers))
    def test_liquidity_withdrawal_blocks(self):
        d=assess_execution(S(),book(bid_depth_change_2s=-.7),time.time()); self.assertFalse(d.eligible)
    def test_wide_spread_blocks(self):
        d=assess_execution(S(),book(spread_bps=9),time.time()); self.assertFalse(d.eligible)

class RiskTests(unittest.TestCase):
    def test_all_layers_required(self):
        s=S(); snap=assess_snapshot(s,book()); ex=assess_execution(s,book(),time.time())
        strong=SimpleNamespace(auto_eligible=True)
        d=assess_risk(s,strong,True,True,snap,ex,True); self.assertTrue(d.eligible)
        d2=assess_risk(s,strong,False,True,snap,ex,True); self.assertFalse(d2.eligible)
    def test_gateway_is_negative_only(self):
        s=S(); snap=assess_snapshot(s,book()); ex=assess_execution(s,book(),time.time())
        d=assess_risk(s,SimpleNamespace(auto_eligible=True),True,True,snap,ex,True)
        self.assertLessEqual(d.score,100)

class ValidationTests(unittest.TestCase):
    def test_future_row_detected(self):
        now=time.time(); df=pd.DataFrame({'close_time':[pd.Timestamp(now-1,unit='s',tz='UTC'),pd.Timestamp(now+10,unit='s',tz='UTC')]})
        self.assertFalse(no_future_rows(df,now).eligible)
    def test_monotonic_closed_rows_pass(self):
        now=time.time(); df=pd.DataFrame({'close_time':[pd.Timestamp(now-10,unit='s',tz='UTC'),pd.Timestamp(now-1,unit='s',tz='UTC')]})
        self.assertTrue(no_future_rows(df,now).eligible)
    def test_recursive_guard_detects_unstable_indicator(self):
        df=pd.DataFrame({'x':range(200)})
        d=recursive_stability(lambda x: x['x'].mean(),df,(80,120,180),tolerance=.01)
        self.assertFalse(d.eligible)

class RuntimeContracts(unittest.TestCase):
    def test_final_gateway_before_save_pending(self):
        from pathlib import Path
        s=Path('bot_v11170.py').read_text()
        self.assertLess(s.index('final_gate=assess_final_risk('),s.index('signal_id=core.save_pending(fresh)'))
    def test_trigger_ages_original_ready_check(self):
        from pathlib import Path
        s=Path('bot_v11170.py').read_text()
        self.assertIn('getattr(initial_assessment,"checked_at"',s)
    def test_new_callbacks_are_acknowledged(self):
        from pathlib import Path
        s=Path('bot_v11170.py').read_text()
        for token in ('v1117:risk','v1117:challenger','v1117:attribution'):
            self.assertIn(token,s)

if __name__=='__main__': unittest.main()
