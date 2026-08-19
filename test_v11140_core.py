from __future__ import annotations
import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_perf(db_path):
    app=types.ModuleType('app'); app.__path__=[]
    cfg=types.ModuleType('app.config'); cfg.DATABASE_PATH=str(db_path)
    sql=types.ModuleType('v1171_sqlite')
    class Session:
        def __init__(self,*a,row_factory=None,**k): self.row_factory=row_factory; self.c=None
        def __enter__(self):
            self.c=sqlite3.connect(str(db_path)); self.c.row_factory=self.row_factory; return self.c
        def __exit__(self,*a): self.c.commit(); self.c.close()
    sql.db_session=Session
    with patch.dict(sys.modules,{'app':app,'app.config':cfg,'v1171_sqlite':sql}):
        name='v11140_performance_tested'
        spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name('v11140_performance.py'))
        mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod)
        return mod

class DecisionMarginTests(unittest.TestCase):
    def test_near_tie_does_not_reorder(self):
        with tempfile.TemporaryDirectory() as td:
            m=load_perf(Path(td)/'x.db')
            a=SimpleNamespace(decision_priority=92.4,professional_rank=92.4,feature_snapshot={})
            b=SimpleNamespace(decision_priority=92.1,professional_rank=92.1,feature_snapshot={})
            out=m.annotate_decision_margin([a,b],[a,b])
            self.assertIs(out[0],a); self.assertEqual(a.decision_margin_label,'NEAR_TIE')
            self.assertAlmostEqual(a.decision_margin,.3,places=6)
    def test_clear_prime(self):
        with tempfile.TemporaryDirectory() as td:
            m=load_perf(Path(td)/'x.db')
            a=SimpleNamespace(decision_priority=95,professional_rank=95,feature_snapshot={})
            b=SimpleNamespace(decision_priority=90,professional_rank=90,feature_snapshot={})
            m.annotate_decision_margin([a,b],[a,b])
            self.assertEqual(a.decision_margin_label,'CLEAR_PRIME')

class PerformanceTests(unittest.TestCase):
    def _db(self,path):
        c=sqlite3.connect(path)
        c.execute('''CREATE TABLE signals(id INTEGER PRIMARY KEY,created_at TEXT,closed_at TEXT,status TEXT,activated_at TEXT,
            is_shadow INTEGER,delivery_state TEXT,result TEXT,pnl_r REAL,symbol TEXT,timeframe TEXT,side TEXT,setup_type TEXT,
            max_favorable_r REAL,max_adverse_r REAL)''')
        return c
    def test_filters_non_production_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'x.db'; c=self._db(path)
            rows=[
                ('CLOSED','x',0,'DELIVERED','TP2',1.0),
                ('CLOSED',None,0,'DELIVERED','TP2',5.0),
                ('CLOSED','x',1,'DELIVERED','TP2',5.0),
                ('CLOSED','x',0,'DELIVERED','AMBIGUOUS_SL_TP',5.0),
            ]
            for i,(st,act,sh,ds,res,pnl) in enumerate(rows,1):
                c.execute('INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (i,'2026-01-01','2026-01-02',st,act,sh,ds,res,pnl,'BTCUSDT','1H','LONG','TEST',1.2,-.4))
            c.commit(); c.close(); m=load_perf(path)
            self.assertEqual(m.snapshot()[100].closed,1)
            self.assertAlmostEqual(m.snapshot()[100].net_r,1.0)
    def test_drawdown_and_pf(self):
        with tempfile.TemporaryDirectory() as td:
            m=load_perf(Path(td)/'x.db')
            p=m.summarize([{'pnl_r':1.0},{'pnl_r':-0.5},{'pnl_r':1.5},{'pnl_r':-1.0}])
            self.assertEqual(p.closed,4); self.assertAlmostEqual(p.net_r,1.0)
            self.assertGreater(p.profit_factor,1.0); self.assertGreaterEqual(p.max_drawdown_r,1.0)
    def test_report_empty_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'x.db'; c=self._db(path); c.close(); m=load_perf(path)
            self.assertIn('Пока нет закрытых',m.report_text())

if __name__=='__main__': unittest.main()
