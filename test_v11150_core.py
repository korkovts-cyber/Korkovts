import unittest
from types import SimpleNamespace
from v11150_strong import assess, annotate


def sig(rank=90,support=6,conflicts=0,hard=None,margin="CLEAR_PRIME",stability="STABLE",edge="INSUFFICIENT"):
    return SimpleNamespace(
        professional_rank=rank,evidence_support=support,evidence_conflicts=conflicts,
        expected_net_r_lcb=None,edge_sample_n=0,edge_block_days=0,
        execution_freshness_penalty=.2,live_cost_penalty=.1,pipeline_latency_penalty=0,
        selection_stability_label=stability,decision_margin_label=margin,decision_margin=3.5,
        feature_snapshot={
            "evidence_v117":{"support":support,"conflict":conflicts,"hard_conflicts":hard or []},
            "execution_revalidation":{"freshness_penalty":.2,"pipeline_latency_penalty":0},
            "decision_edge_v11100":{"label":edge,"n":0,"block_days":0},
            "decision_margin_v11140":{"label":margin,"margin_to_best_competitor":3.5},
            "selection_stability_v11100":{"label":stability},
            "meta_v113":{"ready":False},
        }
    )

class StrongGateTests(unittest.TestCase):
    def test_clear_consensus_becomes_prime_strong(self):
        a=assess(sig())
        self.assertTrue(a.auto_eligible); self.assertTrue(a.prime_eligible)
        self.assertEqual(a.label,"PRIME_STRONG")

    def test_low_rank_is_not_auto_eligible(self):
        a=assess(sig(rank=79))
        self.assertFalse(a.auto_eligible)

    def test_hard_evidence_conflict_blocks(self):
        a=assess(sig(hard=["order flow opposite"]))
        self.assertFalse(a.auto_eligible)
        self.assertTrue(a.blockers)

    def test_negative_mature_edge_blocks(self):
        a=assess(sig(edge="WEAK_EDGE"))
        self.assertFalse(a.auto_eligible)

    def test_near_tie_can_be_strong_but_not_prime(self):
        a=assess(sig(margin="NEAR_TIE",stability="GOOD"))
        self.assertTrue(a.auto_eligible)
        self.assertFalse(a.prime_eligible)

    def test_annotation_never_changes_professional_rank(self):
        s=sig(rank=88)
        before=s.professional_rank
        annotate(s)
        self.assertEqual(s.professional_rank,before)
        self.assertIn("strong_consensus_v11150",s.feature_snapshot)

if __name__=="__main__": unittest.main()

class RuntimeSourceTests(unittest.TestCase):
    def test_runtime_applies_strong_gate_before_arm_and_entry(self):
        from pathlib import Path
        src=Path('bot_v11150.py').read_text(encoding='utf-8')
        self.assertIn('strong=assess_strong_signal(signal)',src)
        self.assertIn('if not strong.auto_eligible:',src)
        self.assertIn('priority_label=bool(strong.prime_eligible',src)

    def test_ui_prime_brand_requires_strong_prime(self):
        from pathlib import Path
        src=Path('v11_ui.py').read_text(encoding='utf-8')
        self.assertIn('YK PRIME STRONG',src)
        self.assertIn('strong_prime',src)
        self.assertIn('Strong Consensus',src)
