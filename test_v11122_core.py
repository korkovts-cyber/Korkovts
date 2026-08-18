from __future__ import annotations
import unittest
from datetime import datetime, timezone
from pathlib import Path
from v11122_auto import choose_radar_symbols, heartbeat_text, ticker_snapshot, AUTO_FULL_SCAN_MIN

class AutoPulseLogicTests(unittest.TestCase):
    def test_full_scan_cadence_is_ten_minutes(self): self.assertEqual(AUTO_FULL_SCAN_MIN,10)
    def test_ticker_snapshot_drops_nonfinite(self):
        out=ticker_snapshot({'AUSDT':{'price':'nan','quote_volume':1},'BUSDT':{'price':2,'quote_volume':3}})
        self.assertNotIn('AUSDT',out); self.assertIn('BUSDT',out)
    def test_near_candidate_has_priority(self):
        t={'NEARUSDT':{'price':10,'quote_volume':20_000_000},'MOVEUSDT':{'price':11,'quote_volume':20_500_000}}
        prev={'NEARUSDT':(10,19_000_000),'MOVEUSDT':(10,20_000_000)}
        picks,_=choose_radar_symbols(t,prev,near_symbols=['NEARUSDT'],max_candidates=1)
        self.assertEqual(picks[0].symbol,'NEARUSDT')
    def test_new_impulse_keeps_one_slot_with_multiple_near_candidates(self):
        t={
            'N1USDT':{'price':10,'quote_volume':20_000_000},
            'N2USDT':{'price':10,'quote_volume':20_000_000},
            'MOVEUSDT':{'price':10.6,'quote_volume':21_000_000},
        }
        prev={
            'N1USDT':(10,19_900_000),'N2USDT':(10,19_900_000),
            'MOVEUSDT':(10,20_000_000),
        }
        picks,_=choose_radar_symbols(t,prev,near_symbols=['N1USDT','N2USDT'],max_candidates=2)
        self.assertEqual(picks[0].source,'near_candidate')
        self.assertEqual(picks[1].symbol,'MOVEUSDT')

    def test_active_symbol_is_excluded(self):
        t={'AUSDT':{'price':11,'quote_volume':20_500_000}}
        picks,_=choose_radar_symbols(t,{'AUSDT':(10,20_000_000)},active_symbols=['AUSDT'])
        self.assertEqual(picks,[])
    def test_impulse_is_selected(self):
        t={'AUSDT':{'price':10.1,'quote_volume':20_500_000}}
        picks,_=choose_radar_symbols(t,{'AUSDT':(10,20_000_000)})
        self.assertEqual(picks[0].symbol,'AUSDT')
    def test_heartbeat_explicitly_says_no_signal(self):
        text=heartbeat_text({'liquid':100,'prefiltered':4,'deep_checked':3,'final':0},now=datetime(2026,1,1,tzinfo=timezone.utc))
        self.assertIn('СИГНАЛОВ ДЛЯ ВХОДА НЕТ',text); self.assertIn('каждые 10 минут',text)
    def test_heartbeat_reports_immediate_trigger(self):
        text=heartbeat_text({},triggered=1)
        self.assertIn('ENTRY NOW ОТПРАВЛЕН: 1',text); self.assertIn('без ожидания',text)
    def test_heartbeat_reports_scan_error(self):
        text=heartbeat_text({},scan_error='boom')
        self.assertIn('СКАН НЕ ЗАВЕРШЁН',text); self.assertIn('boom',text)

class RuntimeSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.src=Path(__file__).with_name('bot_v11122.py').read_text(encoding='utf-8')
    def test_version_current(self): self.assertIn('APP_VERSION="11.12.2"',self.src)
    def test_auto_interval_pinned(self): self.assertIn('core.AUTO_SCAN_INTERVAL_MIN=AUTO_FULL_SCAN_MIN',self.src)
    def test_heartbeat_is_main_cycle_only(self): self.assertIn('heartbeat=str(label).upper()=="1H"',self.src)
    def test_fast_radar_every_60s(self): self.assertIn('fast_radar_job,interval=FAST_RADAR_INTERVAL_SEC',self.src)
    def test_entry_monitor_still_30s(self): self.assertIn('entry_now_monitor_job,interval=30',self.src)
    def test_fast_radar_uses_full_prepare(self): self.assertIn('prepared=await _prepare([result],"main")',self.src)
    def test_fast_radar_never_direct_sends(self): self.assertNotIn('await context.bot.send_message', self.src[self.src.index('async def fast_radar_job'):self.src.index('async def news_status_v114')])

if __name__=='__main__': unittest.main(verbosity=2)
