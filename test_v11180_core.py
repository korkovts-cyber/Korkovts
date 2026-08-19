import unittest
import types
from pathlib import Path
from v11180_defense import assess, required_confirmations

class Sig:
    symbol='TESTUSDT'; timeframe='1H'; side='LONG'; production_regime='TREND'; professional_rank=90
    def __init__(self):
        self.market_context={'bias':'LONG'}
        self.feature_snapshot={'indicator_edge_v11151':{
            'avwap':{'state':'SUPPORT'},'volume_profile':{'state':'SUPPORT'},'rvol':{'state':'SUPPORT'},
            'liquidity_sweep':{'state':'NEUTRAL'},'cvd_live':{'state':'SUPPORT'},'oi_matrix':{'state':'SUPPORT'}}}

def book(**kw):
    d={'adverse_long_share_5s':.1,'adverse_short_share_5s':.1,'microprice_drift_bps_2s':0}; d.update(kw); return d

class Health:
    hard_pause=False; status='GREEN'

class V11180Tests(unittest.TestCase):
    def test_clean_trend_passes(self): self.assertTrue(assess(Sig(),book(),2,Health()).eligible)
    def test_shock_blocks(self):
        s=Sig(); s.production_regime='SHOCK'; self.assertFalse(assess(s,book(),5,Health()).eligible)
    def test_range_requires_three_checks(self):
        s=Sig(); s.production_regime='RANGE'; self.assertFalse(assess(s,book(),2,Health()).eligible); self.assertTrue(assess(s,book(),3,Health()).eligible)
    def test_regime_confirmation_contract(self):
        self.assertEqual(required_confirmations('TREND'),2)
        for regime in ('RANGE','RANGE_LOW_VOL','EXTREME_VOL','HIGH_DISPERSION','DIVERGENCE'):
            self.assertEqual(required_confirmations(regime),3)
    def test_opposite_btc_bias_blocks_without_independent_mode(self):
        s=Sig(); s.market_context={'bias':'SHORT'}; self.assertFalse(assess(s,book(),3,Health()).eligible)
    def test_two_independent_conflicts_block(self):
        s=Sig(); e=s.feature_snapshot['indicator_edge_v11151']; e['avwap']={'state':'CONFLICT'}; e['cvd_live']={'state':'CONFLICT'}
        self.assertFalse(assess(s,book(),3,Health()).eligible)
    def test_adverse_selection_blocks(self): self.assertFalse(assess(Sig(),book(adverse_long_share_5s=.8),3,Health()).eligible)
    def test_microprice_drift_blocks(self): self.assertFalse(assess(Sig(),book(microprice_drift_bps_2s=-3.0),3,Health()).eligible)
    def test_health_circuit_breaker_blocks(self):
        h=Health(); h.hard_pause=True; self.assertFalse(assess(Sig(),book(),3,h).eligible)
    def test_negative_only_contract(self):
        s=Sig(); before=s.professional_rank; assess(s,book(),3,Health()); self.assertEqual(s.professional_rank,before)
        self.assertTrue(s.feature_snapshot['defense_v11180']['negative_only'])
    def test_bot_integrates_defense_before_save_pending(self):
        src=Path('bot_v11180.py').read_text(); a=src.index('defense_decision=assess_v11180_defense'); b=src.index('signal_id=core.save_pending(fresh)')
        self.assertLess(a,b); self.assertIn('defense=defense_decision',src)
    def test_final_gateway_waits_without_destroying_arm(self):
        src=Path('bot_v11180.py').read_text()
        start=src.index('if not final_gate.eligible:')
        end=src.index('priority_label=',start)
        block=src[start:end]
        self.assertIn('return "WAIT",fresh,None,None',block)
        self.assertNotIn('cancel_entry_arm(arm_id,reason)',block)
    def test_transient_health_and_derivatives_keep_arm_alive(self):
        src=Path('bot_v11180.py').read_text()
        self.assertIn('raise _EntryRevalidationWait("production health/clock gate is temporarily unavailable")',src)
        self.assertIn('raise _EntryRevalidationWait("fresh derivatives snapshot is incomplete")',src)
        trigger=src[src.index('async def _trigger_arm'):src.index('async def entry_now_monitor_job')]
        self.assertIn('except _EntryRevalidationWait as exc:',trigger)
        self.assertIn('return "WAIT",None,None,None',trigger)
    def test_futures_l2_exports_real_microprice_drift(self):
        src=Path('v11110_futures_orderbook.py').read_text()
        self.assertIn('self.history.append((now,spread,b,a,imb,micro))',src)
        self.assertIn('microprice_drift_bps_2s=',src)
        self.assertIn('"microprice_drift_bps_2s":float(st.get("microprice_drift_bps_2s",0) or 0)',src)
    def test_snapshot_fingerprint_captures_v11180_microstructure(self):
        from types import SimpleNamespace
        from v11170_snapshot import assess as assess_snapshot
        sig=SimpleNamespace(
            symbol='TESTUSDT',timeframe='1H',feature_snapshot={
                'data_coherence_v11100':{'eligible':True,'observable':True},
                'data_freshness_v1142':{'derivatives_acquire_ms':1000.0},
            }
        )
        base={
            'sequence_synced':True,'healthy':True,'event_age_sec':0.5,'exchange_lag_sec':0.1,
            'fetched_at':1000.0,'lastUpdateId':123,'spread_bps':1.0,'stability_score':90,
            'microprice_drift_bps_2s':0.0,'adverse_long_share_5s':0.0,'adverse_short_share_5s':0.0,
        }
        a=assess_snapshot(sig,dict(base),now=1000.0)
        bbook=dict(base); bbook['microprice_drift_bps_2s']=-3.0
        b=assess_snapshot(sig,bbook,now=1000.0)
        self.assertNotEqual(a.snapshot_fingerprint,b.snapshot_fingerprint)
        src=Path('v11170_snapshot.py').read_text()
        self.assertIn('microprice_drift_bps_2s',src)
        self.assertIn('adverse_long_share_5s',src)

    def test_user_visible_version_labels_are_current(self):
        src=Path('bot_v11180.py').read_text()
        for label in (
            'EARLY WATCH · V11.18.0',
            'FINAL RISK GATE · V11.18.0',
            'YK CONTROL CENTER · V11.18.0',
            'KORKOVTS V11.18.0 · REGIME + PORTFOLIO DEFENSE',
        ):
            self.assertIn(label,src)
        for stale in (
            'EARLY WATCH · V11.15</b>',
            'FINAL RISK GATE · V11.17.2',
            'YK CONTROL CENTER · V11.17.2',
            'KORKOVTS V11.17.2 DEEP AUDIT HARDENING',
        ):
            self.assertNotIn(stale,src)

    def test_early_watch_does_not_claim_two_of_two_for_protected_regimes(self):
        src=Path('bot_v11180.py').read_text()
        block=src[src.index('def early_watch_text'):src.index('async def callback_v112')]
        self.assertIn('required <b>2 / protected 3</b>',block)
        self.assertNotIn('/2</b>',block)

    def test_indicator_edge_does_not_silently_drop_second_half_of_pool(self):
        src=Path('bot_v11180.py').read_text()
        block=src[src.index('async def _annotate_indicator_edge_many'):src.index('def _indicator_gate')]
        self.assertIn('[:V11_CANDIDATE_POOL]',block)
        self.assertNotIn('[:10]',block)

    def test_breadth_divergence_independent_mode_is_not_blanket_hard_veto(self):
        from v1170_evidence import futures
        sig=types.SimpleNamespace(
            side="LONG", estimated_cost_r=.10,
            feature_snapshot={
                "decision":{"score_gap":25},
                "technical":{"adx":24,"plus_di":30,"minus_di":15,"rsi":58,"macd_hist":1,"taker_imbalance10":.03},
                "derivatives":{"taker_ratio":1.06,"oi_change_pct":.4,"price_change_pct":.3,"spread_bps":1.0},
                "news":{"score":0,"breaking":False,"event_risk":0},
                "market":{"bias":"NEUTRAL","btc_bias_raw":"LONG","breadth_blocked":True,"independent_mode":True},
                "alpha_v112":{"ofi_5m":.02},
                "execution_v113":{"l2_state":"NORMAL"},
                "meta_v113":{"ready":False},
            },
        )
        a=futures(sig)
        regime=[f for f in a.families if f.name=="market regime"][0]
        self.assertEqual(regime.state,"NEUTRAL")
        self.assertFalse(regime.hard)
        self.assertFalse(any("market regime:" in x for x in a.hard_conflicts))

    def test_auto_heartbeat_surfaces_production_reject_bottleneck(self):
        from v11122_auto import heartbeat_text
        text=heartbeat_text({
            "liquid":180,"prefiltered":25,"deep_checked":12,"final":0,
            "evidence_rejected":7,"indicator_rejected":3,
        })
        self.assertIn("Evidence −7",text)
        self.assertIn("Indicator −3",text)

    def test_independent_mode_relaxes_duplicate_support_floor_once(self):
        evidence=Path('v1170_evidence.py').read_text()
        strong=Path('v11150_strong.py').read_text()
        self.assertIn('required_support=4 if (breadth_blocked and independent) else 5',evidence)
        self.assertIn('required_support=4 if independent_relief else 5',strong)
        self.assertIn('and support>=required_support',strong)

    def test_strong_consensus_runs_before_portfolio_truncation(self):
        src=Path('bot_v11180.py').read_text()
        strong_pos=src.index('strong_checked=annotate_strong_signals(protection_valid)')
        portfolio_pos=src.index('chosen=_portfolio_select(protection_valid,final_limit)')
        self.assertLess(strong_pos,portfolio_pos)
        self.assertIn('d["strong_rejected"]=len(strong_rejected)',src)

    def test_spot_terminal_extreme_precedes_news_but_news_precedes_degraded_crowding(self):
        for name in ("bot_v11100.py","bot_v11130.py","bot_v11180.py"):
            src=Path(name).read_text(encoding="utf-8")
            start=src.index("async def _deliver_spot_pending")
            end=src.index("async def spot_delivery_retry_job",start)
            block=src[start:end]
            extreme=block.index('if crowd.get("extreme"):\n')
            news=block.index('news=spot_assess_news(news_snapshot,base)')
            degraded=block.index('if crowd.get("degraded"):\n')
            self.assertLess(extreme,news,name)
            self.assertLess(news,degraded,name)

    def test_signal_bot_does_not_start_in_hidden_canary_and_allows_two_live(self):
        src=Path('v1142_risk.py').read_text()
        self.assertIn('MAX_CONCURRENT_LIVE=1',src)
        self.assertIn('V11180_MAX_CONCURRENT_LIVE=2',src)
        self.assertIn('delivery_bootstrap_key',src)
        self.assertIn('bootstrap_release="11.18.1-signal-delivery"',src)
        self.assertIn('effective_max_concurrent_live()',src)
        # Redeploy/bootstrap must not erase a real loss circuit or its baselines.
        self.assertIn('Preserve any real CIRCUIT_PAUSE',src)
        self.assertNotIn('SET canary_passed=1,paused_at=NULL,pause_reason=NULL,\n                    baseline_signal_id=?,probe_baseline_id=?,\n                    delivery_bootstrap_key=?',src)

    def test_futures_second_live_delivery_respects_effective_cap(self):
        for name in ('bot_v11100.py','bot_v11130.py','bot_v11180.py'):
            src=Path(name).read_text()
            self.assertIn('effective_max_concurrent_live as futures_live_cap',src)
            self.assertIn('futures_other_live_count(signal_id)>=max(1,int(futures_live_cap()))',src)
            self.assertNotIn('futures_other_live_count(signal_id)>0',src)

if __name__=='__main__': unittest.main()
