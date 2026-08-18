"""V11.12 deterministic production-hardening tests; no external network."""
from __future__ import annotations
import os, sys, tempfile, types, unittest, json, math
from unittest.mock import patch

if "app" not in sys.modules:
    app=types.ModuleType("app"); app.__path__=[]; sys.modules["app"]=app
if "app.market" not in sys.modules:
    market=types.ModuleType("app.market")
    async def _get(*a,**k): raise RuntimeError("network disabled")
    market._get=_get; sys.modules["app.market"]=market
if "app.db" not in sys.modules:
    db=types.ModuleType("app.db"); db.open_signals=lambda: []; sys.modules["app.db"]=db

import v11_live
import v11110_futures_orderbook as ob
import v11110_tape as tape
from v11120_contract import evaluate_live_contract
from v11120_replay import replay_gate, compare_gate


def l2():
    return {"sequence_synced":True,"healthy":True,"best_bid":100.0,"best_ask":100.1}
def st(**extra):
    d={"samples":12,"coverage_sec":8,"stability_score":82,"last_gap_age_sec":90,
       "median_imbalance_20bps":.05,"bid_replenishment_ratio":.8,"ask_replenishment_ratio":.8,
       "bid_depth_change_2s":0.0,"ask_depth_change_2s":0.0,"spread_ratio_2s":1.0,
       "current_spread_bps":1.0,"adverse_long_share_5s":0.0,"adverse_short_share_5s":0.0,
       "recent5_samples":10}
    d.update(extra); return d
def quote(): return {"bid":100.0,"ask":100.1,"ts":1000.0}
def flow(**extra):
    d={"age_sec":1.0,"total_notional":25000,"trades":30,"active_seconds":8,"coverage_sec":8,
       "max_bucket_share":.25,"buy_share":.60,"recent10_total_notional":12000,"recent10_trades":15,
       "active_seconds_10s":6,"coverage_10s":6,"max_bucket_share_10s":.30,"buy_share_10s":.60}
    d.update(extra); return d

class LiveContractTests(unittest.TestCase):
    def test_quote_is_mandatory(self):
        r=evaluate_live_contract("LONG",l2(),st(),None,flow(),now=1001)
        self.assertFalse(r.ok); self.assertIn("bookTicker",r.reason)
    def test_nonfinite_quote_is_vetoed(self):
        q=quote(); q["bid"]=float("nan")
        r=evaluate_live_contract("LONG",l2(),st(),q,flow(),now=1001)
        self.assertFalse(r.ok); self.assertIn("non-finite",r.reason)
    def test_nonfinite_flow_is_vetoed(self):
        r=evaluate_live_contract("LONG",l2(),st(),quote(),flow(total_notional=float("inf")),now=1001)
        self.assertFalse(r.ok); self.assertIn("non-finite",r.reason)
    def test_nonfinite_inherited_l2_metric_is_vetoed(self):
        r=evaluate_live_contract("LONG",l2(),st(stability_score=float("nan")),quote(),flow(),now=1001)
        self.assertFalse(r.ok); self.assertIn("non-finite",r.reason)
    def test_persistent_flow_can_pass(self):
        self.assertTrue(evaluate_live_contract("LONG",l2(),st(),quote(),flow(),now=1001).ok)
    def test_one_second_burst_is_vetoed(self):
        r=evaluate_live_contract("LONG",l2(),st(),quote(),flow(active_seconds_10s=1,coverage_10s=0),now=1001)
        self.assertFalse(r.ok); self.assertIn("persistent",r.reason)
    def test_concentrated_flow_is_vetoed(self):
        r=evaluate_live_contract("LONG",l2(),st(),quote(),flow(max_bucket_share_10s=.92),now=1001)
        self.assertFalse(r.ok); self.assertIn("burst",r.reason)
    def test_recent_flow_reversal_is_vetoed(self):
        r=evaluate_live_contract("LONG",l2(),st(),quote(),flow(buy_share_10s=.30),now=1001)
        self.assertFalse(r.ok); self.assertIn("reversed",r.reason)
    def test_liquidity_pull_is_vetoed(self):
        r=evaluate_live_contract("LONG",l2(),st(bid_depth_change_2s=-.60,bid_replenishment_ratio=.50),quote(),flow(),now=1001)
        self.assertFalse(r.ok); self.assertIn("liquidity pulled",r.reason)
    def test_spread_shock_is_vetoed(self):
        r=evaluate_live_contract("SHORT",l2(),st(spread_ratio_2s=3.2,current_spread_bps=3.0),quote(),flow(buy_share_10s=.40),now=1001)
        self.assertFalse(r.ok); self.assertIn("spread shock",r.reason)
    def test_persistent_adverse_imbalance_is_vetoed(self):
        r=evaluate_live_contract("LONG",l2(),st(adverse_long_share_5s=.8,recent5_samples=10),quote(),flow(),now=1001)
        self.assertFalse(r.ok); self.assertIn("adverse",r.reason)

    def test_short_side_none_metrics_do_not_crash(self):
        x=st(ask_depth_change_2s=None,ask_replenishment_ratio=None,adverse_short_share_5s=None)
        r=evaluate_live_contract("SHORT",l2(),x,quote(),flow(),now=1001)
        self.assertFalse(r.ok)


class EntryWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_base_ready_is_vetoed_by_nonpersistent_flow(self):
        import dataclasses, importlib
        old_base=sys.modules.get("v1142_entry_now")
        old_wrap=sys.modules.pop("v11120_entry_now",None)
        fake=types.ModuleType("v1142_entry_now")
        @dataclasses.dataclass(frozen=True)
        class A:
            state:str; score:float; reason:str
        fake.TriggerAssessment=A
        async def get_klines(*a,**k): return [1]
        fake.get_klines=get_klines
        fake.live_price=lambda *a,**k:100.0
        fake.live_book=lambda *a,**k:{"bid":100.0,"ask":100.1,"ts":1000.0}
        fake.live_flow=lambda *a,**k:{"age_sec":1,"total_notional":20000,"trades":20,"active_seconds":1,"coverage_sec":0,"max_bucket_share":1.0,"buy_share":.65,"recent10_total_notional":20000,"recent10_trades":20,"active_seconds_10s":1,"coverage_10s":0,"max_bucket_share_10s":1.0,"buy_share_10s":.65}
        fake.evaluate=lambda *a,**k:A("READY",90.0,"base ready")
        fake.row_from_signal=lambda sig:{"symbol":"BTCUSDT","side":"LONG"}
        for name in ("init","arm","active_rows","active_symbols","get_row","record_check","mark_pending_delivery","mark_delivery_uncertain","mark_triggered","mark_shadowed","cancel","status_text"):
            setattr(fake,name,lambda *a,**k:None)
        sys.modules["v1142_entry_now"]=fake
        try:
            wrap=importlib.import_module("v11120_entry_now")
            wrap.futures_l2_snapshot=lambda *a,**k:l2()
            wrap.futures_l2_stability=lambda *a,**k:st()
            wrap.capture_decision=lambda *a,**k:None
            with patch("v11120_contract.evaluate_entry_contract") as old_gate:
                from v11110_contract import ContractResult
                old_gate.return_value=ContractResult(True,"ok",82)
                out=await wrap.assess_row({"symbol":"BTCUSDT","side":"LONG","last_state":"WAIT"})
            self.assertEqual(out.state,"WAIT")
            self.assertIn("persistent",out.reason)
        finally:
            sys.modules.pop("v11120_entry_now",None)
            if old_wrap is not None: sys.modules["v11120_entry_now"]=old_wrap
            if old_base is not None: sys.modules["v1142_entry_now"]=old_base
            else: sys.modules.pop("v1142_entry_now",None)

class FlowMetricsTests(unittest.TestCase):
    def test_flow_reports_persistence_and_concentration(self):
        v11_live._trade_flow.clear()
        with patch("v11_live.time.time",return_value=1005.2):
            rows=v11_live._trade_flow["XUSDT"]
            rows.extend([[1000,100,0,1,1000],[1002,100,0,1,1002],[1005,200,0,2,1005]])
            out=v11_live.flow("XUSDT",60,20)
        self.assertEqual(out["active_seconds"],3)
        self.assertGreaterEqual(out["coverage_sec"],5)
        self.assertAlmostEqual(out["max_bucket_share"],.5,places=6)
        self.assertGreaterEqual(out["active_seconds_10s"],3)
        self.assertGreaterEqual(out["coverage_10s"],5)

class L2ShockMetricsTests(unittest.TestCase):
    def test_stability_exposes_depth_shock_metrics(self):
        b=ob.LocalBook("X")
        now=1000.0
        b.load_snapshot({"lastUpdateId":1,"bids":[["99","10"]],"asks":[["101","10"]],"fetched_at":now})
        b.bridge_pending=False; b.last_event_ts=now; b.last_exchange_event_ms=int(now*1000); b.last_exchange_lag_sec=0
        b.history.extend([(994.0,1.0,1000,1000,0),(996.0,1.0,1000,1000,0),(998.0,1.0,1000,1000,0),(1000.0,3.0,400,1000,-.4)])
        oldc=ob._connected; ob._connected=True; ob._books["X"]=b
        try:
            with patch("v11110_futures_orderbook.time.time",return_value=now):
                out=ob.stability("X",3)
            self.assertIn("bid_depth_change_2s",out)
            self.assertLess(out["bid_depth_change_2s"],0)
            self.assertGreaterEqual(out["spread_ratio_2s"],1)
        finally:
            ob._connected=oldc; ob._books.pop("X",None)

    def test_nonfinite_depth_level_forces_gap(self):
        b=ob.LocalBook("X")
        b.load_snapshot({"lastUpdateId":10,"bids":[["99","1"]],"asks":[["101","1"]],"fetched_at":1000})
        event={"U":10,"u":11,"pu":0,"E":1000000,"b":[["99", "nan"]],"a":[]}
        out=b.apply_event(event,now=1000,first_after_snapshot=True)
        self.assertEqual(out,"GAP")
        self.assertFalse(b.synced)

class TapeRetentionTests(unittest.TestCase):
    def test_capture_throttle_key_is_symbol_aware(self):
        tmp=tempfile.TemporaryDirectory(); old=tape._DB_PATH
        try:
            tape._DB_PATH=os.path.join(tmp.name,"t.db"); tape._buffers.clear(); tape._last_capture.clear(); tape.init()
            a=types.SimpleNamespace(state="WAIT",score=1,reason="x")
            x=tape.capture_decision({"symbol":"AAAUSDT","id":0},a,force=False)
            y=tape.capture_decision({"symbol":"BBBUSDT","id":0},a,force=False)
            self.assertIsNotNone(x); self.assertIsNotNone(y)
        finally:
            tape._DB_PATH=old; tmp.cleanup()

    def test_bundle_records_actual_gate_version(self):
        tmp=tempfile.TemporaryDirectory(); old=tape._DB_PATH
        try:
            tape._DB_PATH=os.path.join(tmp.name,"t.db"); tape._buffers.clear(); tape._last_capture.clear(); tape.init()
            a=types.SimpleNamespace(state="READY",score=90,reason="ok")
            bid=tape.capture_decision({"symbol":"AAAUSDT","id":1,"side":"LONG"},a,
                l2={"lastUpdateId":10,"bids":[[99,1]],"asks":[[101,1]]},
                gate={"version":"11.12.0-live-contract","ok":True},force=True)
            bundle=tape.load_bundle(bid)
            self.assertEqual(bundle["gate_version"],"11.12.0-live-contract")
            self.assertEqual(bundle["schema"],"v11.12.0-tape-2")
        finally:
            tape._DB_PATH=old; tmp.cleanup()

    def test_oversize_tape_uses_explicit_decision_anchor(self):
        events=[{"recv_ts":1000+i/10,"exchange_ms":0,"kind":"depth","payload":{"blob":"x"*1000}} for i in range(30)]
        bundle={"events":events}
        l2row={"lastUpdateId":55,"bids":[[99,2]],"asks":[[101,3]]}
        with patch.object(tape,"_MAX_BUNDLE_RAW_BYTES",500):
            raw=tape._compact_bundle_events(bundle,l2row,1003.0)
        out=json.loads(raw.decode("utf-8"))
        self.assertFalse(out["sequence_replay_complete"])
        anchors=[e for e in out["events"] if e.get("kind")=="depth_snapshot"]
        self.assertTrue(anchors)
        self.assertEqual(anchors[-1]["payload"]["source"],"decision_anchor")

class ReplayTests(unittest.TestCase):
    def test_live_gate_replay_is_deterministic(self):
        bundle={"captured_at":1001.0,"arm":{"side":"LONG"},"l2":l2(),"l2_stability":st(),"quote":quote(),"flow":flow()}
        out=replay_gate(bundle)
        self.assertTrue(out["ok"]); self.assertEqual(out["gate_version"],"11.12.0-live-contract")
        cmp=compare_gate({**bundle,"gate":{"ok":True}})
        self.assertTrue(cmp["same"])

class SourceContractTests(unittest.TestCase):
    def test_ws_watchdog_is_present(self):
        from pathlib import Path
        src=Path(os.path.join(os.path.dirname(__file__),"v11_live.py")).read_text(encoding="utf-8")
        self.assertIn("_ROUTE_STALL_SEC",src); self.assertIn("application data stalled",src)

if __name__=="__main__": unittest.main()
