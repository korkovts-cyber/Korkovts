"""Deterministic V11.11 tests; no external network calls."""
from __future__ import annotations
import importlib, os, sys, tempfile, types, unittest, time
from pathlib import Path

# Stub only the app.market dependency needed to import the pure LocalBook code.
if "app" not in sys.modules:
    app=types.ModuleType("app"); app.__path__=[]; sys.modules["app"]=app
if "app.market" not in sys.modules:
    market=types.ModuleType("app.market")
    async def _get(*a,**k): raise RuntimeError("network disabled in core test")
    market._get=_get; sys.modules["app.market"]=market
if "app.db" not in sys.modules:
    db=types.ModuleType("app.db")
    db.open_signals=lambda: []
    sys.modules["app.db"]=db

import v11110_futures_orderbook as ob
from v11110_contract import evaluate_entry_contract
import v11110_tape as tape
import v11110_lease as lease
from v11110_replay import reconstruct_depth


class RoutedWebsocketContractTests(unittest.TestCase):
    def test_binance_futures_routes_are_split(self):
        src=Path(__file__).with_name("v11_live.py").read_text(encoding="utf-8")
        self.assertIn("/public/stream?streams=",src)
        self.assertIn("/market/stream?streams=",src)
        self.assertIn("@bookTicker",src)
        self.assertIn("@aggTrade",src)
        self.assertIn('_route_monitor("public")',src)
        self.assertIn('_route_monitor("market")',src)
        self.assertNotIn('wss://fstream.binance.com/stream?streams=',src)

    def test_route_builders_do_not_mix_stream_classes(self):
        import v11_live
        pub=v11_live._public_url(("BTCUSDT","ETHUSDT"))
        market=v11_live._market_url(("BTCUSDT","ETHUSDT"))
        self.assertIn("/public/stream?streams=",pub)
        self.assertIn("btcusdt@bookTicker",pub)
        self.assertNotIn("aggTrade",pub)
        self.assertIn("/market/stream?streams=",market)
        self.assertIn("btcusdt@aggTrade",market)
        self.assertNotIn("bookTicker",market)

    def test_live_handle_keeps_aggtrade_flow_contract(self):
        from unittest.mock import patch
        import v11_live
        v11_live._trade_flow.clear()
        with patch("v11_live.time.time",return_value=2000.0):
            now_ms=2_000_000
            v11_live._handle({"data":{"e":"aggTrade","E":now_ms,"T":now_ms,"s":"TESTUSDT","p":"100","q":"2","m":False}})
            v11_live._handle({"data":{"e":"aggTrade","E":now_ms,"T":now_ms,"s":"TESTUSDT","p":"100","q":"1","m":True}})
            flow=v11_live.flow("TESTUSDT",60,20)
        self.assertIsNotNone(flow)
        self.assertAlmostEqual(flow["buy_share"],2/3,places=6)

class FuturesL2Tests(unittest.TestCase):
    def snapshot(self):
        return {"lastUpdateId":100,"bids":[["99","2"],["98","3"]],"asks":[["101","2"],["102","3"]],"fetched_at":time.time()}
    def test_snapshot_bridge_then_pu_continuity(self):
        b=ob.LocalBook("X"); b.load_snapshot(self.snapshot())
        self.assertEqual(b.apply_event({"U":99,"u":101,"pu":98,"E":int(time.time()*1000),"b":[["99","4"]],"a":[]},first_after_snapshot=True),"APPLIED")
        self.assertEqual(b.last_update_id,101)
        self.assertEqual(b.apply_event({"U":102,"u":103,"pu":101,"E":int(time.time()*1000),"b":[],"a":[["101","1"]]}),"APPLIED")
        self.assertEqual(b.last_update_id,103)
    def test_first_event_after_snapshot_can_bridge_later(self):
        b=ob.LocalBook("X"); b.load_snapshot(self.snapshot())
        self.assertTrue(b.bridge_pending)
        # An old event is ignored while the bridge remains pending.
        self.assertEqual(b.apply_event({"U":90,"u":99,"pu":89,"E":int(time.time()*1000),"b":[],"a":[]},first_after_snapshot=b.bridge_pending),"IGNORED")
        self.assertTrue(b.bridge_pending)
        # The first applicable event is validated against the snapshot range.
        self.assertEqual(b.apply_event({"U":100,"u":102,"pu":99,"E":int(time.time()*1000),"b":[],"a":[]},first_after_snapshot=b.bridge_pending),"APPLIED")
        self.assertFalse(b.bridge_pending)

    def test_pu_gap_fails_closed(self):
        b=ob.LocalBook("X"); b.load_snapshot(self.snapshot())
        b.apply_event({"U":100,"u":101,"pu":99,"E":int(time.time()*1000),"b":[],"a":[]},first_after_snapshot=True)
        self.assertEqual(b.apply_event({"U":102,"u":103,"pu":77,"E":int(time.time()*1000),"b":[],"a":[]}),"GAP")
        self.assertFalse(b.synced)
    def test_replay_reconstructs_sequence(self):
        now=time.time(); bundle={"symbol":"X","events":[
            {"kind":"depth_snapshot","recv_ts":now,"payload":self.snapshot()},
            {"kind":"depth","recv_ts":now+.1,"payload":{"U":100,"u":101,"pu":99,"E":int(now*1000),"b":[["99","4"]],"a":[]}},
            {"kind":"depth","recv_ts":now+.2,"payload":{"U":102,"u":103,"pu":101,"E":int(now*1000),"b":[],"a":[["101","1"]]}},
        ]}
        r=reconstruct_depth(bundle,ob.LocalBook)
        self.assertTrue(r["ok"]); self.assertEqual(r["last_update_id"],103)
    def test_replay_local_anchor_starts_after_anchor(self):
        now=time.time(); anchor=self.snapshot(); anchor["source"]="local_anchor"
        anchor["history"]=[[now-2,1.0,1000,900,.05],[now-1,1.1,1100,950,.07]]
        bundle={"symbol":"X","events":[
            {"kind":"depth","recv_ts":now-.2,"payload":{"U":50,"u":60,"pu":49,"E":int((now-.2)*1000),"b":[],"a":[]}},
            {"kind":"depth_snapshot","recv_ts":now,"payload":anchor},
            {"kind":"depth","recv_ts":now+.1,"payload":{"U":101,"u":101,"pu":100,"E":int((now+.1)*1000),"b":[["99","4"]],"a":[]}},
        ]}
        r=reconstruct_depth(bundle,ob.LocalBook)
        self.assertTrue(r["ok"]); self.assertEqual(r["last_update_id"],101)
        self.assertEqual(r["anchor_source"],"local_anchor")
        self.assertGreaterEqual(r["history_samples"],2)
    def test_periodic_local_anchor_carries_stability_history(self):
        b=ob.LocalBook("X"); b.load_snapshot(self.snapshot()); b.bridge_pending=False
        now=time.time(); b.last_update_id=123; b.last_tape_anchor_ts=now-60
        b.history.extend([(now-2,1.0,1000,900,.05),(now-1,1.1,1100,950,.07)])
        captured=[]; old=ob._record_tape
        try:
            ob._record_tape=lambda symbol,kind,payload,**kwargs: captured.append((symbol,kind,payload))
            ob._record_local_anchor(b,now)
        finally:
            ob._record_tape=old
        self.assertEqual(len(captured),1); payload=captured[0][2]
        self.assertEqual(payload["source"],"local_anchor")
        self.assertEqual(payload["lastUpdateId"],123)
        self.assertEqual(len(payload["history"]),2)

class ContractTests(unittest.TestCase):
    def test_warmup_cannot_confirm_entry(self):
        r=evaluate_entry_contract("LONG",{"sequence_synced":True,"healthy":True},{"samples":3,"coverage_sec":1,"stability_score":90})
        self.assertFalse(r.ok)
    def test_stable_support_can_pass(self):
        l2={"sequence_synced":True,"healthy":True}
        st={"samples":12,"coverage_sec":8,"stability_score":82,"last_gap_age_sec":90,"median_imbalance_20bps":.1,"bid_replenishment_ratio":.7,"ask_replenishment_ratio":.7}
        self.assertTrue(evaluate_entry_contract("LONG",l2,st).ok)
        self.assertTrue(evaluate_entry_contract("SHORT",l2,st).ok)
    def test_directional_opposition_vetoes(self):
        l2={"sequence_synced":True,"healthy":True}
        st={"samples":12,"coverage_sec":8,"stability_score":82,"last_gap_age_sec":90,"median_imbalance_20bps":-.5,"bid_replenishment_ratio":.7,"ask_replenishment_ratio":.7}
        self.assertFalse(evaluate_entry_contract("LONG",l2,st).ok)

    def test_crossfeed_stale_bookticker_vetoes(self):
        l2={"sequence_synced":True,"healthy":True,"best_bid":100.0,"best_ask":100.1}
        st={"samples":12,"coverage_sec":8,"stability_score":82,"last_gap_age_sec":90,"median_imbalance_20bps":.1,"bid_replenishment_ratio":.7,"ask_replenishment_ratio":.7}
        quote={"bid":100.0,"ask":100.1,"ts":1000.0}
        r=evaluate_entry_contract("LONG",l2,st,quote=quote,now=1004.1)
        self.assertFalse(r.ok); self.assertIn("stale",r.reason)

    def test_crossfeed_divergence_vetoes(self):
        l2={"sequence_synced":True,"healthy":True,"best_bid":100.0,"best_ask":100.1}
        st={"samples":12,"coverage_sec":8,"stability_score":82,"last_gap_age_sec":90,"median_imbalance_20bps":.1,"bid_replenishment_ratio":.7,"ask_replenishment_ratio":.7}
        quote={"bid":100.3,"ask":100.4,"ts":1000.0}
        r=evaluate_entry_contract("LONG",l2,st,quote=quote,now=1001.0)
        self.assertFalse(r.ok); self.assertIn("diverged",r.reason)

    def test_crossfeed_coherent_top_can_pass(self):
        l2={"sequence_synced":True,"healthy":True,"best_bid":100.0,"best_ask":100.1}
        st={"samples":12,"coverage_sec":8,"stability_score":82,"last_gap_age_sec":90,"median_imbalance_20bps":.1,"bid_replenishment_ratio":.7,"ask_replenishment_ratio":.7}
        quote={"bid":100.0,"ask":100.1,"ts":1000.0}
        self.assertTrue(evaluate_entry_contract("LONG",l2,st,quote=quote,now=1001.0).ok)


class EntryWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_is_vetoed_when_l2_unavailable(self):
        import dataclasses
        old_base=sys.modules.get("v1142_entry_now")
        old_wrap=sys.modules.pop("v11110_entry_now",None)
        fake=types.ModuleType("v1142_entry_now")
        @dataclasses.dataclass(frozen=True)
        class A:
            state:str; score:float; reason:str
        fake.TriggerAssessment=A
        async def get_klines(*a,**k): return [1]
        fake.get_klines=get_klines
        fake.live_price=lambda *a,**k: 100.0
        fake.live_book=lambda *a,**k: {"bid":99.9,"ask":100.1}
        fake.live_flow=lambda *a,**k: {"buy_share":.6}
        fake.evaluate=lambda *a,**k: A("READY",90.0,"base ready")
        fake.row_from_signal=lambda s: {"symbol":"BTCUSDT","side":"LONG"}
        for name in ("init","arm","active_rows","active_symbols","get_row","record_check","mark_pending_delivery","mark_delivery_uncertain","mark_triggered","mark_shadowed","cancel","status_text"):
            setattr(fake,name,lambda *a,**k: None)
        sys.modules["v1142_entry_now"]=fake
        try:
            wrap=importlib.import_module("v11110_entry_now")
            wrap.futures_l2_snapshot=lambda *a,**k: None
            wrap.futures_l2_stability=lambda *a,**k: {"healthy":False}
            wrap.capture_decision=lambda *a,**k: None
            out=await wrap.assess_row({"symbol":"BTCUSDT","side":"LONG","last_state":"WAIT"})
            self.assertEqual(out.state,"WAIT")
            self.assertIn("L2",out.reason)
        finally:
            sys.modules.pop("v11110_entry_now",None)
            if old_wrap is not None: sys.modules["v11110_entry_now"]=old_wrap
            if old_base is not None: sys.modules["v1142_entry_now"]=old_base
            else: sys.modules.pop("v1142_entry_now",None)

class TapeAndLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=os.path.join(self.tmp.name,"x.db")
    def tearDown(self): self.tmp.cleanup()
    def test_tape_redacts_secrets_and_roundtrips(self):
        tape._DB_PATH=self.db; tape._buffers.clear(); tape._last_capture.clear()
        tape.record_event("BTCUSDT","x",{"TELEGRAM_BOT_TOKEN":"secret","p":"1"})
        event=tape.recent("BTCUSDT",10)[0]
        self.assertEqual(event["payload"]["TELEGRAM_BOT_TOKEN"],"<redacted>")
        aid=types.SimpleNamespace(state="WAIT",score=50,reason="x")
        bundle_id=tape.capture_decision({"symbol":"BTCUSDT","id":1,"side":"LONG"},aid,quote={"bid":99,"ask":101,"price":100.25},force=True)
        loaded=tape.load_bundle(bundle_id)
        self.assertIsNotNone(bundle_id); self.assertEqual(loaded["symbol"],"BTCUSDT")
        self.assertAlmostEqual(float(loaded["quote"]["price"]),100.25)
    def test_singleton_lease_excludes_second_owner(self):
        lease.DB_PATH=self.db; old=lease.OWNER
        try:
            now=time.time()
            lease.OWNER="owner-a"; self.assertTrue(lease.acquire(now=now))
            lease.OWNER="owner-b"; self.assertFalse(lease.acquire(now=now+1))
            # Bounded handover wait must not steal a still-live lease.
            self.assertFalse(lease.acquire_with_wait(timeout_sec=0,poll_sec=.1))
            self.assertTrue(lease.acquire(now=now+lease.TTL_SEC+1))
        finally: lease.OWNER=old

if __name__=="__main__": unittest.main()
