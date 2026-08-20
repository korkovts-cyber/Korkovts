import ast
import unittest
from pathlib import Path

class V11217Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Path("v11217_reliability.py").read_text(encoding="utf-8")
        cls.entry = Path("bot_v11217.py").read_text(encoding="utf-8")
        ast.parse(cls.s)
        ast.parse(cls.entry)

    def test_production_pipeline_not_bypassed(self):
        self.assertIn("base._raw_scan = raw_scan_v11217", self.s)
        self.assertNotIn("base.core.scan = scan_v11217", self.s)
        self.assertNotIn("base.core.scan=scan_v11217", self.s)

    def test_futures_reliability(self):
        self.assertIn("futures.DEEP_CONCURRENCY = 1", self.s)
        self.assertIn("_wait_for_deep_window", self.s)
        self.assertIn("deep verification coverage incomplete", self.s)

    def test_spot_auto(self):
        self.assertIn("base.SPOT_AUTO_INTERVAL_MIN = 15", self.s)
        self.assertIn("base.SPOT_WATCH_INTERVAL_MIN = 1", self.s)
        self.assertIn("spot_auto_job_v11217", self.s)
        self.assertIn("spot_watch_job_v11217", self.s)
        self.assertIn("v11217-spot-auto-bootstrap", self.s)

    def test_entrypoint(self):
        self.assertIn("runtime.base.core.main()", self.entry)

if __name__ == "__main__":
    unittest.main()
