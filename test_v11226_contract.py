import ast,unittest
from pathlib import Path
class V11226Contracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11226_stable_deep_engine.py").read_text()
  cls.e=Path("bot_v11226.py").read_text()
  cls.r=Path("railway.toml").read_text()
  ast.parse(cls.o); ast.parse(cls.e)
 def test_queue_safe_deep(self):
  self.assertIn("async with sem:",self.o)
  self.assertIn("timeout=65.0",self.o)
  self.assertIn("_noop_sem",self.o)
 def test_api_burst_control(self):
  self.assertIn("FUTURES_DATA_RPS = 1.6",self.o)
  self.assertIn("_snapshot_inflight",self.o)
  self.assertIn("_snapshot_cache",self.o)
 def test_truthful_verification(self):
  self.assertIn('"DEEP_CANDIDATE_TIMEOUT"',self.o)
  self.assertIn("deep_verification_v11226",self.o)
  self.assertIn("_reset_deep_stats",self.o)
 def test_capacity(self):
  self.assertIn("DEEP_CONCURRENCY = 3",self.o)
  self.assertIn("540",self.o)
 def test_layers(self):
  order=["i217()","i218()","i219()","i220()","i221()","i222()","i223()","i224()","i225()","i226()"]
  for a,b in zip(order,order[1:]): self.assertLess(self.e.index(a),self.e.index(b))
 def test_railway(self):
  self.assertIn("python preflight_v11226.py",self.r)
  self.assertIn("test_v11226_contract.py",self.r)
  self.assertTrue(any(x in self.r for x in ("python bot_v11226.py","python bot_v11227.py")))
if __name__=="__main__": unittest.main()
