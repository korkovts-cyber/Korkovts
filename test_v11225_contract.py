import ast,unittest
from pathlib import Path
class T(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.o=Path("v11225_pipeline_repair.py").read_text(); cls.e=Path("bot_v11225.py").read_text(); cls.r=Path("railway.toml").read_text(); ast.parse(cls.o); ast.parse(cls.e)
 def test_spot(self): self.assertIn("base.spot_scan=spot_scan_independent_v11225",self.o); self.assertIn("DAILY_TIMEOUT_45S",self.o)
 def test_fut(self): self.assertIn("TECHNICAL_RANK_FALLBACK",self.o); self.assertIn("full-deep remains mandatory",self.o)
 def test_adaptive(self): self.assertIn("adaptive=14",self.o); self.assertIn("adaptive=20",self.o)
 def test_railway(self): self.assertTrue(any(x in self.r for x in ("python bot_v11225.py","python bot_v11226.py")))
if __name__=="__main__": unittest.main()
