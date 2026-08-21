import ast,unittest
from pathlib import Path
class V11224Contracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11224_full_deep_repair.py").read_text()
  cls.e=Path("bot_v11224.py").read_text()
  cls.r=Path("railway.toml").read_text()
  ast.parse(cls.o); ast.parse(cls.e)
 def test_throughput(self):
  self.assertIn("ANALYSIS_CONCURRENCY=4",self.o)
  self.assertIn("DEEP_CONCURRENCY=6",self.o)
  self.assertIn("420",self.o)
 def test_candidate_deadline(self):
  self.assertIn("timeout=30.0",self.o)
  self.assertIn("DEEP_CANDIDATE_TIMEOUT",self.o)
 def test_safety(self):
  self.assertNotIn("MIN_FRAME_COVERAGE=.0",self.o)
  self.assertNotIn("FULL_DEEP_TARGET=2",self.o)
 def test_layers(self):
  order=["i217()","i218()","i219()","i220()","i221()","i222()","i223()","i224()"]
  for a,b in zip(order,order[1:]): self.assertLess(self.e.index(a),self.e.index(b))
 def test_railway(self):
  self.assertIn("python preflight_v11224.py",self.r)
  self.assertIn("test_v11224_contract.py",self.r)
  self.assertTrue(any(x in self.r for x in ("python bot_v11224.py","python bot_v11225.py","python bot_v11226.py")))
if __name__=="__main__": unittest.main()
