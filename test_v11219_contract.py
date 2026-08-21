import ast,unittest
from pathlib import Path
class V11219Contracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.o=Path("v11219_trade_engine_audit.py").read_text(); cls.r=Path("railway.toml").read_text(); ast.parse(cls.o)
 def test_relief(self): self.assertIn("buy_ready_threshold_alignment",self.o)
 def test_ready_priority(self): self.assertIn("prioritized_spot_rows",self.o); self.assertIn("spot_orderbook_symbols_v11219",self.o)
 def test_safety(self):
  for x in ("price invalidated before BUY","spot_active_correlation_risk","spot_recheck_watch","record_spot_ready","streak<2","_deliver_spot_pending"): self.assertIn(x,self.o)
 def test_api(self):
  for x in ("3.2","900","700"): self.assertIn(x,self.o)
 def test_railway(self): self.assertIn("python preflight_v11219.py",self.r); self.assertIn("test_v11219_contract.py",self.r); self.assertTrue(any(x in self.r for x in ("python bot_v11219.py","python bot_v11220.py","python bot_v11221.py","python bot_v11222.py")))
if __name__=="__main__": unittest.main()
