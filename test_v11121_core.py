"""V11.12.1 control-audit tests; no external network calls."""
from __future__ import annotations
import sys, types, unittest
from pathlib import Path
from unittest.mock import patch

if "app" not in sys.modules:
    app=types.ModuleType("app"); app.__path__=[]; sys.modules["app"]=app
if "app.db" not in sys.modules:
    db=types.ModuleType("app.db"); db.open_signals=lambda: []; sys.modules["app.db"]=db

import v11_live

class BookTickerResilienceTests(unittest.TestCase):
    def setUp(self):
        v11_live._books.clear()
    def test_timestamp_less_bookticker_uses_receive_time(self):
        with patch("v11_live.time.time",return_value=2000.0):
            v11_live._handle({"data":{"e":"bookTicker","s":"TESTUSDT","u":1,"b":"99.9","B":"2","a":"100.1","A":"3"}})
            row=v11_live.book("TESTUSDT",3)
        self.assertIsNotNone(row)
        self.assertEqual(row["timestamp_source"],"recv")
        self.assertEqual(row["event_ts"],2000.0)
    def test_receive_time_quote_still_expires(self):
        with patch("v11_live.time.time",return_value=2000.0):
            v11_live._handle({"data":{"e":"bookTicker","s":"TESTUSDT","u":1,"b":"99.9","B":"2","a":"100.1","A":"3"}})
        with patch("v11_live.time.time",return_value=2004.1):
            self.assertIsNone(v11_live.book("TESTUSDT",3))
    def test_malformed_bookticker_never_enters_cache(self):
        with patch("v11_live.time.time",return_value=2000.0):
            v11_live._handle({"data":{"e":"bookTicker","s":"TESTUSDT","u":1,"b":"nan","a":"100.1"}})
        self.assertNotIn("TESTUSDT",v11_live._books)

class RuntimePresentationTests(unittest.TestCase):
    def test_ui_version_is_current(self):
        src=Path(__file__).with_name("bot_v11121.py").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION="11.12.1"',src)
        self.assertIn("YK CONTROL CENTER · V11.12.1",src)
        self.assertNotIn("YK CONTROL CENTER · V11.10.0",src)

if __name__=="__main__": unittest.main(verbosity=2)
