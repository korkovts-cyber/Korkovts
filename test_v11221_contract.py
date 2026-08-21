import ast,unittest
from pathlib import Path
class V11221Contracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11221_production_reachability.py").read_text(); cls.e=Path("bot_v11221.py").read_text(); cls.r=Path("railway.toml").read_text(); ast.parse(cls.o); ast.parse(cls.e)
 def test_api_binding(self):
  self.assertIn("governor.market._get=governed_get_v11221",self.o); self.assertIn("_reserve_weight",self.o); self.assertIn("WEIGHT_BUDGET_PER_MIN=780",self.o)
 def test_rate_safety(self):
  self.assertIn("_wait_for_shared_cooldown",self.o); self.assertIn("Semaphore(2)",self.o); self.assertIn("2.4",self.o)
 def test_adl(self):
  self.assertIn("actual_adl=='high'",self.o); self.assertIn("recovered.adl_risk='unknown'",self.o); self.assertIn("recovered.leverage=1",self.o)
 def test_diag(self): self.assertIn("strategy_issue_counts",self.o); self.assertIn("Final-strategy blockers",self.o)
 def test_spot(self): self.assertIn("_near_original_zone_v11221",self.o); self.assertIn("mid*.0075",self.o)
 def test_railway(self): self.assertIn("python preflight_v11221.py",self.r); self.assertIn("test_v11221_contract.py",self.r); self.assertTrue(any(x in self.r for x in ("python bot_v11221.py","python bot_v11222.py","python bot_v11223.py","python bot_v11224.py")))
if __name__=="__main__": unittest.main()
