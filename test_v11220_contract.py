import ast, unittest
from pathlib import Path

class V11220Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o=Path("v11220_deep_audit.py").read_text(encoding="utf-8")
        cls.e=Path("bot_v11220.py").read_text(encoding="utf-8")
        cls.r=Path("railway.toml").read_text(encoding="utf-8")
        ast.parse(cls.o); ast.parse(cls.e)

    def test_full_relief_alignment(self):
        self.assertIn("min(before_score, 78.0)", self.o)
        self.assertIn("min(before_rp, 70.0)", self.o)
        self.assertIn("evidence_still_mandatory", self.o)

    def test_broad_watch_does_not_erase_ready_one(self):
        self.assertIn('incoming == "WATCH"', self.o)
        self.assertIn('int(before.get("confirm_streak") or 0) == 1', self.o)
        self.assertIn("confirm_streak=1", self.o)

    def test_geometry_guard(self):
        self.assertIn("_materially_same_geometry", self.o)
        self.assertIn("overlap_ratio", self.o)
        self.assertIn("width_ok", self.o)

    def test_no_synthetic_second_confirmation(self):
        self.assertIn("Never create or preserve 2/2", self.o)
        self.assertNotIn("confirm_streak=2", self.o)

    def test_layer_order(self):
        self.assertLess(self.e.index("i217()"), self.e.index("i218()"))
        self.assertLess(self.e.index("i218()"), self.e.index("i219()"))
        self.assertLess(self.e.index("i219()"), self.e.index("i220()"))

    def test_railway(self):
        self.assertIn("python preflight_v11220.py", self.r)
        self.assertIn("test_v11220_contract.py", self.r)
        self.assertIn("v11220_deep_audit.py", self.r)
        self.assertTrue(any(x in self.r for x in ("python bot_v11220.py","python bot_v11221.py","python bot_v11222.py","python bot_v11223.py","python bot_v11224.py","python bot_v11226.py")))

if __name__=="__main__":
    unittest.main()
