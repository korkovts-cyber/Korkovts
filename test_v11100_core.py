"""Focused stdlib/pandas tests for V11.10 competitive-edge modules.

These tests intentionally avoid importing python-telegram-bot so they can run in
a minimal build environment before the full Railway regression suite.
"""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

import pandas as pd

from v11100_data import validate_frame, validate_snapshot
from v11100_edge import _stats
from v11100_protections import assess
from v11100_base_contract import git_blob_sha
from v11100_blackbox import DECISION_RELEASE
from v11100_stability import annotate as annotate_stability
from v11100_policy import payload as policy_payload, contract as policy_contract, fingerprint_from_payload


def _frame(interval_seconds:int, rows:int=40, now:float=1_800_000_000.0):
    # Latest bar opens exactly one interval before now, hence closes at now.
    start=now-interval_seconds*rows
    return pd.DataFrame({
        "open_time":[pd.Timestamp.fromtimestamp(start+i*interval_seconds,tz="UTC") for i in range(rows)],
        "open":[100.0]*rows,"high":[101.0]*rows,"low":[99.0]*rows,
        "close":[100.0]*rows,"volume":[1000.0]*rows,
    })


class DataCoherenceTests(unittest.TestCase):
    def test_fresh_contiguous_snapshot_passes(self):
        now=1_800_000_000.0
        status=validate_snapshot(
            "1H",_frame(900,now=now),_frame(3600,now=now),_frame(14400,now=now),now,
        )
        self.assertTrue(status.eligible)
        self.assertEqual(status.status,"GOOD")
        self.assertTrue(status.observable)

    def test_stale_frame_fails(self):
        now=1_800_000_000.0
        df=_frame(900,now=now-3600)
        status=validate_frame(df,"15m","base",now)
        self.assertFalse(status.ok)
        self.assertIn("stale",status.reason)

    def test_recent_gap_fails(self):
        now=1_800_000_000.0
        df=_frame(300,now=now)
        df=df.drop(index=df.index[-3]).reset_index(drop=True)
        status=validate_frame(df,"5m","lower",now)
        self.assertFalse(status.ok)
        self.assertTrue(status.gap)

    def test_future_timestamp_fails(self):
        now=1_800_000_000.0
        df=_frame(300,now=now+900)
        status=validate_frame(df,"5m","lower",now)
        self.assertFalse(status.ok)
        self.assertTrue(status.future)


class ProtectionTests(unittest.TestCase):
    def _signal(self):
        return SimpleNamespace(
            symbol="ABCUSDT",side="LONG",timeframe="1H",setup_type="CONTINUATION",
            feature_snapshot={},professional_rank=85.0,
        )

    def test_three_recent_pair_losses_lock(self):
        now=1_800_000_000.0
        ts=pd.Timestamp.fromtimestamp(now-3600,tz="UTC").isoformat()
        rows=[
            {"symbol":"ABCUSDT","side":"LONG","timeframe":"1H","setup_type":"CONTINUATION","pnl_r":-.7,"event_time":ts},
            {"symbol":"ABCUSDT","side":"LONG","timeframe":"1H","setup_type":"CONTINUATION","pnl_r":-.6,"event_time":ts},
            {"symbol":"ABCUSDT","side":"LONG","timeframe":"1H","setup_type":"CONTINUATION","pnl_r":-.5,"event_time":ts},
        ]
        d=assess(self._signal(),rows,now)
        self.assertFalse(d.eligible)
        self.assertEqual(d.label,"LOCK")

    def test_positive_history_never_adds_rank(self):
        now=1_800_000_000.0
        rows=[]
        for i in range(30):
            rows.append({
                "symbol":"ABCUSDT","side":"LONG","timeframe":"1H","setup_type":"CONTINUATION",
                "pnl_r":.8,"event_time":pd.Timestamp.fromtimestamp(now-3600*(i+1),tz="UTC").isoformat(),
            })
        d=assess(self._signal(),rows,now)
        self.assertTrue(d.eligible)
        self.assertEqual(d.penalty,0.0)


class EdgeRobustnessTests(unittest.TestCase):
    def test_many_same_day_wins_cannot_promote(self):
        rows=[{"pnl_r":.8,"event_time":"2026-07-01T12:00:00+00:00"} for _ in range(80)]
        e=_stats(rows,"same-day")
        self.assertEqual(e.block_days,1)
        self.assertLessEqual(e.adjustment,0.0)
        self.assertEqual(e.label,"POSITIVE_NOT_DIVERSE")

    def test_diverse_positive_days_can_be_confirmed(self):
        rows=[]
        for day in range(1,21):
            for _ in range(3):
                rows.append({"pnl_r":.8,"event_time":f"2026-07-{day:02d}T12:00:00+00:00"})
        e=_stats(rows,"diverse-positive")
        self.assertGreaterEqual(e.block_days,12)
        self.assertGreater(e.adjustment,0.0)
        self.assertIsNotNone(e.block_p05_r)

    def test_negative_edge_demotes(self):
        rows=[]
        for day in range(1,13):
            for _ in range(2):
                rows.append({"pnl_r":-.5,"event_time":f"2026-07-{day:02d}T12:00:00+00:00"})
        e=_stats(rows,"negative")
        self.assertLess(e.adjustment,0.0)
        self.assertLess(e.lcb90_r,0.0)

    def test_missing_event_times_cannot_fake_positive_diversity(self):
        e=_stats([{"pnl_r":.8} for _ in range(80)],"missing-time")
        self.assertEqual(e.block_days,1)
        self.assertLessEqual(e.adjustment,0.0)


class ReleaseContractTests(unittest.TestCase):
    def test_git_blob_hash_implementation(self):
        # Git's known empty-blob SHA-1.
        self.assertEqual(git_blob_sha(b""),"e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")

    def test_decision_release(self):
        self.assertEqual(DECISION_RELEASE,"11.10.0-competitive-edge")


class SelectionStabilityTests(unittest.TestCase):
    def _s(self,symbol,pro,priority):
        return SimpleNamespace(
            symbol=symbol,side="LONG",timeframe="1H",professional_rank=pro,
            decision_priority=priority,score=pro,estimated_cost_r=.1,feature_snapshot={},
        )

    def test_stability_exposes_close_race_without_changing_order(self):
        a=self._s("AUSDT",90,91.0); b=self._s("BUSDT",89.5,90.8)
        chosen=[a,b]
        out=annotate_stability(chosen,[a,b])
        self.assertIs(out[0],a)
        self.assertEqual(a.selection_stability_label,"FRAGILE")
        self.assertAlmostEqual(a.selection_priority_gap,.2)

    def test_stability_reports_edge_reorder(self):
        base=self._s("AUSDT",92,92.0); edge=self._s("BUSDT",90,93.5)
        out=annotate_stability([edge,base],[base,edge])
        self.assertIs(out[0],edge)
        self.assertFalse(edge.selection_base_consensus)
        self.assertEqual(edge.feature_snapshot["selection_stability_v11100"]["base_pro_top"],"AUSDT")


class PolicyFingerprintTests(unittest.TestCase):
    def test_policy_fingerprint_is_deterministic(self):
        data=policy_payload()
        a=fingerprint_from_payload(data)
        b=policy_contract()["fingerprint"]
        self.assertEqual(a,b)
        self.assertEqual(len(a),64)

    def test_policy_contract_contains_no_secret_fields(self):
        text=str(policy_contract()).lower()
        for forbidden in ("telegram_bot_token","x_bearer_token","api_key","password","secret"):
            self.assertNotIn(forbidden,text)


if __name__=="__main__":
    unittest.main(verbosity=2)
