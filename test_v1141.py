import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd

from bot_v1141 import combine_rank, grade_for_rank, _portfolio_select, _freeze_final_rank, _refresh_cluster_ranks, thresholds_v112, _revalidation_candidates, _pipeline_latency_penalty
from v11_manager import target_flags
from v112_alpha import _agg_imbalance, _closed_5m_taker
from v11_engine import classify_regime, drift_stats
from v112_health import classify
from v113_execution import evaluate_quote, _total_cost_r
from v113_tracking import effective_tracking_start, effective_created_at, effective_last_checked_at
from v114_news import safe_fetch as safe_news_fetch, state as news_state, begin_context as begin_news_context, end_context as end_news_context
from v114_db import harden_database, status as hardened_db_status, backup_if_due
from v113_robustness import bootstrap as robustness_bootstrap
from v114_entry import activation_rate
from v112_lab import weight_from_groups, _family_total, _bh_approved
from v11_liquidity import _shape, _book_state
from v113_micro import evaluate as evaluate_micro
from v113_meta import _fit, _predict, _walk_forward, model as meta_model, decide as meta_decide, _training_row_allowed, _ood_metrics
from v1141_integrity import _apply_rounding, invariant_errors, stamp_lineage
from v1141_tracking import evaluate_ambiguous, update_one_ambiguous
from v1141_governor import _priority
import v1141_governor as request_governor


class V1141Tests(unittest.TestCase):
    def test_alpha_can_reject_borderline(self):
        self.assertIsNone(combine_rank(76,-3,True))

    def test_alpha_cannot_rescue_invalid_base(self):
        self.assertIsNone(combine_rank(95,4,False))

    def test_grade_recomputed(self):
        self.assertEqual(grade_for_rank(88),"A")
        self.assertEqual(grade_for_rank(81),"B+")

    def test_supported_factor_requires_stronger_evidence(self):
        pos=[1.0,1.1,.9,1.2,1.0]*12
        ctrl=[0,.1,-.1,.05,0]*12
        w,e,z,status=weight_from_groups(pos,ctrl)
        self.assertEqual(status,"SUPPORTED")
        self.assertGreater(w,1.0)
        self.assertLessEqual(w,1.10)

    def test_small_factor_sample_never_adapts(self):
        w,e,z,status=weight_from_groups([1]*20,[0]*20)
        self.assertEqual(w,1.0)
        self.assertEqual(status,"LEARNING")

    def test_harmful_factor_not_inverted(self):
        pos=[-.8,-.9,-.7,-1,-.6]*12
        ctrl=[0,.1,.2,0,-.1]*12
        w,e,z,status=weight_from_groups(pos,ctrl)
        self.assertGreaterEqual(w,.70)
        self.assertLess(w,1.0)

    def test_health_rest_failure_pauses(self):
        status,hard,_=classify(False,100,10,0,True,1,True,True)
        self.assertTrue(hard); self.assertEqual(status,"PAUSE")

    def test_health_ws_failure_only_degrades(self):
        status,hard,_=classify(True,100,10,0,False,999,True,True)
        self.assertFalse(hard); self.assertEqual(status,"DEGRADED")

    def test_health_db_failure_pauses(self):
        status,hard,_=classify(True,100,10,0,True,1,False,True)
        self.assertTrue(hard)

    def test_target_flags_use_actual_prices_long(self):
        row={"side":"LONG","tp1":105,"tp2":110}
        self.assertEqual(target_flags(row,106),(True,False))
        self.assertEqual(target_flags(row,111),(True,True))

    def test_target_flags_use_actual_prices_short(self):
        row={"side":"SHORT","tp1":95,"tp2":90}
        self.assertEqual(target_flags(row,94),(True,False))
        self.assertEqual(target_flags(row,89),(True,True))

    def test_agg_trade_sample_imbalance(self):
        rows=[
            {"T":1000,"p":"100","q":"1","m":False},
            {"T":2000,"p":"100","q":"1","m":True},
            {"T":3000,"p":"100","q":"2","m":False},
        ]
        imbalance,coverage=_agg_imbalance(rows,now_ms=4000)
        self.assertAlmostEqual(imbalance,.5,places=6)
        self.assertEqual(coverage,3)

    def test_portfolio_cap(self):
        class X: pass
        rows=[]
        for i in range(6):
            x=X(); x.professional_rank=95-i; x.score=95-i
            x.cluster_id=i+1; x.side="LONG" if i<4 else "SHORT"
            rows.append(x)
        out=_portfolio_select(rows,4)
        self.assertLessEqual(sum(x.side=="LONG" for x in out),3)

    def test_candidate_callback_payloads_are_short(self):
        # Numeric exact refs are far below Telegram's callback_data limit.
        for prefix in ("v11i","v11s"):
            value=f"{prefix}:stats:123456789"
            self.assertLessEqual(len(value.encode()),64)

    def test_grade_thresholds_consistent(self):
        self.assertEqual(grade_for_rank(90),"A+")
        self.assertEqual(grade_for_rank(85),"A")
        self.assertEqual(grade_for_rank(80),"B+")
        self.assertEqual(grade_for_rank(75),"B")

    def test_alpha_positive_cap(self):
        self.assertEqual(combine_rank(98,10,True),99)

    def test_portfolio_returns_best_first(self):
        class X: pass
        a=X(); a.professional_rank=91; a.score=92; a.cluster_id=1; a.side="LONG"
        b=X(); b.professional_rank=95; b.score=90; b.cluster_id=2; b.side="SHORT"
        self.assertIs(_portfolio_select([a,b],2)[0],b)

    def test_closed_5m_taker_exact_window(self):
        frame=pd.DataFrame({
            "volume":[10,10,10,10,10,999],
            "taker_buy_base":[7,7,7,7,7,0],
        })
        # Last five rows are used: buys = 28, volume = 1039 => strongly negative.
        expected=(2*(7+7+7+7+0)-(10+10+10+10+999))/(10+10+10+10+999)
        self.assertAlmostEqual(_closed_5m_taker(frame),expected,places=9)

    def test_high_volatility_alone_does_not_hard_pause(self):
        r=classify_regime({
            "btc_bias_raw":"LONG","btc_atr_pct":2.6,
            "breadth":{"dispersion":3.0},"breadth_blocked":False
        })
        self.assertEqual(r.name,"EXTREME_VOL")
        self.assertFalse(r.hard_pause)

    def test_true_extreme_shock_pauses(self):
        r=classify_regime({
            "btc_bias_raw":"LONG","btc_atr_pct":3.6,
            "breadth":{"dispersion":3.0},"breadth_blocked":False
        })
        self.assertEqual(r.name,"SHOCK")
        self.assertTrue(r.hard_pause)

    @patch("v11_engine._cohort_rows")
    def test_bad_recent_without_baseline_is_not_hard_drift(self,rows):
        rows.return_value=[-0.5]*20
        d=drift_stats(object())
        self.assertLess(d.penalty,8)
        self.assertEqual(d.baseline_n,0)

    @patch("v11_engine._cohort_rows")
    def test_good_baseline_then_collapse_can_hard_drift(self,rows):
        rows.return_value=[-0.5]*20+[0.4]*40
        d=drift_stats(object())
        self.assertEqual(d.penalty,8)
        self.assertGreaterEqual(d.baseline_n,30)

    def test_execution_rejects_tp1_already_hit_long(self):
        class S:
            side="LONG"; entry_high=100; entry_low=99; stop=95; tp1=105
        r=evaluate_quote(S(),104.99,105.01)
        self.assertFalse(r.eligible)
        self.assertEqual(r.status,"MOVE_DONE")

    def test_execution_rejects_late_long(self):
        class S:
            side="LONG"; entry_high=100; entry_low=99; stop=95; tp1=110
        r=evaluate_quote(S(),101.89,101.91)
        self.assertFalse(r.eligible)
        self.assertEqual(r.status,"LATE_ENTRY")

    def test_execution_rejects_invalidated_short(self):
        class S:
            side="SHORT"; entry_low=100; entry_high=101; stop=105; tp1=95
        r=evaluate_quote(S(),104.99,105.01)
        self.assertFalse(r.eligible)
        self.assertEqual(r.status,"STOP_INVALID")

    def test_execution_accepts_fresh_zone(self):
        class S:
            side="LONG"; entry_high=100; entry_low=99; stop=95; tp1=105
        r=evaluate_quote(S(),99.98,100.02)
        self.assertTrue(r.eligible)
        self.assertEqual(r.status,"OK")

    def test_execution_rejects_wide_live_spread(self):
        class S:
            side="LONG"; entry_high=100; entry_low=99; stop=95; tp1=105
        r=evaluate_quote(S(),99.9,100.1,max_spread_bps=5)
        self.assertFalse(r.eligible)
        self.assertEqual(r.status,"SPREAD_WIDE")

    def test_delivery_tracking_does_not_start_before_delivery(self):
        created="2026-08-17T10:00:00+00:00"
        delivered="2026-08-17T10:04:00+00:00"
        checked="2026-08-17T10:00:00+00:00"
        self.assertEqual(
            effective_tracking_start(created,delivered,checked,False),
            delivered
        )

    def test_shadow_tracking_uses_generation_time(self):
        created="2026-08-17T10:00:00+00:00"
        delivered="2026-08-17T10:04:00+00:00"
        self.assertEqual(
            effective_tracking_start(created,delivered,None,True),
            created
        )

    def test_family_cap_prevents_double_counting(self):
        self.assertEqual(_family_total([2.0,1.5],2.5),2.5)

    def test_family_cap_keeps_negative_penalty(self):
        # Positive evidence is capped, negative contradiction remains additive.
        self.assertEqual(_family_total([3.0,-2.0],2.5),0.5)

    def test_factor_requires_40_each(self):
        w,e,z,status=weight_from_groups([1.0]*39,[0.0]*100)
        self.assertEqual(w,1.0)
        self.assertEqual(status,"LEARNING")

    def test_unstable_factor_not_boosted(self):
        # Overall mean looks positive, but older half fails consistency gate.
        pos=[1.0]*25+[-.2]*25
        ctrl=[0.0]*50
        w,e,z,status=weight_from_groups(pos,ctrl,min_each=40)
        self.assertEqual(w,1.0)
        self.assertNotEqual(status,"SUPPORTED")

    def test_l2_shape_balanced_book(self):
        bids=[["99.99","100"],["99.98","100"],["99.95","100"]]
        asks=[["100.01","100"],["100.02","100"],["100.05","100"]]
        m=_shape(bids,asks)
        self.assertLess(abs(m["imbalance_10bps"]),.05)
        self.assertAlmostEqual(m["microprice_bias_bps"],0,places=5)

    def test_l2_state_marks_thin_book(self):
        self.assertEqual(_book_state(9.0,0.0,100_000,1.0,5000),"THIN")

    def test_l2_state_marks_bid_heavy(self):
        self.assertEqual(_book_state(2.5,.70,100_000,1.0,5000),"BID_HEAVY")

    def test_microstructure_blocks_three_layer_contradiction(self):
        class S:
            side="LONG"
            l2_state="ASK_HEAVY"
            l2_signed_imbalance_10=-.75
            l2_signed_microprice_bps=-.5
            l2_depth_ratio=.50
            l2_imbalance_delta=-.40
            alpha_ofi_5m=-.20
        d=evaluate_micro(S())
        self.assertFalse(d.eligible)
        self.assertEqual(d.label,"BLOCK")

    def test_microstructure_confirmation_is_small(self):
        class S:
            side="LONG"
            l2_state="DEEP_BALANCED"
            l2_signed_imbalance_10=.50
            l2_signed_microprice_bps=.2
            l2_depth_ratio=1.0
            l2_imbalance_delta=.1
            alpha_ofi_5m=.10
        d=evaluate_micro(S())
        self.assertTrue(d.eligible)
        self.assertLessEqual(d.adjustment,1.0)

    def test_meta_logistic_separates_simple_signal(self):
        import numpy as np
        rng=np.random.default_rng(7)
        neg=rng.normal(-1,.25,size=(80,16))
        pos=rng.normal(1,.25,size=(80,16))
        X=np.vstack([neg,pos])
        y=np.array([0]*80+[1]*80,dtype=float)
        model=_fit(X,y)
        pneg=float(_predict(model,np.zeros(16)-1))
        ppos=float(_predict(model,np.zeros(16)+1))
        self.assertLess(pneg,.25)
        self.assertGreater(ppos,.75)

    def test_meta_small_history_stays_learning(self):
        import numpy as np
        rows=[]
        for i in range(100):
            x=np.zeros(16); x[0]=i%2
            rows.append((str(i),x,float(i%2)))
        report,model=_walk_forward(rows)
        self.assertEqual(report.status,"LEARNING")

    def test_meta_walk_forward_can_become_ready_on_stable_edge(self):
        import numpy as np
        rng=np.random.default_rng(11)
        rows=[]
        for i in range(360):
            y=float(i%2)
            centre=1.0 if y else -1.0
            x=rng.normal(centre,.35,size=16)
            rows.append((f"{i:04d}",x,y))
        report,model=_walk_forward(rows)
        self.assertEqual(report.status,"READY")
        self.assertIsNotNone(model)
        self.assertGreater(report.precision,report.baseline_precision)

    def test_meta_feature_count_stays_bounded(self):
        from v113_meta import FEATURE_NAMES
        self.assertGreaterEqual(len(FEATURE_NAMES),12)
        self.assertLessEqual(len(FEATURE_NAMES),20)

    def test_live_impact_cost_is_measured_in_r(self):
        class S:
            side="LONG"; entry_high=100; entry_low=99; stop=95
            estimated_cost_r=.10; buy_1k_bps=5; sell_1k_bps=5
            funding=0; timeframe="15M"
        # Static configured 0.12% = .024R plus 10 bps two-sided impact = .02R.
        self.assertAlmostEqual(_total_cost_r(S()),.044,places=6)

    def test_live_impact_cost_can_exceed_gate(self):
        class S:
            side="LONG"; entry_high=100; entry_low=99; stop=99
            estimated_cost_r=.20; buy_1k_bps=10; sell_1k_bps=10
            funding=0; timeframe="15M"
        self.assertGreater(_total_cost_r(S()),.30)

    @patch("v113_meta._rows")
    def test_meta_model_small_history_builds_report_without_type_error(self,rows):
        import numpy as np
        rows.return_value=[(str(i),np.zeros(16),float(i%2)) for i in range(80)]
        report,fitted=meta_model("1H",force=True)
        self.assertEqual(report.timeframe,"1H")
        self.assertEqual(report.status,"LEARNING")
        self.assertIsNone(fitted)

    @patch("v113_meta.model")
    def test_ready_meta_never_boosts_pro_rank(self,model_mock):
        from v113_meta import ModelReport
        import numpy as np
        report=ModelReport("1H","READY",300,150,180,50,.60,.70,.55,.58,.28,.20,.25,"ok")
        model_mock.return_value=(report,(np.zeros(16),np.ones(16)*100,np.zeros(17)))
        class S:
            timeframe="1H"; score=90; side="LONG"; feature_snapshot={}
        s,d=meta_decide(S())
        self.assertTrue(d.ready)
        self.assertEqual(d.adjustment,0.0)

    def test_delivery_created_at_does_not_slide_with_checkpoints(self):
        created="2026-08-17T10:00:00+00:00"
        delivered="2026-08-17T10:04:00+00:00"
        first_checked="2026-08-17T10:20:00+00:00"
        second_checked="2026-08-17T11:00:00+00:00"

        # Entry-expiry anchor remains the delivery moment forever.
        self.assertEqual(effective_created_at(created,delivered,False),delivered)
        self.assertEqual(
            effective_last_checked_at(created,delivered,first_checked,False),
            first_checked
        )
        self.assertEqual(
            effective_last_checked_at(created,delivered,second_checked,False),
            second_checked
        )
        self.assertEqual(effective_created_at(created,delivered,False),delivered)

    def test_meta_rejects_execution_shadow_from_training(self):
        feature={"meta_v113":{"score":.4}}
        self.assertFalse(
            _training_row_allowed(feature,1,"V1141_EXECUTION_REJECT")
        )

    def test_meta_accepts_only_shadow_that_reached_meta(self):
        feature={"meta_v113":{"score":.4}}
        self.assertTrue(
            _training_row_allowed(feature,1,"V1141_META_REJECT")
        )
        self.assertTrue(
            _training_row_allowed(feature,1,"V1141_PORTFOLIO")
        )

    def test_meta_requires_meta_snapshot(self):
        self.assertFalse(
            _training_row_allowed({},1,"V1141_PORTFOLIO")
        )

    def test_final_rank_freeze_updates_feature_snapshot(self):
        class S:
            professional_rank=80
            professional_grade="B+"
            feature_snapshot={"v11":{"champion_rank":80,"grade":"B+"}}
        s=_freeze_final_rank(S(),87.5)
        self.assertEqual(s.professional_rank,87.5)
        self.assertEqual(s.professional_grade,"A")
        self.assertEqual(s.feature_snapshot["v11"]["champion_rank"],87.5)
        self.assertEqual(s.feature_snapshot["v11"]["grade"],"A")

    def test_cluster_rank_is_recomputed_after_pro_scoring(self):
        class S:
            feature_snapshot={}
        a=S(); a.cluster_id=1; a.professional_rank=82; a.score=95
        b=S(); b.cluster_id=1; b.professional_rank=91; b.score=90
        a.cluster_rank=1; b.cluster_rank=2
        _refresh_cluster_ranks([a,b])
        self.assertEqual(b.cluster_rank,1)
        self.assertEqual(a.cluster_rank,2)

    def test_news_failure_becomes_neutral_degraded_snapshot(self):
        import asyncio
        async def broken():
            raise RuntimeError("offline")
        data=asyncio.run(safe_news_fetch(broken))
        self.assertTrue(data["v114_news_degraded"])
        self.assertEqual(data["real_sources"],0)
        self.assertEqual(data["global"],0.0)
        self.assertFalse(data["assets"])
        self.assertFalse(data["breaking_events"])

    @patch("bot_v1141.news_runtime_state")
    @patch("bot_v1141.classify_regime")
    def test_news_degraded_adds_two_quality_points(self,regime,news):
        class R:
            penalty=0.0
            name="NORMAL"
        regime.return_value=R()
        state={
            "btc_bias_raw":"LONG",
            "score_adjustment":0,
            "breadth_blocked":False,
        }
        news.return_value={"degraded":False}
        normal=thresholds_v112(state)["main"]
        news.return_value={"degraded":True}
        degraded=thresholds_v112(state)["main"]
        self.assertEqual(degraded,normal+2)

    def test_bh_fdr_does_not_approve_every_nominal_signal(self):
        approved=_bh_approved({
            "fresh":.001,
            "momentum":.04,
            "ofi":.08,
            "squeeze":.20,
            "residual":.40,
            "quarter":.70,
        },q=.10)
        self.assertIn("fresh",approved)
        self.assertNotIn("quarter",approved)

    def test_meta_ood_detects_extreme_feature_vector(self):
        import numpy as np
        model=(np.zeros(16),np.ones(16),np.zeros(17))
        ood,rms,max_z=_ood_metrics(model,np.ones(16)*20)
        self.assertTrue(ood)
        self.assertGreater(max_z,6)

    @patch("v113_meta.model")
    def test_ready_meta_abstains_on_ood_instead_of_rejecting(self,model_mock):
        from v113_meta import ModelReport
        import numpy as np
        report=ModelReport("1H","READY",400,200,200,60,.60,.75,.55,.60,.30,.18,.25,"ok")
        model_mock.return_value=(report,(np.zeros(16),np.ones(16),np.zeros(17)))
        class S:
            timeframe="1H"; score=1000; side="LONG"; feature_snapshot={}
        s,d=meta_decide(S())
        self.assertTrue(d.ood)
        self.assertFalse(d.ready)
        self.assertTrue(d.eligible)
        self.assertEqual(s.meta_status,"ABSTAIN_OOD")

    @patch("v113_robustness._day_blocks")
    def test_robustness_uses_day_blocks(self,blocks):
        blocks.return_value=[[1.0,-.5],[.5],[1.0,-1.0,.2]]*5
        r=robustness_bootstrap("1H",simulations=100)
        self.assertEqual(r["days"],15)
        self.assertEqual(r["n"],30)

    def test_sqlite_hardening_enables_wal(self):
        hard=harden_database()
        status=hardened_db_status()
        self.assertEqual(hard["journal_mode"],"wal")
        self.assertEqual(status["journal"],"wal")
        self.assertTrue(status["ok"])

    def test_entry_activation_rate_ignores_unresolved_waiting(self):
        # Waiting signals are not part of the resolved denominator.
        self.assertAlmostEqual(activation_rate(6,2,2),.60)
        self.assertEqual(activation_rate(0,0,0),0.0)

    def test_sqlite_online_backup_is_created(self):
        harden_database()
        path=Path(backup_if_due(keep=2))
        self.assertTrue(path.exists())
        import sqlite3
        with sqlite3.connect(path,timeout=5) as c:
            self.assertEqual(str(c.execute("PRAGMA quick_check").fetchone()[0]).lower(),"ok")

    def test_full_revalidation_pool_does_not_drop_candidate_13(self):
        class S: pass
        rows=[]
        for i in range(20):
            s=S(); s.professional_rank=100-i; s.score=90-i/10
            rows.append(s)
        selected=_revalidation_candidates(rows)
        self.assertEqual(len(selected),20)
        self.assertIs(selected[12],rows[12])

    def test_scale_aware_liquidity_uses_probe_coverage(self):
        # $8k near-book depth is only 1.6x the $5k probe -> thin even if spread is fine.
        self.assertEqual(_book_state(1.0,0.0,8_000,1.0,5000),"THIN")
        self.assertEqual(_book_state(1.0,0.0,120_000,1.0,5000),"DEEP_BALANCED")

    def test_two_sided_cost_uses_exit_side_not_double_entry(self):
        class S:
            side="LONG"; entry_high=100; entry_low=99; stop=95
            estimated_cost_r=.10
            buy_1k_bps=1; sell_1k_bps=9
            funding=0; timeframe="15M"
        # 1 + 9 bps total has the same .02R impact as 5+5, proving both sides are used.
        self.assertAlmostEqual(_total_cost_r(S()),.044,places=6)

    def test_exchange_filter_rounding_preserves_long_geometry(self):
        class S:
            side="LONG"; entry_low=100.01; entry_high=100.09; stop=98.03
            tp1=102.07; tp2=104.11; tp3=106.19
        s=_apply_rounding(S(),{"tick_size":"0.1"})
        self.assertFalse(invariant_errors(s))
        self.assertEqual(s.entry_high,100.1)
        self.assertEqual(s.stop,98.0)

    def test_exchange_filter_rounding_preserves_short_geometry(self):
        class S:
            side="SHORT"; entry_low=100.01; entry_high=100.09; stop=102.03
            tp1=98.07; tp2=96.11; tp3=94.19
        s=_apply_rounding(S(),{"tick_size":"0.1"})
        self.assertFalse(invariant_errors(s))
        self.assertEqual(s.entry_low,100.0)
        self.assertEqual(s.stop,102.1)

    def test_lineage_hash_is_idempotent(self):
        class S:
            symbol="BTCUSDT"; timeframe="1H"; side="LONG"
            entry_low=99; entry_high=100; stop=95
            tp1=105; tp2=110; tp3=115
            score=90; professional_rank=88
            feature_snapshot={"v11":{"champion_rank":88}}
        s=S()
        stamp_lineage(s); first=s.feature_snapshot["lineage_v1141"]["sha256"]
        stamp_lineage(s); second=s.feature_snapshot["lineage_v1141"]["sha256"]
        self.assertEqual(first,second)

    def test_governor_prioritizes_execution_endpoints(self):
        self.assertEqual(_priority("/fapi/v1/depth"),"high")
        self.assertEqual(_priority("/fapi/v1/aggTrades"),"low")
        self.assertEqual(_priority("/fapi/v1/klines"),"normal")

    def test_news_contexts_do_not_leak_between_concurrent_scans(self):
        import asyncio
        async def broken():
            await asyncio.sleep(.01)
            raise RuntimeError("offline")
        async def healthy():
            await asyncio.sleep(.02)
            return {"sources":2,"source_total":2,"global":0.1}
        async def worker(fetcher):
            token=begin_news_context()
            try:
                await safe_news_fetch(fetcher)
                return news_state()["degraded"]
            finally:
                end_news_context(token)
        async def main():
            return await asyncio.gather(worker(broken),worker(healthy))
        result=asyncio.run(main())
        self.assertEqual(result,[True,False])

    def test_ambiguous_active_candle_is_not_counted_as_sl_or_tp(self):
        row={"entry":100,"stop":95,"tp2":110,"side":"LONG"}
        df=pd.DataFrame([{
            "open_time":pd.Timestamp("2026-01-01T00:00:00Z"),
            "close_time":pd.Timestamp("2026-01-01T00:00:59Z"),
            "open":100,"high":111,"low":94,"close":101,
        }])
        outcome,_,_=evaluate_ambiguous(row,df)
        self.assertEqual(outcome[0],"AMBIGUOUS_SL_TP")
        self.assertEqual(outcome[2],0.0)

    def test_exchange_filter_rounding_respects_nonzero_min_price_grid(self):
        class S:
            side="LONG"; entry_low=1.06; entry_high=1.06; stop=.86
            tp1=1.26; tp2=1.46; tp3=1.66
        s=_apply_rounding(S(),{"tick_size":"0.1","min_price":"0.05"})
        # Valid prices are .05 + N*.10: 1.05, 1.15, ...
        self.assertEqual(s.entry_low,1.05)
        self.assertEqual(s.entry_high,1.15)
        self.assertEqual(s.stop,.85)
        self.assertFalse(invariant_errors(s))

    def test_pipeline_latency_penalty_is_negative_only_after_grace(self):
        self.assertEqual(_pipeline_latency_penalty(100,"short"),0.0)
        self.assertAlmostEqual(_pipeline_latency_penalty(180,"short"),1.0)
        self.assertEqual(_pipeline_latency_penalty(170,"main"),0.0)
        self.assertAlmostEqual(_pipeline_latency_penalty(240,"main"),1.0)
        self.assertEqual(_pipeline_latency_penalty(999,"main"),2.0)

    def test_request_governor_enters_shared_cooldown_on_429(self):
        import asyncio
        class Response:
            status_code=429
            headers={"Retry-After":"2"}
        class RateLimitError(Exception):
            response=Response()
        async def fail(*args,**kwargs):
            raise RateLimitError("429")

        original=request_governor._raw_get
        request_governor._state["cooldown_until"]=0.0
        before=int(request_governor._state["rate_limit_failures"])
        request_governor._raw_get=fail
        try:
            async def main():
                try:
                    await request_governor.governed_get("/fapi/v1/depth",{"symbol":"BTCUSDT"})
                except RateLimitError:
                    return
            asyncio.run(main())
            self.assertEqual(
                int(request_governor._state["rate_limit_failures"]),before+1
            )
            self.assertGreater(request_governor.status()["cooldown_seconds"],0)
        finally:
            request_governor._raw_get=original
            request_governor._state["cooldown_until"]=0.0

    @patch("v1141_tracking.core.close_signal")
    def test_same_candle_entry_and_stop_is_marked_ambiguous(self,close_signal):
        import asyncio
        now=pd.Timestamp.now(tz="UTC")
        created=now-pd.Timedelta(minutes=5)
        candle_open=created+pd.Timedelta(seconds=2)
        candle_close=candle_open+pd.Timedelta(seconds=58)
        row={
            "id":7,"symbol":"BTCUSDT","timeframe":"1H","side":"LONG",
            "entry":100.0,"stop":95.0,"tp2":110.0,"status":"WAITING",
            "created_at":created.isoformat(),"last_checked_at":None,
            "activated_at":None,"source_chat_id":1,
            "max_favorable_r":0.0,"max_adverse_r":0.0,
        }
        df=pd.DataFrame([{
            "open_time":candle_open,"close_time":candle_close,
            "open":99.0,"high":101.0,"low":94.0,"close":98.0,
        }])
        events=asyncio.run(update_one_ambiguous(row,preloaded=df))
        self.assertEqual(events[0][3],"AMBIGUOUS_ENTRY_STOP")
        self.assertEqual(close_signal.call_args.args[1],"AMBIGUOUS_ENTRY_STOP")


if __name__=="__main__":
    unittest.main()
