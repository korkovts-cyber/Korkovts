import unittest
from dataclasses import dataclass

from v112_health import classify
from v112_lab import weight_from_groups
from bot_v1121 import combine_rank, _portfolio_select


class V112Tests(unittest.TestCase):
    def test_low_sample_never_adapts(self):
        w,e,z,s=weight_from_groups([1.0]*5,[0.0]*5)
        self.assertEqual(w,1.0)
        self.assertEqual(s,"LEARNING")

    def test_supported_feature_gets_only_modest_boost(self):
        pos=[1.0,1.2,.8,1.1,1.3]*8
        ctrl=[-.2,0,.1,-.1,.2]*8
        w,e,z,s=weight_from_groups(pos,ctrl)
        self.assertGreater(w,1.0)
        self.assertLessEqual(w,1.25)
        self.assertEqual(s,"SUPPORTED")

    def test_harmful_feature_is_not_inverted(self):
        pos=[-.8,-1.0,-.6,-.9,-.7]*8
        ctrl=[.1,.2,0,.3,.1]*8
        w,e,z,s=weight_from_groups(pos,ctrl)
        self.assertGreaterEqual(w,.5)
        self.assertLess(w,1.0)
        self.assertEqual(s,"WEAK")

    def test_alpha_can_reject_borderline_signal(self):
        self.assertIsNone(combine_rank(76,-3,True))

    def test_alpha_cannot_rescue_invalid_base(self):
        self.assertIsNone(combine_rank(90,6,False))

    def test_positive_alpha_can_reorder_valid_signal(self):
        self.assertEqual(combine_rank(85,2,True),87)

    def test_health_rest_failure_pauses(self):
        status,hard,reasons=classify(False,100,10,0,True,1,True,True)
        self.assertTrue(hard)
        self.assertEqual(status,"PAUSE")

    def test_health_stale_candle_pauses(self):
        status,hard,reasons=classify(True,100,240,0,True,1,True,True)
        self.assertTrue(hard)
        self.assertEqual(status,"PAUSE")

    def test_ws_failure_only_degrades_with_good_rest(self):
        status,hard,reasons=classify(True,100,10,0,False,999,True,True)
        self.assertFalse(hard)
        self.assertEqual(status,"DEGRADED")

    def test_large_clock_skew_pauses(self):
        status,hard,reasons=classify(True,100,10,15000,True,1,True,True)
        self.assertTrue(hard)

    def test_database_failure_pauses(self):
        status,hard,reasons=classify(True,100,10,0,True,1,False,True)
        self.assertTrue(hard)
        self.assertEqual(status,"PAUSE")

    def test_nonpersistent_database_only_degrades(self):
        status,hard,reasons=classify(True,100,10,0,True,1,True,False)
        self.assertFalse(hard)
        self.assertEqual(status,"DEGRADED")

    def test_portfolio_selector_respects_explicit_neutral_cap(self):
        class X:
            pass
        rows=[]
        for i in range(5):
            x=X()
            x.professional_rank=95-i
            x.score=95-i
            x.cluster_id=i+1
            x.side="LONG" if i<2 else "SHORT"
            rows.append(x)
        self.assertEqual(len(_portfolio_select(rows,3)),3)


if __name__=="__main__":
    unittest.main()
