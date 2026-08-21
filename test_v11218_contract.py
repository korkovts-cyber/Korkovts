import ast
import unittest
from pathlib import Path

class V11218Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Path("v11218_spot_entry_fix.py").read_text(encoding="utf-8")
        cls.entry = Path("bot_v11218.py").read_text(encoding="utf-8")
        cls.railway = Path("railway.toml").read_text(encoding="utf-8")
        ast.parse(cls.s)
        ast.parse(cls.entry)

    def test_exact_float_reset_is_neutralized(self):
        self.assertIn("_same_spot_setup", self.s)
        self.assertIn("prior_streak", self.s)
        self.assertIn("spot_watch.upsert = upsert_spot_watch_v11218", self.s)
        self.assertIn("base.upsert_spot_watch = upsert_spot_watch_v11218", self.s)

    def test_safety_gates_remain(self):
        self.assertIn("price invalidated before BUY", self.s)
        self.assertIn("spot_active_correlation_risk", self.s)
        self.assertIn("active_count >= 2", self.s)
        self.assertIn('!= "BUY"', self.s)
        self.assertIn("streak < 2", self.s)
        self.assertIn("_deliver_spot_pending", self.s)

    def test_near_zone_is_recheck_only(self):
        self.assertIn("_near_original_zone", self.s)
        self.assertIn("fresh full revalidation still WATCH", self.s)

    def test_final_health_version(self):
        self.assertIn("health_text_v11218", self.s)
        self.assertIn('replace("V11.21.7", "V11.21.8")', self.s)

    def test_railway_executes_final_checks(self):
        self.assertIn("python preflight_v11218.py", self.railway)
        self.assertIn("test_v11218_contract.py", self.railway)
        self.assertIn("v11218_spot_entry_fix.py", self.railway)
        self.assertTrue(any(x in self.railway for x in ("python bot_v11218.py","python bot_v11219.py","python bot_v11220.py","python bot_v11221.py","python bot_v11222.py")))

    def test_entrypoint_layers_217_then_218(self):
        self.assertIn("install_v11217()", self.entry)
        self.assertIn("install_v11218()", self.entry)
        self.assertLess(self.entry.index("install_v11217()"), self.entry.index("install_v11218()"))

if __name__ == "__main__":
    unittest.main()
