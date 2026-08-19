import asyncio,time,unittest
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from v11170_snapshot import assess as assess_snapshot
from v11170_execution import assess as assess_execution
from v11170_risk import assess as assess_risk
from v11170_validation import no_future_rows

class S:
    symbol='TESTUSDT'; timeframe='1H'; side='LONG'; professional_rank=92
    def __init__(self):
        self.feature_snapshot={
            'data_coherence_v11100':{'eligible':True,'observable':True},
            'data_freshness_v1142':{'derivatives_acquire_ms':1200},
            'strong_consensus_v11150':{'auto_eligible':True,'score':91},
            'indicator_edge_v11151':{'auto_eligible':True,'score':88},
            'adaptive_edge_v11160':{'eligible':True,'score':90},
        }

def book(**kw):
    now=time.time()
    d={'sequence_synced':True,'healthy':True,'event_age_sec':.2,'exchange_lag_sec':.1,'fetched_at':now,
       'spread_bps':1.0,'stability_score':90,'bid_replenishment_ratio':1.0,
       'ask_replenishment_ratio':1.0,'median_imbalance_20bps':.1,'book_samples':20,
       'book_coverage_sec':10,'gaps':0,'resyncs':1,'bid_depth_change_2s':0,
       'ask_depth_change_2s':0,'adverse_long_share_5s':0,'adverse_short_share_5s':0,
       'bids':[(99.9,100),(99.8,100)],'asks':[(100.0,100),(100.1,100)]}
    d.update(kw); return d

class HardeningTests(unittest.TestCase):
    def test_missing_coherence_fails_closed(self):
        s=S(); s.feature_snapshot.pop('data_coherence_v11100')
        self.assertFalse(assess_snapshot(s,book()).eligible)

    def test_missing_derivative_timing_fails_closed(self):
        s=S(); s.feature_snapshot.pop('data_freshness_v1142')
        self.assertFalse(assess_snapshot(s,book()).eligible)

    def test_snapshot_fingerprint_ignores_capture_clock(self):
        s1=S(); b1=book(fetched_at=1000.0)
        s2=S(); b2=dict(b1); b2['fetched_at']=1001.0
        d1=assess_snapshot(s1,b1,now=1000.0)
        d2=assess_snapshot(s2,b2,now=1001.0)
        self.assertEqual(d1.snapshot_fingerprint,d2.snapshot_fingerprint)

    def test_clock_skew_blocks_snapshot(self):
        now=time.time(); self.assertFalse(assess_snapshot(S(),book(fetched_at=now+10),now=now).eligible)

    def test_future_trigger_timestamp_blocks(self):
        now=time.time(); self.assertFalse(assess_execution(S(),book(fetched_at=now),now+10,now).eligible)

    def test_unsorted_execution_ladder_blocks(self):
        b=book(asks=[(100.1,100),(100.0,100)])
        self.assertFalse(assess_execution(S(),b,time.time()).eligible)

    def test_5k_depth_is_required(self):
        b=book(asks=[(100.0,5)],bids=[(99.9,5)])
        self.assertFalse(assess_execution(S(),b,time.time()).eligible)

    def test_final_score_reflects_weakest_component(self):
        s=S(); snap=SimpleNamespace(eligible=True,score=82,blockers=())
        ex=SimpleNamespace(eligible=True,score=79,blockers=())
        strong=SimpleNamespace(auto_eligible=True,score=91)
        d=assess_risk(s,strong,True,True,snap,ex,True)
        self.assertEqual(d.score,79)
        self.assertTrue(d.eligible)

    def test_missing_timestamp_column_blocks_validation(self):
        df=pd.DataFrame({'close':[1,2,3]})
        self.assertFalse(no_future_rows(df,time.time()).eligible)

    def test_duplicate_timestamps_block_validation(self):
        ts=pd.Timestamp.now(tz='UTC')-pd.Timedelta(seconds=10)
        df=pd.DataFrame({'open_time':[ts,ts]})
        self.assertFalse(no_future_rows(df,time.time()).eligible)

    def test_sqlite_reports_are_offloaded_from_callback(self):
        s=Path('bot_v11171.py').read_text()
        self.assertIn('await asyncio.to_thread(challenger_report_text)',s)
        self.assertIn('await asyncio.to_thread(attribution_report_text)',s)
        self.assertIn('asyncio.to_thread(\n                record_v11170_challenger',s)

    def test_replay_persistence_is_off_event_loop(self):
        s=Path('bot_v11171.py').read_text()
        self.assertIn('asyncio.to_thread(\n                record_v11160_replay',s)

if __name__=='__main__': unittest.main()
