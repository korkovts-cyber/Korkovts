import ast,unittest
from pathlib import Path
class V11227Contracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11227_stability_core.py").read_text(); cls.e=Path("bot_v11227.py").read_text(); cls.r=Path("railway.toml").read_text(); ast.parse(cls.o); ast.parse(cls.e)
 def test_no_hidden_22_in_new_deep(self):
  s=self.o[self.o.index("async def deep_one_v11227"):self.o.index("futures._deep_one=deep_one_v11227")]
  self.assertIn("timeout=90.0",s); self.assertNotIn("timeout=22",s)
 def test_priority(self):
  self.assertIn("_scan_context",self.o); self.assertIn("BACKGROUND_RPS_DURING_SCAN=0.8",self.o)
 def test_cooldown(self):
  self.assertIn("0<cooldown<=180",self.o); self.assertIn("cooldown+1.25",self.o)
 def test_limits(self):
  self.assertIn("REQUESTS_PER_SEC=3.2",self.o); self.assertIn("FUTURES_DATA_RPS=1.0",self.o); self.assertIn("WEIGHT_BUDGET_PER_MIN=620",self.o)
 def test_safety(self):
  self.assertIn("FINAL_STRATEGY_REJECT",self.o); self.assertIn("DERIVATIVES_INCOMPLETE",self.o); self.assertNotIn("min_score=0",self.o)
 def test_layers(self):
  order=["i217()","i218()","i219()","i220()","i221()","i222()","i223()","i224()","i225()","i226()","i227()"]
  for a,b in zip(order,order[1:]): self.assertLess(self.e.index(a),self.e.index(b))
 def test_railway(self):
  self.assertIn("python preflight_v11227.py",self.r); self.assertIn("test_v11227_contract.py",self.r); self.assertIn("python bot_v11227.py",self.r)
if __name__=="__main__": unittest.main()
