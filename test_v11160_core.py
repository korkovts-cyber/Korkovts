import unittest
from types import SimpleNamespace
from v11160_adaptive import assess_stats
from v11160_replay import _safe,fingerprint

class AdaptiveEdgeTests(unittest.TestCase):
    def test_small_sample_never_blocks(self):
        d=assess_stats([-1]*10,5)
        self.assertEqual(d.label,'LEARNING'); self.assertTrue(d.eligible)
    def test_mature_positive_clear(self):
        d=assess_stats([.5,-.2,.4,-.1]*10,20)
        self.assertEqual(d.label,'CLEAR'); self.assertTrue(d.eligible)
    def test_mature_persistent_negative_degrades(self):
        d=assess_stats([.2,-.6]*12,18)
        self.assertEqual(d.label,'DEGRADED'); self.assertFalse(d.eligible)
    def test_severe_mature_negative_quarantines(self):
        d=assess_stats([.1,-.8]*20,30)
        self.assertEqual(d.label,'QUARANTINE'); self.assertFalse(d.eligible)
    def test_recent_decay_can_block(self):
        d=assess_stats([.4]*20+[-.2]*20,25)
        self.assertFalse(d.eligible); self.assertLessEqual(d.decay_r,-.25)

class ReplayTests(unittest.TestCase):
    def test_safe_removes_secret_keys(self):
        x=_safe({'token':'abc','nested':{'api_key':'x','ok':1}})
        self.assertNotIn('token',x); self.assertNotIn('api_key',x['nested']); self.assertEqual(x['nested']['ok'],1)
    def test_fingerprint_is_deterministic(self):
        self.assertEqual(fingerprint({'a':1,'b':2}),fingerprint({'b':2,'a':1}))
    def test_nonfinite_is_serializable(self):
        x=_safe({'x':float('nan')}); self.assertIsInstance(x['x'],str)

class RuntimeContracts(unittest.TestCase):
    def test_adaptive_gate_before_portfolio_selection_and_entry(self):
        from pathlib import Path
        s=Path('bot_v11160.py').read_text()
        self.assertIn('ADAPTIVE_EDGE_REJECT',s)
        self.assertIn('not _adaptive_gate(signal)[0]',s)
        self.assertIn('not _adaptive_gate(fresh)[0]',s)
    def test_replay_before_delivery(self):
        from pathlib import Path
        s=Path('bot_v11160.py').read_text()
        self.assertLess(s.index('record_v11160_replay(signal_id'),s.index('payload=core.fmt(fresh,priority_label)'))
    def test_new_callbacks_are_acknowledged(self):
        from pathlib import Path
        s=Path('bot_v11160.py').read_text()
        self.assertIn('data=="v1116:adaptive"',s); self.assertIn('data=="v1116:replay"',s)

if __name__=='__main__':unittest.main()
