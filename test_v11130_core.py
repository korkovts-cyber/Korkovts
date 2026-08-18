from __future__ import annotations
import asyncio
import unittest
import time
import importlib.util
import sqlite3
import sys
import tempfile
import types
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from v11130_geometry import material_geometry_change
import v11130_news_intel as ni
from v11122_auto import heartbeat_text

class GeometryResetTests(unittest.TestCase):
    def base(self):
        return dict(side='LONG',entry_low=99.8,entry_high=100.2,stop=98.0,tp1=104.0,tp2=106,tp3=108)
    def test_small_refresh_keeps_streak(self):
        old=self.base(); new=dict(old,entry_low=99.85,entry_high=100.25,tp1=104.05)
        changed,_=material_geometry_change(old,new)
        self.assertFalse(changed)
    def test_material_entry_shift_resets(self):
        old=self.base(); new=dict(old,entry_low=100.4,entry_high=100.8)
        changed,reason=material_geometry_change(old,new)
        self.assertTrue(changed); self.assertIn('entry',reason)
    def test_material_stop_shift_resets(self):
        old=self.base(); new=dict(old,stop=97.5)
        self.assertTrue(material_geometry_change(old,new)[0])
    def test_invalid_side_geometry_resets(self):
        old=self.base(); new=dict(old,stop=100.0)
        self.assertTrue(material_geometry_change(old,new)[0])

class HeartbeatContractTests(unittest.TestCase):
    def test_recent_between_cycle_trigger_is_not_reported_as_no_signal(self):
        text=heartbeat_text({'liquid':100},triggered_window=1,now=datetime(2026,1,1,tzinfo=timezone.utc))
        self.assertIn('ENTRY NOW ЗА ПОСЛЕДНИЕ 10М: 1',text)
        self.assertNotIn('СИГНАЛОВ ДЛЯ ВХОДА НЕТ',text)
    def test_pause_is_not_reported_as_no_signal(self):
        text=heartbeat_text({},scan_error='PRODUCTION HEALTH PAUSE')
        self.assertIn('СКАН НЕ ЗАВЕРШЁН',text)
        self.assertNotIn('СИГНАЛОВ ДЛЯ ВХОДА НЕТ',text)
    def test_heartbeat_escapes_html_in_error_reason(self):
        text=heartbeat_text({},scan_error='HTTP <bad&broken>')
        self.assertIn('&lt;bad&amp;broken&gt;',text)
        self.assertNotIn('<bad&broken>',text)


class EntryStateSQLiteIntegrationTests(unittest.TestCase):
    def test_material_refresh_resets_real_sqlite_streak(self):
        with tempfile.TemporaryDirectory() as td:
            app=types.ModuleType('app'); app.__path__=[]
            cfg=types.ModuleType('app.config'); cfg.DATABASE_PATH=str(Path(td)/'state.db')
            market=types.ModuleType('app.market')
            async def get_klines(*a,**k): return None
            market.get_klines=get_klines
            live=types.ModuleType('v11_live')
            async def none_async(*a,**k): return None
            live.price=none_async; live.book=none_async; live.flow=none_async
            mods={'app':app,'app.config':cfg,'app.market':market,'v11_live':live}
            with patch.dict(sys.modules,mods):
                spec=importlib.util.spec_from_file_location('v1142_entry_now_tested',Path(__file__).with_name('v1142_entry_now.py'))
                mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
                sig=SimpleNamespace(symbol='BTCUSDT',timeframe='1H',side='LONG',setup_type='TEST',professional_rank=90,
                    entry_low=99.8,entry_high=100.2,stop=98.0,tp1=104.0,tp2=106.0,tp3=108.0,feature_snapshot={})
                arm_id=mod.arm(sig,'auto_main')
                c=sqlite3.connect(cfg.DATABASE_PATH)
                try:
                    c.execute("UPDATE v1142_armed SET confirm_streak=1,last_state='READY',last_score=88 WHERE id=?",(arm_id,))
                    c.commit()
                finally:
                    c.close()
                sig.entry_low=100.4; sig.entry_high=100.8
                mod.arm(sig,'auto_main')
                row=mod.get_row(arm_id)
                self.assertEqual(int(row['confirm_streak']),0)
                self.assertIsNone(row['last_state'])
                self.assertIn('confirmation reset',str(row['last_reason']))
                sys.modules.pop(spec.name,None)

class NewsIntelTests(unittest.TestCase):
    def test_macro_ambiguous_does_not_guess_direction(self):
        src={'name':'BLS','trust':.98,'official':True,'scope':'macro'}
        row=ni._item(src,'Consumer Price Index released for July','','',time.time())
        self.assertEqual(row['category'],'MACRO_INFLATION')
        self.assertEqual(row['trade_bias'],'AMBIGUOUS')
    def test_explicit_hot_cpi_is_risk_off(self):
        src={'name':'BLS','trust':.98,'official':True,'scope':'macro'}
        row=ni._item(src,'CPI hotter than expected as inflation rises','','',time.time())
        self.assertEqual(row['trade_bias'],'RISK_OFF')
    def test_hack_named_asset_is_negative(self):
        src={'name':'BBC Business','trust':.82,'official':False,'scope':'world'}
        row=ni._item(src,'Ethereum hack exploit drains funds','','',time.time())
        self.assertEqual(row['trade_bias'],'ASSET_NEGATIVE')
        self.assertIn('ETH',row['assets'])
    def test_stock_market_crash_is_high_impact_risk_off(self):
        src={'name':'BBC Business','trust':.82,'official':False,'scope':'world'}
        row=ni._item(src,'Stock market crash triggers circuit breakers on Wall Street','','',time.time())
        self.assertEqual(row['category'],'MARKET_STRESS')
        self.assertEqual(row['trade_bias'],'RISK_OFF')
        self.assertTrue(row['high_impact'])

    def test_world_noise_is_filtered(self):
        src={'name':'BBC World','trust':.80,'official':False,'scope':'world'}
        self.assertIsNone(ni._item(src,'Local arts festival opens this weekend','','',time.time()))
    def test_ceasefire_is_not_misclassified_risk_off(self):
        src={'name':'BBC World','trust':.80,'official':False,'scope':'world'}
        row=ni._item(src,'Ceasefire agreed after war escalation','','',time.time())
        self.assertEqual(row['trade_bias'],'RISK_ON')
    def test_sanctions_lifted_is_risk_on(self):
        src={'name':'BBC World','trust':.80,'official':False,'scope':'world'}
        row=ni._item(src,'Government lifts sanctions after trade talks','','',time.time())
        self.assertEqual(row['trade_bias'],'RISK_ON')
    def test_broken_ceasefire_is_risk_off(self):
        src={'name':'BBC World','trust':.80,'official':False,'scope':'world'}
        row=ni._item(src,'Ceasefire broken as missile attacks resume','','',time.time())
        self.assertEqual(row['trade_bias'],'RISK_OFF')
    def test_regulatory_charge_is_risk_off(self):
        src={'name':'CFTC','trust':.98,'official':True,'scope':'regulation'}
        row=ni._item(src,'CFTC charges Coinbase in digital asset enforcement action','','',time.time())
        self.assertEqual(row['trade_bias'],'RISK_OFF')
    def test_regulatory_settlement_without_direction_is_ambiguous(self):
        src={'name':'CFTC','trust':.98,'official':True,'scope':'regulation'}
        row=ni._item(src,'CFTC announces crypto settlement with exchange','','',time.time())
        self.assertEqual(row['trade_bias'],'AMBIGUOUS')
    def test_directional_breaking_can_be_market_confirmed(self):
        snapshot={'breaking_events':[{'source':'BLS','official':True,'title':'CPI hotter than expected','published_epoch':time.time(),
                                      'age_minutes':1,'confidence':.98,'category':'MACRO_INFLATION','trade_bias':'RISK_OFF','assets':[]}],
                  'event_risk':1.0}
        base=lambda snap,sym:{'score':0,'breaking':True,'event_risk':1.0}
        out=ni.for_symbol(base,snapshot,'BTCUSDT')
        self.assertFalse(out['breaking']); self.assertLess(out['score'],0); self.assertTrue(out['catalyst'])
    def test_ambiguous_breaking_stays_fail_closed(self):
        snapshot={'breaking_events':[{'source':'Federal Reserve','official':True,'title':'Federal Reserve statement','published_epoch':time.time(),
                                      'age_minutes':1,'confidence':.98,'category':'MACRO_RATES','trade_bias':'AMBIGUOUS','assets':[]}],
                  'event_risk':.5}
        base=lambda snap,sym:{'score':0,'breaking':False,'event_risk':.5}
        out=ni.for_symbol(base,snapshot,'BTCUSDT')
        self.assertTrue(out['breaking']); self.assertTrue(out['block']); self.assertGreaterEqual(out['event_risk'],.8)
    def test_ambiguous_event_releases_after_price_discovery(self):
        snapshot={'breaking_events':[{'source':'Federal Reserve','official':True,'title':'Federal Reserve statement','published_epoch':time.time()-600,
                                      'age_minutes':10,'confidence':.98,'category':'MACRO_RATES','trade_bias':'AMBIGUOUS','assets':[]}],
                  'event_risk':1.0}
        base=lambda snap,sym:{'score':0,'breaking':True,'event_risk':1.0}
        out=ni.for_symbol(base,snapshot,'BTCUSDT')
        self.assertFalse(out['breaking']); self.assertFalse(out['block']); self.assertEqual(out['score'],0.0)
    def test_unrelated_cftc_item_is_filtered(self):
        src={'name':'CFTC','trust':.98,'official':True,'scope':'regulation'}
        self.assertIsNone(ni._item(src,'CFTC charges agricultural futures trader with spoofing','','',time.time()))
    def test_near_duplicate_breaking_headlines_cluster(self):
        rows=[
            {'title':'Bitcoin ETF approved after regulator vote','category':'ETF_REGULATION','assets':['BTC'],'confidence':.9,'published_epoch':2},
            {'title':'Regulator approves Bitcoin ETF after vote','category':'ETF_REGULATION','assets':['BTC'],'confidence':.8,'published_epoch':1},
        ]
        out=ni._cluster_breaking(rows)
        self.assertEqual(len(out),1)
        self.assertGreaterEqual(out[0].get('corroboration_count',0),1)
    def test_opinion_explicitly_forbids_headline_entry(self):
        text=ni.opinion_text({'category':'GEO_CONFLICT','trade_bias':'RISK_OFF','confidence':.8})
        self.assertIn('По одному заголовку не входить',text)
    def test_news_alert_html_escapes_external_fields(self):
        text=ni.alert_text({'title':'Hack <NOW> & panic','source':'X & Y','url':'https://example.com/?a=1&b=2',
                            'category':'HACK_EXPLOIT','trade_bias':'RISK_OFF','confidence':.9})
        self.assertIn('Hack &lt;NOW&gt; &amp; panic',text)
        self.assertIn('X &amp; Y',text)
        self.assertIn('a=1&amp;b=2',text)
        self.assertNotIn('Hack <NOW>',text)
    def test_legacy_x_official_flag_is_demoted_to_social_trust(self):
        row=ni._enrich_existing({'source':'X @POTUS','social':True,'official':True,'title':'Bitcoin strategic reserve announced',
                                 'confidence':1.0,'high_impact':True,'assets':['BTC']})
        self.assertFalse(row['official'])
        self.assertTrue(row['social'])
        self.assertLessEqual(row['confidence'],.58)
        out=ni._corroborate([row])
        self.assertFalse(out[0]['trade_usable'])

    def test_uncorroborated_social_event_is_not_trade_relevant(self):
        snapshot={'breaking_events':[{'source':'X @someone','social':True,'official':False,'title':'Bitcoin will moon',
                                      'published_epoch':time.time(),'age_minutes':1,'confidence':.58,'category':'GENERAL_SHOCK',
                                      'trade_bias':'RISK_ON','assets':['BTC'],'corroboration_count':0}], 'event_risk':.5}
        base=lambda snap,sym:{'score':0,'breaking':False,'event_risk':.5}
        out=ni.for_symbol(base,snapshot,'BTCUSDT')
        self.assertEqual(out.get('score'),0)
        self.assertFalse(out.get('global_breaking',False))

    def test_unrelated_same_category_does_not_corroborate(self):
        rows=[
            {'source':'X @account','social':True,'official':False,'title':'SEC approves Bitcoin ETF','age_minutes':1,'category':'ETF_REGULATION','assets':['BTC'],'source_trust':.58},
            {'source':'BBC Business','social':False,'official':False,'title':'CFTC charges Coinbase over derivatives controls','age_minutes':2,'category':'ETF_REGULATION','assets':[],'source_trust':.82},
        ]
        out=ni._corroborate(rows)
        self.assertEqual(out[0]['corroboration_count'],0)
        self.assertFalse(out[0]['trade_usable'])

    def test_same_event_cross_source_does_corroborate(self):
        rows=[
            {'source':'X @account','social':True,'official':False,'title':'SEC approves Bitcoin spot ETF','age_minutes':1,'category':'ETF_REGULATION','assets':['BTC'],'source_trust':.58},
            {'source':'BBC Business','social':False,'official':False,'title':'Bitcoin spot ETF approved by SEC','age_minutes':2,'category':'ETF_REGULATION','assets':['BTC'],'source_trust':.82},
        ]
        out=ni._corroborate(rows)
        self.assertGreaterEqual(out[0]['corroboration_count'],1)
        self.assertTrue(out[0]['trade_usable'])

    def test_zero_age_event_is_still_fresh_for_corroboration(self):
        rows=[
            {'source':'BLS','official':True,'title':'CPI hotter than expected','age_minutes':0.0,'category':'MACRO_INFLATION','assets':[],'source_trust':.98},
            {'source':'BBC Business','official':False,'title':'US CPI hotter than expected','age_minutes':1.0,'category':'MACRO_INFLATION','assets':[],'source_trust':.82},
        ]
        out=ni._corroborate(rows)
        self.assertGreaterEqual(out[0]['corroboration_count'],1)
    def test_sources_include_primary_macro_regulators(self):
        names={x['name'] for x in ni.EXTRA_FEEDS}
        self.assertTrue({'BLS','ECB','CFTC','CFTC Enforcement'}.issubset(names))
    def test_global_breaking_targets_btc_eth(self):
        ni._latest_symbols=('BTCUSDT','ETHUSDT')
        self.assertEqual(ni.latest_breaking_symbols()[:2],('BTCUSDT','ETHUSDT'))


class NewsFeedHealthAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_rss_is_not_counted_healthy(self):
        class Resp:
            content=b'<rss></rss>'
            def raise_for_status(self): return None
        class Client:
            async def get(self,*a,**k): return Resp()
        fake=SimpleNamespace(parse=lambda content: SimpleNamespace(entries=[]))
        ok,name,items=await ni._fetch_one(Client(),{'name':'Empty','url':'https://example.invalid/rss','scope':'world','trust':.8,'official':False},fake)
        self.assertFalse(ok); self.assertEqual(items,[])

class NewsFallbackAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_base_sources_survive_extra_branch_exception(self):
        async def base(*a,**k): return {'global':0,'assets':{},'items':[],'breaking_events':[],'sources':2,'source_total':2,'event_risk':0,'high_impact_count':0}
        with patch.object(ni,'_extra_items',AsyncMock(side_effect=RuntimeError('extras down'))):
            out=await ni.get_news_sentiment(base,force=True)
        self.assertEqual(out['sources'],2)
        self.assertIn('GLOBAL_FEEDS:RuntimeError',out['global_intel']['failed_sources'])

    async def test_extra_sources_can_survive_base_news_exception(self):
        async def broken(*a,**k): raise RuntimeError('base down')
        extra={'id':'x','source':'BLS','title':'CPI hotter than expected','url':'','published_epoch':time.time(),
               'published_at':None,'age_minutes':1,'official':True,'social':False,'assets':[],
               'category':'MACRO_INFLATION','trade_bias':'RISK_OFF','confidence':.98,'source_trust':.98,
               'direction':'NEGATIVE','score':-.45,'high_impact':True,'impact':'HIGH','weight':1.2,'rationale':'macro'}
        with patch.object(ni,'_extra_items',AsyncMock(return_value=([extra],1,[]))):
            out=await ni.get_news_sentiment(broken,force=True)
        self.assertEqual(out['sources'],1)
        self.assertTrue(out['breaking_events'])
        self.assertIn('base down',out['global_intel']['base_error'])

class NewsFreshnessAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_age_official_event_is_breaking(self):
        async def base(*a,**k): return {'global':0,'assets':{},'items':[],'breaking_events':[],'sources':1,'source_total':1,'event_risk':0,'high_impact_count':0}
        event={'id':'fresh0','source':'BLS','title':'CPI hotter than expected','url':'','published_epoch':time.time(),
               'published_at':None,'age_minutes':0.0,'official':True,'social':False,'assets':[],
               'category':'MACRO_INFLATION','trade_bias':'RISK_OFF','confidence':.98,'source_trust':.98,
               'direction':'NEGATIVE','score':-.45,'high_impact':True,'impact':'HIGH','weight':1.2,'rationale':'macro'}
        with patch.object(ni,'_extra_items',AsyncMock(return_value=([event],1,[]))), patch.object(ni.time,'time',return_value=ni._PROCESS_STARTED+600):
            out=await ni.get_news_sentiment(base,force=True)
        self.assertTrue(any(x.get('id')=='fresh0' for x in out['breaking_events']))

class NewsOnboardingAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_extra_headline_does_not_alert_on_first_process_minutes(self):
        async def base(*a,**k): return {'global':0,'assets':{},'items':[],'breaking_events':[],'sources':1,'source_total':1,'event_risk':0,'high_impact_count':0}
        old={'id':'old','source':'BLS','title':'CPI hotter than expected','url':'','published_epoch':ni._PROCESS_STARTED-600,
             'published_at':None,'age_minutes':10,'official':True,'social':False,'assets':[],
             'category':'MACRO_INFLATION','trade_bias':'RISK_OFF','confidence':.98,'source_trust':.98,
             'direction':'NEGATIVE','score':-.45,'high_impact':True,'impact':'HIGH','weight':1.2,'rationale':'macro'}
        with patch.object(ni,'_extra_items',AsyncMock(return_value=([old],1,[]))), patch.object(ni.time,'time',return_value=ni._PROCESS_STARTED+60):
            out=await ni.get_news_sentiment(base,force=True)
        self.assertEqual(out['breaking_events'],[])
        self.assertEqual(out['high_impact_count'],1)

class RuntimeSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src=Path(__file__).with_name('bot_v11130.py').read_text(encoding='utf-8')
        cls.arm=Path(__file__).with_name('v1142_entry_now.py').read_text(encoding='utf-8')
    def test_version(self): self.assertIn('APP_VERSION="11.13.0"',self.src)
    def test_user_visible_version_labels_are_current(self):
        self.assertIn('YK CONTROL CENTER · V11.13.0',self.src)
        self.assertIn('KORKOVTS V11.13.0 GLOBAL INTELLIGENCE',self.src)
        self.assertNotIn('YK CONTROL CENTER · V11.12.2',self.src)
        self.assertNotIn('KORKOVTS V11.12.2 AUTO PULSE',self.src)
    def test_news_poll_one_minute(self): self.assertIn('core.NEWS_POLL_INTERVAL_SEC=60',self.src)
    def test_fast_radar_prioritizes_breaking_assets(self): self.assertIn('near=list(news_breaking_symbols())',self.src)
    def test_fast_radar_covers_15m_without_extra_candidate_budget(self):
        self.assertIn('_fast_analyze_short_symbol',self.src)
        self.assertIn('pick.source=="ticker_impulse"',self.src)
        self.assertIn('return await _fast_analyze_symbol(symbol,"15M")',self.src)
    def test_breaking_payload_patched(self): self.assertIn('core._breaking_news_payload=breaking_news_payload_v11130',self.src)
    def test_news_watch_releases_before_heavy_rescan(self):
        self.assertIn('core._run_news_triggered_scans=schedule_news_triggered_scans_v11130',self.src)
        self.assertIn('v11130-news-market-rescan',self.src)
        self.assertIn('news re-scan coalesced',self.src)
    def test_auto_on_describes_heartbeat(self): self.assertIn('Если входов нет, 10-минутный статус всё равно придёт',self.src)
    def test_pause_diagnostics_are_detected(self): self.assertIn('def _scan_pause_reason',self.src)
    def test_health_pauses_are_marked_blocked_not_ok(self):
        self.assertIn('d["status"]="blocked"; d["reason"]="PRODUCTION HEALTH PAUSE"',self.src)
        self.assertIn('d["status"]="blocked"; d["reason"]="BINANCE CLOCK PAUSE"',self.src)
    def test_recent_triggers_are_counted(self): self.assertIn('triggered_window=len(_recent_auto_triggers(600))',self.src)
    def test_async_entry_number_one_label_is_locked(self):
        self.assertIn('PRIME_LABEL_LOCK_SEC=120',self.src)
        self.assertIn('priority_label=_prime_label_candidate(arm_id,row)',self.src)
        self.assertIn('payload=core.fmt(fresh,priority_label)',self.src)
        self.assertIn('_last_prime_entry_at=time.time()',self.src)
    def test_live_flow_zero_age_is_not_treated_stale_by_source_contract(self):
        self.assertIn('flow_age is not None',self.arm)
        self.assertIn('0.0<=float(flow_age)<=20.0',self.arm)

    def test_arm_resets_confirmation_on_geometry_change(self):
        self.assertIn('material_geometry_change',self.arm)
        self.assertIn('confirm_streak=0',self.arm)
        self.assertIn('confirmation reset',self.arm)

if __name__=='__main__': unittest.main(verbosity=2)
