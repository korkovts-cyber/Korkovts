import ast,unittest
from pathlib import Path
class V11223Contracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11223_end_to_end_repair.py").read_text(); cls.e=Path("bot_v11223.py").read_text(); cls.r=Path("railway.toml").read_text(); ast.parse(cls.o); ast.parse(cls.e)
 def test_futures(self):
  self.assertIn("data_arch.REQUESTS_PER_SEC=4.0",self.o); self.assertIn("320",self.o); self.assertIn("MIN_FRAME_COVERAGE",self.o)
 def test_spot(self):
  self.assertIn("SPOT_DEEP_SHORTLIST=24",self.o); self.assertIn("SPOT_WEIGHT_BUDGET=900",self.o); self.assertIn("SPOT_RPS=5.0",self.o)
 def test_manual(self):
  self.assertIn("timeout=210.0",self.o); self.assertIn("FUTURES SCAN НЕ ЗАВЕРШЁН",self.o); self.assertIn("SPOT SCAN НЕ ЗАВЕРШЁН",self.o)
 def test_layers(self):
  order=["i217()","i218()","i219()","i220()","i221()","i222()","i223()"]
  for a,b in zip(order,order[1:]): self.assertLess(self.e.index(a),self.e.index(b))
 def test_railway(self):
  self.assertIn("python preflight_v11223.py",self.r); self.assertIn("test_v11223_contract.py",self.r); self.assertTrue(any(x in self.r for x in ("python bot_v11223.py","python bot_v11224.py")))
if __name__=="__main__": unittest.main()
