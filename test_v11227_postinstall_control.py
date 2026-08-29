import ast,unittest
from pathlib import Path
class V11227PostInstallControl(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11227_stability_core.py").read_text()
  ast.parse(cls.o)
 def test_final_alias_rebind(self):
  self.assertIn("_rebind_scheduler_aliases_v11227()",self.o)
  self.assertIn("governor.governed_get=governed_get_v11227",self.o)
  for name in ("v112_health","v11_liquidity","v112_alpha","v1141_integrity","v11197_sources"):
   self.assertIn(f'"{name}"',self.o)
if __name__=="__main__": unittest.main()
