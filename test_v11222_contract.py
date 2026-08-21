import ast, unittest
from pathlib import Path
class V11222Contracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11222_verification_hardening.py").read_text()
  cls.e=Path("bot_v11222.py").read_text()
  cls.r=Path("railway.toml").read_text()
  ast.parse(cls.o); ast.parse(cls.e)
 def test_binding_is_measured_not_assumed(self):
  self.assertIn("governor.market._get is p221.governed_get_v11221",self.o)
  self.assertIn('state = "BOUND" if _binding_ok() else "ERROR"',self.o)
 def test_startup_fails_closed(self):
  self.assertIn("raise RuntimeError",self.o)
  self.assertIn("startup blocked: final Binance governor is not bound",self.o)
 def test_newline_fix(self):
  self.assertIn('text = text.replace("\\\\n", "\\n")',self.o)
 def test_layer_order(self):
  for a,b in (("i217()","i218()"),("i218()","i219()"),("i219()","i220()"),("i220()","i221()"),("i221()","i222()")):
   self.assertLess(self.e.index(a),self.e.index(b))
 def test_railway(self):
  self.assertIn("python preflight_v11222.py",self.r)
  self.assertIn("test_v11222_contract.py",self.r)
  self.assertTrue(any(x in self.r for x in ("python bot_v11222.py","python bot_v11223.py","python bot_v11224.py")))
if __name__=="__main__": unittest.main()
