import sqlite3, tempfile, time, unittest
from pathlib import Path
import pandas as pd
import v11160_adaptive as adaptive
from v11100_data import validate_snapshot, validate_frame
from v11170_snapshot import assess as assess_snapshot
from v11170_execution import assess as assess_execution

class Sig:
    symbol='TESTUSDT'; timeframe='1H'; side='LONG'; setup_type='BREAKOUT'
    def __init__(self):
        now=time.time()*1000
        self.feature_snapshot={
            'data_coherence_v11100':{'eligible':True,'observable':True},
            'data_freshness_v1142':{'derivatives_acquire_ms':1000,'acquire_started_ms':now-1000,'acquire_finished_ms':now},
        }

def frame(interval_sec, n=20, now=None):
    now=time.time() if now is None else now
    last_open=(int(now)//interval_sec-1)*interval_sec
    opens=[pd.Timestamp((last_open-(n-1-i)*interval_sec),unit='s',tz='UTC') for i in range(n)]
    return pd.DataFrame({'open_time':opens,'open':[1]*n,'high':[2]*n,'low':[.5]*n,'close':[1.5]*n,'volume':[100]*n})

def book(**kw):
    now=time.time(); d={'sequence_synced':True,'healthy':True,'event_age_sec':.2,'exchange_lag_sec':.1,'fetched_at':now,
        'spread_bps':1.0,'stability_score':90,'bids':[(99.9,100),(99.8,100)],'asks':[(100.0,100),(100.1,100)],
        'bid_depth_change_2s':0,'ask_depth_change_2s':0,'adverse_long_share_5s':0,'adverse_short_share_5s':0}
    d.update(kw); return d

class DeepAuditHardeningTests(unittest.TestCase):
    def test_adaptive_uses_latest_window_not_oldest(self):
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            con=sqlite3.connect(tmp.name)
            con.execute('CREATE TABLE signals(id INTEGER PRIMARY KEY,status TEXT,activated_at TEXT,is_shadow INTEGER,delivery_state TEXT,pnl_r REAL,timeframe TEXT,side TEXT,setup_type TEXT,result TEXT,closed_at TEXT,created_at TEXT)')
            for i in range(120):
                # oldest 80 positive, newest 40 sharply negative
                r=0.5 if i<80 else -0.8
                ts=f'2026-01-{1+i//5:02d}T00:00:00+00:00'
                con.execute('INSERT INTO signals(status,activated_at,is_shadow,delivery_state,pnl_r,timeframe,side,setup_type,result,closed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    ('CLOSED',ts,0,'DELIVERED',r,'1H','LONG','BREAKOUT','SL' if r<0 else 'TP2',ts,ts))
            con.commit(); con.close()
            old=adaptive._db_path; adaptive._db_path=lambda:tmp.name
            try:
                rows=adaptive._rows_for(Sig(),80)
                self.assertEqual(len(rows),80)
                self.assertLess(sum(x['pnl_r'] for x in rows[-20:])/20,0)
                d=adaptive.assess_signal(Sig())
                self.assertIn(d.label,{'DEGRADED','QUARANTINE'})
            finally: adaptive._db_path=old

    def test_snapshot_requires_all_three_timeframes_observable(self):
        now=time.time(); lower=frame(900,now=now); base=frame(3600,now=now); higher=pd.DataFrame({'close':[1,2,3]})
        d=validate_snapshot('1H',lower,base,higher,now)
        self.assertFalse(d.observable)
        self.assertEqual(d.status,'UNOBSERVABLE')

    def test_duplicate_or_nonmonotonic_candles_are_not_coherent(self):
        now=time.time(); f=frame(3600,now=now)
        f.loc[f.index[-1],'open_time']=f.loc[f.index[-2],'open_time']
        d=validate_frame(f,'1h','base',now)
        self.assertFalse(d.ok)
        self.assertIn('non-monotonic',d.reason)

    def test_derivative_timing_must_be_complete_and_consistent(self):
        s=Sig(); s.feature_snapshot['data_freshness_v1142']={'derivatives_acquire_ms':0}
        self.assertFalse(assess_snapshot(s,book()).eligible)
        s=Sig(); s.feature_snapshot['data_freshness_v1142']={'derivatives_acquire_ms':1000,'acquire_started_ms':1000,'acquire_finished_ms':5000}
        self.assertFalse(assess_snapshot(s,book()).eligible)

    def test_execution_requires_near_complete_requested_depth(self):
        # 99.2% filled used to pass the old 2% tolerance; hardening requires >=99.5%.
        b=book(asks=[(100.0,49.6)],bids=[(99.9,100)])
        d=assess_execution(Sig(),b,time.time())
        self.assertFalse(d.eligible)
        self.assertIn('$5k executable depth unavailable',d.blockers)

    def test_invalid_side_fails_execution(self):
        s=Sig(); s.side='FLAT'
        self.assertFalse(assess_execution(s,book(),time.time()).eligible)

    def test_shutdown_cancels_detached_ui_tasks_source_contract(self):
        src=Path('bot_v11172.py').read_text()
        self.assertIn('pending=list(_ui_background_tasks)',src)
        self.assertIn('await asyncio.gather(*pending,return_exceptions=True)',src)

    def test_adaptive_sqlite_wait_is_bounded_and_cached(self):
        src=Path('v11160_adaptive.py').read_text()
        self.assertIn("sqlite3.connect(path,timeout=.25)",src)
        self.assertIn("ROW_CACHE_TTL_SEC=30.0",src)
        self.assertIn("_ROW_CACHE[key]",src)

    def test_db_heavy_callback_reports_are_detached(self):
        src=Path('bot_v11172.py').read_text()
        for label in ('performance','adaptive','replay','challenger','attribution','edgelab','manager','spotwatch','entrynow-status','active-status'):
            self.assertIn(f'_spawn_sync_text_reply(query,',src)
        self.assertIn('def _spawn_sync_text_reply',src)

if __name__=='__main__': unittest.main()
