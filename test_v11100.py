import unittest
import asyncio
import json
import random
import sqlite3
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import pandas as pd
import app.db as app_db

from bot_v11100 import (
    combine_rank, grade_for_rank, _portfolio_select, _freeze_final_rank,
    _refresh_cluster_ranks, thresholds_v112, _revalidation_candidates,
    _pipeline_latency_penalty, _activate_delivered_entry_now, _deliver_spot_pending,
    _validate_futures_delivery, _deliver_forced_futures,
    _arm_spot_candidate, _spot_orderbook_symbols,
    APP_VERSION, FUTURES_RELEASE_VERSION,
)
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
from v1141_governor import _retry_after_seconds as futures_retry_after_seconds
from v1142_entry_now import (
    TriggerAssessment, evaluate as evaluate_entry_now, arm as arm_entry_now,
    get_row as get_entry_arm, record_check as record_entry_check,
    mark_triggered as mark_entry_triggered,
    mark_pending_delivery as mark_entry_pending_delivery,
    active_rows as current_entry_rows,
    active_symbols as armed_symbols,
)
import v1142_entry_now as entry_now_module
from v11_live import _handle as live_handle, flow as live_flow, set_extra_symbol_provider, desired_symbols
import v11_live as live_module
from v11_ui import card as signal_card
from spot_ui import card as spot_card, active_text as spot_active_text
from v1142_risk import (
    init as init_futures_safety, status as futures_safety_status,
    CANARY_REASON, CIRCUIT_REASON, active_trades_text
)
import v1142_risk as futures_risk_module

from spot_indicators import enrich as spot_enrich
from spot_microstructure import analyze_book as spot_analyze_book
from spot_news import assess as spot_news_assess
from spot_strategy import analyze as spot_analyze, derivatives_crowding as spot_crowding, normalize as spot_normalize
from spot_market import (
    SpotMeta, _is_leveraged_base as spot_is_leveraged_base,
    _retry_after_seconds as spot_retry_after_seconds,
)
from spot_scanner import (
    _percentiles as spot_percentiles, _futures_counterpart as spot_futures_counterpart,
    _corr30 as spot_corr30, _portfolio_diversify as spot_portfolio_diversify,
    recheck_watch as spot_recheck_watch,
    active_correlation_risk as spot_active_correlation_risk,
)
from v1171_sqlite import db_session
import spot_db as spot_db_module
from spot_db import (
    init as init_spot_db, stats as spot_db_stats, recent as recent_spot_signals,
    save as save_spot_signal, enqueue_delivery as enqueue_spot_delivery,
    expire_pending_deliveries as expire_spot_deliveries,
    pending_deliveries as pending_spot_deliveries,
    was_sent_recently as spot_was_sent_recently,
    mark_delivery_sent as mark_spot_delivery_sent,
    active_open_count as spot_active_open_count,
    active_portfolio_clusters as spot_active_portfolio_clusters,
    portfolio_reserved_count as spot_reserved_count,
    portfolio_reserved_signals as spot_reserved_signals,
    SPOT_RELEASE_VERSION,
)
from spot_tracker import update_one as update_spot_one
from spot_watch import (
    init as init_spot_watch, upsert as upsert_spot_watch,
    active as active_spot_watches, get as get_spot_watch,
    record_ready as record_spot_ready, reset_ready as reset_spot_ready,
    close as close_spot_watch,
    reconcile_pending_delivery as reconcile_spot_watch_delivery,
)
from v1170_calibration import (
    futures as calibrated_futures, spot as calibrated_spot,
    _spot_success_value, wilson_interval, short_text as calibrated_text,
)
from v1171_delivery import (
    init as init_v1171_delivery,
    pending as pending_v1171_futures,
    expire as expire_v1171_futures,
    reconcile_failed_arms as reconcile_failed_futures_arms,
)
from v1170_evidence import futures as futures_evidence, spot as spot_evidence
from v11_ui import main_menu
import spot_orderbook as spot_orderbook_module
from spot_orderbook import LocalBook, stability as spot_local_stability
from v1180_lab import (
    init as init_v1180_lab, challenger_decision as v1180_challenger_decision,
    summary as v1180_summary, sync_outcomes as v1180_sync_outcomes,
    MIN_CHALLENGER_RESOLVED, MIN_REJECTED_RESOLVED,
)
from v1180_manager import (
    init as init_v1180_manager, _futures_state as v1180_futures_state,
    _spot_state as v1180_spot_state, _classify_spot as v1180_classify_spot,
    reconcile_closed as v1180_reconcile_closed,
    sync_failures as v1180_sync_failures,
)
import v1180_manager as v1180_manager_module
from v11100_edge import (
    _stats as v1190_edge_stats, annotate_many as v1190_annotate_edges,
    selection_key as v1190_selection_key,
)
from v11100_blackbox import (
    init as init_v1190_blackbox, record_many as record_many_v1190_blackbox,
    recent as recent_v1190_blackbox,
)
from v11100_replay import replay as v1190_replay


def _entry_frame(side="LONG",n=40):
    rows=[]
    base=100.0
    for i in range(n):
        if side=="LONG":
            close=base+i*.03
            open_=close-.02
            low=open_-.02
            high=close+.02
            taker=.60
        else:
            close=base-i*.03
            open_=close+.02
            low=close-.02
            high=open_+.02
            taker=.40
        volume=1000.0
        rows.append({
            "open_time":pd.Timestamp("2026-01-01T00:00:00Z")+pd.Timedelta(minutes=i),
            "close_time":pd.Timestamp("2026-01-01T00:00:59Z")+pd.Timedelta(minutes=i),
            "open":open_,"high":high,"low":low,"close":close,
            "volume":volume,"taker_buy_base":volume*taker,
        })
    return pd.DataFrame(rows)


def _arm_signal(side="LONG",timeframe="1H"):
    class S: pass
    s=S()
    s.symbol="TESTUSDT"; s.timeframe=timeframe; s.side=side
    s.setup_type="CONTROLLED CONTINUATION"; s.professional_rank=90.0; s.score=90.0
    s.market_context={"bias":side}; s.adl_risk="low"; s.cluster_id=0
    if side=="LONG":
        s.entry_low=100.8; s.entry_high=101.2; s.stop=99.2
        s.tp1=103.2; s.tp2=105.2; s.tp3=107.2
    else:
        s.entry_low=98.8; s.entry_high=99.2; s.stop=100.8
        s.tp1=96.8; s.tp2=94.8; s.tp3=92.8
    s.feature_snapshot={}
    return s


def _spot_frame(n=240,freq="1D",start=100.0,step=.35,volume=1_000_000):
    rows=[]
    base_time=pd.Timestamp("2025-01-01T00:00:00Z")
    delta=pd.Timedelta(days=1) if freq=="1D" else (pd.Timedelta(hours=4) if freq=="4H" else pd.Timedelta(hours=1))
    for i in range(n):
        # smooth trend with tiny deterministic oscillation, no vertical pump.
        close=start+step*i+(0.15 if i%4==0 else (-0.08 if i%4==2 else 0))
        open_=close-step*.25
        high=close+max(.25,abs(step)*.7)
        low=open_-max(.20,abs(step)*.55)
        vol=volume*(1+0.08*((i%7)/6))
        rows.append({
            "open_time":base_time+delta*i,
            "close_time":base_time+delta*(i+1)-pd.Timedelta(milliseconds=1),
            "open":open_,"high":high,"low":low,"close":close,
            "volume":vol,"quote_volume":vol*close,"trades":1000+i,
            "taker_buy_base":vol*.56,"taker_buy_quote":vol*.56*close,
        })
    return pd.DataFrame(rows)


class V1181Tests(unittest.TestCase):
    def setUp(self):
        app_db.init()
        entry_now_module.init()
        init_futures_safety()
        init_spot_db()
        init_spot_watch()
        init_v1180_lab()
        init_v1180_manager()
        init_v1190_blackbox()
        with entry_now_module._db() as c:
            c.execute("DELETE FROM spot_watchlist")
            c.execute("DELETE FROM spot_deliveries")
            c.execute("DELETE FROM spot_signals")
            c.execute("DELETE FROM v1180_compare")
            c.execute("DELETE FROM v1180_manager")
            c.execute("DELETE FROM v1180_failures")
            c.execute("DELETE FROM v1190_blackbox")
            c.execute("DELETE FROM v1142_armed")
            # Full unit-test isolation. Legacy/current release fixtures are
            # inserted explicitly inside the test that needs them.
            c.execute("DELETE FROM signal_deliveries")
            c.execute("DELETE FROM signals")
            c.execute("""
                UPDATE v1142_safety
                SET canary_passed=0,paused_at=NULL,pause_reason=NULL,
                    baseline_signal_id=0,probe_baseline_id=0,release_key='11.7.1',resumed_at=NULL
                WHERE id=1
            """)
        try:
            import v11_live
            v11_live._trade_flow.clear()
            set_extra_symbol_provider(None)
            spot_orderbook_module._books.clear()
            spot_orderbook_module._buffers.clear()
            spot_orderbook_module._sync_tasks.clear()
            spot_orderbook_module._connected=False
            spot_orderbook_module._last_message=0.0
            spot_orderbook_module.set_symbol_provider(None)
        except Exception:
            pass

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
            _training_row_allowed(feature,1,"V1142_EXECUTION_REJECT")
        )

    def test_meta_accepts_only_shadow_that_reached_meta(self):
        feature={"meta_v113":{"score":.4}}
        self.assertTrue(
            _training_row_allowed(feature,1,"V1142_META_REJECT")
        )
        self.assertTrue(
            _training_row_allowed(feature,1,"V1142_PORTFOLIO")
        )

    def test_meta_requires_meta_snapshot(self):
        self.assertFalse(
            _training_row_allowed({},1,"V1142_PORTFOLIO")
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

    @patch("bot_v11100.news_runtime_state")
    @patch("bot_v11100.classify_regime")
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
        with db_session(path=path,timeout=5) as c:
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
        stamp_lineage(s); first=s.feature_snapshot["lineage_v1142"]["sha256"]
        stamp_lineage(s); second=s.feature_snapshot["lineage_v1142"]["sha256"]
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

    def test_binance_retry_after_is_not_truncated_to_sixty_seconds(self):
        self.assertEqual(spot_retry_after_seconds({"Retry-After":"600"},418),600.0)
        self.assertEqual(futures_retry_after_seconds({"Retry-After":"600"},418),600.0)
        self.assertEqual(spot_retry_after_seconds({},418),120.0)
        self.assertEqual(futures_retry_after_seconds({},429),2.0)

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

    def test_entry_now_long_requires_live_flow_and_is_ready(self):
        row={
            "side":"LONG","timeframe":"1H","setup_type":"CONTINUATION",
            "entry_low":100.8,"entry_high":101.2,"stop":99.2,"tp1":103.2,
        }
        f1=_entry_frame("LONG")
        f3=_entry_frame("LONG")
        a=evaluate_entry_now(
            row,f1,f3,px=101.0,
            bk={"bid":100.99,"ask":101.01},
            flow_row={"total_notional":50_000,"trades":30,"age_sec":1,
                      "buy_share":.60,"imbalance":.20},
        )
        self.assertEqual(a.state,"READY")
        self.assertGreaterEqual(a.score,80)
        self.assertTrue(a.one_min_ok)
        self.assertTrue(a.three_min_ok)
        self.assertTrue(a.flow_ok)
        self.assertEqual(a.flow_source,"live_aggTrade_60s")

    def test_entry_now_closed_candle_flow_alone_cannot_trigger(self):
        row={
            "side":"LONG","timeframe":"1H","setup_type":"CONTINUATION",
            "entry_low":100.8,"entry_high":101.2,"stop":99.2,"tp1":103.2,
        }
        a=evaluate_entry_now(
            row,_entry_frame("LONG"),_entry_frame("LONG"),px=101.0,
            bk={"bid":100.99,"ask":101.01},flow_row=None,
        )
        self.assertEqual(a.state,"WAIT")
        self.assertFalse(a.flow_ok)
        self.assertEqual(a.flow_source,"closed_1m_context_only")

    def test_entry_now_short_ready(self):
        row={
            "side":"SHORT","timeframe":"15M","setup_type":"CONTINUATION",
            "entry_low":98.8,"entry_high":99.2,"stop":100.8,"tp1":96.8,
        }
        a=evaluate_entry_now(
            row,_entry_frame("SHORT"),_entry_frame("SHORT"),px=99.0,
            bk={"bid":98.99,"ask":99.01},
            flow_row={"total_notional":40_000,"trades":20,"age_sec":1,
                      "buy_share":.40,"imbalance":-.20},
        )
        self.assertEqual(a.state,"READY")
        self.assertTrue(a.flow_ok)

    def test_entry_now_wide_spread_never_ready(self):
        row={
            "side":"LONG","timeframe":"15M","setup_type":"CONTINUATION",
            "entry_low":100.8,"entry_high":101.2,"stop":99.2,"tp1":103.2,
        }
        a=evaluate_entry_now(
            row,_entry_frame("LONG"),_entry_frame("LONG"),px=101.0,
            bk={"bid":100.90,"ask":101.10},
            flow_row={"total_notional":40_000,"trades":20,"age_sec":1,"buy_share":.60},
        )
        self.assertEqual(a.state,"WAIT")
        self.assertGreater(a.spread_bps,2.5)

    def test_entry_now_chase_is_cancelled(self):
        row={
            "side":"LONG","timeframe":"1H","setup_type":"CONTINUATION",
            "entry_low":100.0,"entry_high":101.0,"stop":99.0,"tp1":110.0,
        }
        # Risk=2; >0.25R above high means >101.5.
        a=evaluate_entry_now(
            row,_entry_frame("LONG"),_entry_frame("LONG"),px=101.7,
            bk={"bid":101.69,"ask":101.71},
            flow_row={"total_notional":50_000,"trades":30,"age_sec":1,"buy_share":.60},
        )
        self.assertEqual(a.state,"CANCEL")
        self.assertIn("escaped entry",a.reason)

    def test_entry_now_requires_two_persistent_checks(self):
        s=_arm_signal("LONG","1H")
        arm_id=arm_entry_now(s,"unit")
        base=dict(
            state="READY",score=90.0,reason="ok",price=101.0,bid=100.99,ask=101.01,
            spread_bps=2.0,distance_r=0.0,one_min_ok=True,three_min_ok=True,
            flow_ok=True,flow_share=.60,flow_source="live_aggTrade_60s",
            candle_ok=True,volume_ratio=1.2,
        )
        a1=TriggerAssessment(checked_at=1000.0,**base)
        a2=TriggerAssessment(checked_at=1010.0,**base)
        a3=TriggerAssessment(checked_at=1031.0,**base)
        self.assertEqual(record_entry_check(arm_id,a1),1)
        self.assertEqual(record_entry_check(arm_id,a2),1)
        self.assertEqual(record_entry_check(arm_id,a3),2)

    def test_rearming_does_not_slide_original_expiry(self):
        s=_arm_signal("LONG","1H")
        with patch("v1142_entry_now.time.time",return_value=10_000.0):
            arm_id=arm_entry_now(s,"first")
        first=get_entry_arm(arm_id)
        with patch("v1142_entry_now.time.time",return_value=10_600.0):
            same=arm_entry_now(s,"repeat")
        second=get_entry_arm(same)
        self.assertEqual(arm_id,same)
        self.assertEqual(first["expires_at"],second["expires_at"])
        self.assertEqual(first["armed_at"],second["armed_at"])

    def test_triggered_history_does_not_block_new_active_arm(self):
        s=_arm_signal("LONG","1H")
        first=arm_entry_now(s,"first")
        mark_entry_triggered(first,123)
        second=arm_entry_now(s,"new")
        self.assertNotEqual(first,second)
        self.assertEqual(get_entry_arm(second)["status"],"ACTIVE")

    def test_live_aggtrade_flow_direction(self):
        import v11_live
        v11_live._trade_flow.clear()
        with patch("v11_live.time.time",return_value=2000.0):
            now_ms=int(time.time()*1000)
            live_handle({"data":{"e":"aggTrade","E":now_ms,"T":now_ms,"s":"TESTUSDT","p":"100","q":"2","m":False}})
            live_handle({"data":{"e":"aggTrade","E":now_ms,"T":now_ms,"s":"TESTUSDT","p":"100","q":"1","m":True}})
            f=live_flow("TESTUSDT",60,20)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f["buy_share"],2/3,places=6)
        self.assertGreater(f["imbalance"],0)

    def test_armed_symbols_are_added_to_live_monitor(self):
        set_extra_symbol_provider(lambda: ("ZZZUSDT","AAAUSDT"))
        symbols=desired_symbols(10)
        self.assertIn("ZZZUSDT",symbols)
        self.assertIn("AAAUSDT",symbols)
        set_extra_symbol_provider(None)

    def test_card_says_do_not_enter_when_armed(self):
        s=_arm_signal("LONG","1H")
        # Populate fields expected by the rich card.
        s.professional_grade="A"; s.data_health_score=95; s.execution_quality=95
        s.impact_1k_bps=1; s.drift_label="stable"; s.alpha_adjustment=0
        s.alpha_raw_adjustment=0; s.alpha_fresh_score=80; s.alpha_momentum_percentile=70
        s.alpha_ofi_5m=.1; s.alpha_residual_pct=.2; s.alpha_residual_horizon="6h"
        s.l2_state="BALANCED"; s.l2_signed_imbalance_10=.1; s.micro_label="OK"
        s.meta_status="LEARNING"; s.meta_score=.5; s.cluster_id=0
        s.expected_window="6–48 часов"; s.review_window="4 часа"; s.rr=2; s.leverage=2
        s.entry_now_state="ARMED"; s.entry_now_score=70; s.entry_now_price=101
        s.entry_now_streak=0; s.entry_now_reason="waiting for flow"
        text=signal_card(s,True)
        self.assertIn("НЕ ВХОДИТЬ СЕЙЧАС",text)

    def test_card_marks_confirmed_entry_now(self):
        s=_arm_signal("LONG","1H")
        s.professional_grade="A"; s.data_health_score=95; s.execution_quality=95
        s.impact_1k_bps=1; s.drift_label="stable"; s.alpha_adjustment=0
        s.alpha_raw_adjustment=0; s.alpha_fresh_score=80; s.alpha_momentum_percentile=70
        s.alpha_ofi_5m=.1; s.alpha_residual_pct=.2; s.alpha_residual_horizon="6h"
        s.l2_state="BALANCED"; s.l2_signed_imbalance_10=.1; s.micro_label="OK"
        s.meta_status="LEARNING"; s.meta_score=.5; s.cluster_id=0
        s.expected_window="6–48 часов"; s.review_window="4 часа"; s.rr=2; s.leverage=2
        s.entry_now_state="ENTER_NOW"; s.entry_now_score=91; s.entry_now_price=101
        s.entry_now_streak=2; s.entry_now_reason="persistent micro confirmation ready"
        text=signal_card(s,True)
        self.assertIn("ENTRY NOW CONFIRMED",text)
        self.assertIn("ВХОД СЕЙЧАС",text)

    def test_armed_symbols_have_priority_over_old_open_signals(self):
        set_extra_symbol_provider(lambda: ("BESTUSDT","SECONDUSDT"))
        old_rows=[
            {"symbol":"OLD1USDT","is_shadow":0},
            {"symbol":"OLD2USDT","is_shadow":0},
            {"symbol":"OLD3USDT","is_shadow":0},
        ]
        with patch("v11_live.open_signals",return_value=old_rows):
            symbols=desired_symbols(4)
        self.assertEqual(
            symbols,("BTCUSDT","BESTUSDT","SECONDUSDT","OLD1USDT")
        )
        set_extra_symbol_provider(None)

    def _insert_safety_signal(
        self,pnl,shadow=False,reason=None,status="CLOSED",
        symbol="TESTUSDT",hours_ago=0
    ):
        import sqlite3
        from app.config import DATABASE_PATH
        offset=f"-{int(hours_ago)} hours"
        with db_session() as c:
            cur=c.execute("""
                INSERT INTO signals(
                    created_at,symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,
                    status,closed_at,result,pnl_r,activated_at,release_version,
                    is_shadow,shadow_reason,delivery_state,delivered_at
                ) VALUES(
                    datetime('now',?),?,'1H','LONG',90,100,95,105,110,115,
                    ?,CURRENT_TIMESTAMP,?, ?,CURRENT_TIMESTAMP,'11.7.1',
                    ?,?,'DELIVERED',CURRENT_TIMESTAMP
                )
            """,(
                offset,str(symbol),
                status,
                "TP2" if float(pnl)>0 else "SL",
                float(pnl),
                1 if shadow else 0,
                reason
            ))
            return int(cur.lastrowid)

    def test_new_entry_now_engine_starts_in_canary(self):
        s=futures_safety_status()
        self.assertEqual(s.mode,"CANARY")
        self.assertFalse(s.allow_live)
        self.assertEqual(s.probe_reason,CANARY_REASON)

    def test_canary_needs_forward_shadow_evidence_before_live(self):
        samples=[
            (-.5,"AAAUSDT",4),(-.4,"BBBUSDT",3),(1.0,"CCCUSDT",2),
            (.8,"AAAUSDT",1),(.7,"BBBUSDT",0),
        ]
        for pnl,symbol,hours_ago in samples:
            self._insert_safety_signal(
                pnl,shadow=True,reason=CANARY_REASON,
                symbol=symbol,hours_ago=hours_ago
            )
        s=futures_safety_status()
        self.assertEqual(s.mode,"LIVE")
        self.assertTrue(s.allow_live)

    def test_canary_does_not_promote_one_correlated_batch(self):
        for pnl in (-.5,-.4,1.0,.8,.7):
            self._insert_safety_signal(
                pnl,shadow=True,reason=CANARY_REASON,
                symbol="SAMEUSDT",hours_ago=0
            )
        s=futures_safety_status()
        self.assertEqual(s.mode,"CANARY")
        self.assertFalse(s.allow_live)
        self.assertLess(s.probe_distinct_symbols,3)

    def test_three_consecutive_live_losses_trip_circuit(self):
        # First prove canary.
        samples=[
            (-.5,"AAAUSDT",4),(-.4,"BBBUSDT",3),(1.0,"CCCUSDT",2),
            (.8,"AAAUSDT",1),(.7,"BBBUSDT",0),
        ]
        for pnl,symbol,hours_ago in samples:
            self._insert_safety_signal(
                pnl,shadow=True,reason=CANARY_REASON,
                symbol=symbol,hours_ago=hours_ago
            )
        self.assertEqual(futures_safety_status().mode,"LIVE")
        for pnl in (-1.0,-1.0,-1.0):
            self._insert_safety_signal(pnl,shadow=False)
        s=futures_safety_status()
        self.assertEqual(s.mode,"CIRCUIT_PAUSE")
        self.assertFalse(s.allow_live)
        self.assertGreaterEqual(s.consecutive_losses,3)
        self.assertEqual(s.probe_reason,CIRCUIT_REASON)

    def test_rolling_drawdown_pauses_even_without_three_consecutive_losses(self):
        samples=[
            (-.5,"AAAUSDT",4),(-.4,"BBBUSDT",3),(1.0,"CCCUSDT",2),
            (.8,"AAAUSDT",1),(.7,"BBBUSDT",0),
        ]
        for pnl,symbol,hours_ago in samples:
            self._insert_safety_signal(
                pnl,shadow=True,reason=CANARY_REASON,
                symbol=symbol,hours_ago=hours_ago
            )
        self.assertEqual(futures_safety_status().mode,"LIVE")
        # Newest-first sequence becomes -0.3, -1.0, +0.2, -1.0: only two
        # consecutive losses, but rolling net is -2.1R.
        for pnl in (-1.0,.2,-1.0,-.3):
            self._insert_safety_signal(pnl,shadow=False)
        s=futures_safety_status()
        self.assertEqual(s.mode,"CIRCUIT_PAUSE")
        self.assertFalse(s.allow_live)
        self.assertLessEqual(s.rolling_net_r,-2.0)
        self.assertIn("rolling",s.reason)

    def test_circuit_requires_recovery_shadows_to_resume(self):
        samples=[
            (-.5,"AAAUSDT",4),(-.4,"BBBUSDT",3),(1.0,"CCCUSDT",2),
            (.8,"AAAUSDT",1),(.7,"BBBUSDT",0),
        ]
        for pnl,symbol,hours_ago in samples:
            self._insert_safety_signal(
                pnl,shadow=True,reason=CANARY_REASON,
                symbol=symbol,hours_ago=hours_ago
            )
        self.assertEqual(futures_safety_status().mode,"LIVE")
        for pnl in (-1.0,-1.0,-1.0):
            self._insert_safety_signal(pnl,shadow=False)
        paused=futures_safety_status()
        self.assertEqual(paused.mode,"CIRCUIT_PAUSE")
        recovery=[
            (-.5,"DDDUSDT",4),(-.4,"EEEUSDT",3),(1.0,"FFFUSDT",2),
            (.8,"DDDUSDT",1),(.7,"EEEUSDT",0),
        ]
        for pnl,symbol,hours_ago in recovery:
            self._insert_safety_signal(
                pnl,shadow=True,reason=CIRCUIT_REASON,
                symbol=symbol,hours_ago=hours_ago
            )
        resumed=futures_safety_status()
        self.assertEqual(resumed.mode,"LIVE")
        self.assertTrue(resumed.allow_live)

    def test_legacy_delivered_active_futures_trade_still_consumes_risk_slot(self):
        with db_session() as c:
            c.execute("""
                INSERT INTO signals(
                    created_at,symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,
                    status,activated_at,release_version,is_shadow,
                    delivery_state,delivered_at
                ) VALUES(
                    CURRENT_TIMESTAMP,'LEGACYUSDT','1H','LONG',88,100,95,105,110,115,
                    'ACTIVE',CURRENT_TIMESTAMP,'11.4.1',0,
                    'DELIVERED',CURRENT_TIMESTAMP
                )
            """)
        self.assertGreaterEqual(futures_risk_module.active_live_count(),1)
        text=active_trades_text()
        self.assertIn("LEGACYUSDT",text)

    def test_only_one_live_entry_now_trade_can_be_active(self):
        samples=[
            (-.5,"AAAUSDT",4),(-.4,"BBBUSDT",3),(1.0,"CCCUSDT",2),
            (.8,"AAAUSDT",1),(.7,"BBBUSDT",0),
        ]
        for pnl,symbol,hours_ago in samples:
            self._insert_safety_signal(
                pnl,shadow=True,reason=CANARY_REASON,
                symbol=symbol,hours_ago=hours_ago
            )
        self.assertEqual(futures_safety_status().mode,"LIVE")
        self._insert_safety_signal(0.0,shadow=False,status="ACTIVE")
        s=futures_safety_status()
        self.assertEqual(s.mode,"POSITION_BUSY")
        self.assertFalse(s.allow_live)

    def test_active_registry_keeps_delivered_trade_visible(self):
        import sqlite3
        from app.config import DATABASE_PATH
        with db_session() as c:
            c.execute("""
                INSERT INTO signals(
                    created_at,symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,
                    status,activated_at,release_version,is_shadow,delivery_state,delivered_at
                ) VALUES(
                    CURRENT_TIMESTAMP,'BTCUSDT','1H','LONG',90,100,95,105,110,115,
                    'ACTIVE',CURRENT_TIMESTAMP,'11.7.1',0,'DELIVERED',CURRENT_TIMESTAMP
                )
            """)
        text=active_trades_text()
        self.assertIn("BTCUSDT",text)
        self.assertIn("ACTIVE",text)
        self.assertIn("не исчезает",text)

    @patch("bot_v11100.mark_entry_triggered")
    @patch("bot_v11100.db.activate_signal")
    def test_delayed_telegram_delivery_still_makes_entry_now_active(self,activate,mark_arm):
        import json, sqlite3
        from app.config import DATABASE_PATH
        feature_json=json.dumps({
            "delivery_meta":{"source":"entry_now_v1142"},
            "entry_now_v1142":{"arm_id":77},
        })
        with db_session() as c:
            cur=c.execute("""
                INSERT INTO signals(
                    created_at,symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,
                    status,feature_json,release_version,is_shadow,delivery_state,delivered_at
                ) VALUES(
                    CURRENT_TIMESTAMP,'ETHUSDT','1H','LONG',90,100,95,105,110,115,
                    'SENT',?,'11.5.2',0,'DELIVERED',CURRENT_TIMESTAMP
                )
            """,(feature_json,))
            signal_id=int(cur.lastrowid)
        self.assertTrue(_activate_delivered_entry_now(signal_id))
        self.assertEqual(activate.call_args.args[0],signal_id)
        self.assertEqual(mark_arm.call_args.args,(77,signal_id))

    def test_spot_rsi_and_mfi_handle_one_sided_trends_correctly(self):
        up=_spot_frame(240,"1D",100,.50)
        up["open"]=up["close"]-.10
        up["high"]=up["close"]+.20
        up["low"]=up["open"]-.10
        up["taker_buy_base"]=up["volume"]*.60
        xu=spot_enrich(up)
        self.assertGreater(float(xu.iloc[-1]["rsi"]),95.0)
        self.assertGreater(float(xu.iloc[-1]["mfi"]),95.0)

        down=_spot_frame(240,"1D",300,-.50)
        down["open"]=down["close"]+.10
        down["high"]=down["open"]+.10
        down["low"]=down["close"]-.20
        down["taker_buy_base"]=down["volume"]*.40
        xd=spot_enrich(down)
        self.assertLess(float(xd.iloc[-1]["rsi"]),5.0)
        self.assertLess(float(xd.iloc[-1]["mfi"]),5.0)

    def test_spot_path_continuity_rewards_smooth_trend(self):
        smooth=spot_enrich(_spot_frame(240,"1D",100,.35))
        jump=_spot_frame(240,"1D",100,.10)
        jump.loc[jump.index[-5]:,"close"] += 25
        jump.loc[jump.index[-5]:,"open"] += 25
        jump.loc[jump.index[-5]:,"high"] += 25
        jump.loc[jump.index[-5]:,"low"] += 25
        jump=spot_enrich(jump)
        self.assertGreater(float(smooth.iloc[-1].path_eff14),.25)
        self.assertGreater(float(jump.iloc[-1].max_day14),12)

    def test_spot_news_positive_requires_two_sources(self):
        snap={"sources":3,"event_risk":0,"items":[
            {"title":"Solana integration expands institutional adoption","source":"A","age_minutes":60},
            {"title":"SOL partnership launches new payment integration","source":"B","age_minutes":90},
        ]}
        a=spot_news_assess(snap,"SOL")
        self.assertTrue(a["catalyst"]); self.assertEqual(a["adjustment"],3)
        b=spot_news_assess({"sources":3,"items":[snap["items"][0]]},"SOL")
        self.assertFalse(b["catalyst"])

    def test_spot_news_severe_negative_blocks_buy(self):
        snap={"sources":2,"items":[
            {"title":"Solana suffers exploit and stolen funds","source":"A","age_minutes":30},
        ]}
        a=spot_news_assess(snap,"SOL")
        self.assertTrue(a["block"]); self.assertLess(a["adjustment"],0)

    def test_spot_news_outage_is_penalty_not_fake_neutral(self):
        a=spot_news_assess({"sources":0,"items":[]},"SOL")
        self.assertTrue(a["degraded"]); self.assertEqual(a["adjustment"],-3)

    def test_spot_l2_good_book_is_healthy(self):
        book={"bids":[(100.0,100),(99.95,100),(99.9,100)],
              "asks":[(100.02,100),(100.05,100),(100.1,100)]}
        now=1_000_000
        trades=[{"time_ms":now-i*1000,"notional":1000,"buyer_taker":i%3!=0} for i in range(30)]
        m=spot_analyze_book(book,trades,now_ms=now)
        self.assertTrue(m["healthy"]); self.assertLess(m["impact_5k_bps"],20)
        self.assertGreater(m["buy_share"],.5)

    def test_spot_l2_thin_book_rejects_execution(self):
        book={"bids":[(100.0,1)],"asks":[(101.0,1)]}
        m=spot_analyze_book(book,[],now_ms=1_000_000)
        self.assertFalse(m["healthy"]); self.assertGreater(m["spread_bps"],8)

    def _spot_candidate(self,price_shift=0,news=None,micro=None,derivatives=None,market=None):
        d=_spot_frame(240,"1D",100,.35)
        h4=_spot_frame(300,"4H",150,.08)
        h1=_spot_frame(300,"1H",170,.025)
        if price_shift:
            for frame in (h4,h1):
                for col in ("open","high","low","close"):
                    frame.loc[frame.index[-3]:,col]+=price_shift
        news=news or {
            "block":False,"recent_negative":False,"catalyst":False,
            "adjustment":0,"sources":3,"degraded":False,"global_breaking":False,
        }
        micro=micro or {
            "healthy":True,"excellent":True,"spread_bps":2.0,"impact_5k_bps":5.0,
            "book_imbalance_20bps":.1,"flow_reliable":True,
            "closed_flow_ok":True,"closed_buy_share_5m":.55,"closed_buy_share_15m":.54,
            "buy_share":.57,"ask":float(h4.iloc[-1].close),
        }
        derivatives=derivatives or {"available":True,"funding":.0001,"oi_change_pct":1,"global_ls":1.1,
                                     "top_position_ls":1.1,"taker_ratio":1.05}
        market=market or {
            "regime":"BULL","breadth":.62,"dispersion_7d":8.0,
            "dispersion_risk":False,"risk_off":False,
        }
        return spot_analyze("TESTUSDT","TEST",d,h4,h1,92,7.0,market,news,micro,derivatives)

    def test_spot_strong_candidate_can_be_buy_or_watch_but_not_false_buy_outside_zone(self):
        s=self._spot_candidate()
        self.assertIsNotNone(s)
        self.assertIn(s.status,("BUY","WATCH"))
        if s.status=="BUY":
            self.assertGreaterEqual(s.score,82)
            self.assertLessEqual(s.entry_low,float(s.micro.get("ask")))
            self.assertGreaterEqual(s.entry_high,float(s.micro.get("ask")))

    def test_spot_overextension_never_buy(self):
        s=self._spot_candidate(price_shift=25)
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_negative_news_never_buy(self):
        s=self._spot_candidate(news={"block":True,"catalyst":False,"adjustment":-6,"sources":2})
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_extreme_futures_crowding_never_buy(self):
        d={"available":True,"funding":.0025,"oi_change_pct":8,"global_ls":3,
           "top_position_ls":3,"taker_ratio":1.4}
        self.assertTrue(spot_crowding(d)["extreme"])
        s=self._spot_candidate(derivatives=d)
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_menu_is_separate_from_futures(self):
        labels=[button.text for row in main_menu().inline_keyboard for button in row]
        self.assertTrue(any("SPOT" in x for x in labels))
        self.assertTrue(any("FUTURES" in x for x in labels))

    def test_spot_database_is_separate_table(self):
        import sqlite3
        from app.config import DATABASE_PATH
        with db_session() as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("spot_signals",tables)
        self.assertIn("signals",tables)

    def test_spot_auto_does_not_send_watch_contract(self):
        src=Path(__file__).with_name("bot_v11100.py").read_text(encoding="utf-8")
        start=src.index("async def spot_auto_job")
        end=src.index("async def spot_tracker_job",start)
        block=src[start:end]
        self.assertIn('s.status!="BUY"',block)
        self.assertIn("WATCH/READY stays silent",block)
        self.assertIn("enqueue_spot_delivery",block)
        self.assertIn("if streak<2:",block)
        self.assertNotIn('s.status=="WATCH"',block)

    def test_spot_outbox_is_independent_and_durable(self):
        import sqlite3
        from app.config import DATABASE_PATH
        with db_session() as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("spot_deliveries",tables)
        src=Path(__file__).with_name("bot_v11100.py").read_text(encoding="utf-8")
        self.assertIn("spot_delivery_retry_job",src)
        self.assertIn("pending_spot_deliveries",src)

    def test_spot_exact_buy_is_reachable_on_controlled_pullback(self):
        d=_spot_frame(240,"1D",100,.35)
        h4=_spot_frame(300,"4H",150,.08)
        h1=_spot_frame(300,"1H",170,.025)
        for k in range(10):
            idx=h4.index[-10+k]; shift=-(k+1)*.10
            for col in ("open","high","low","close"):
                h4.loc[idx,col]+=shift
        # The exact-BUY reachability fixture must also satisfy the new 1H
        # acceleration gate. A tiny recent acceleration keeps MACD histogram
        # positive without creating an overextended tape.
        for k in range(8):
            idx=h1.index[-8+k]; shift=(k+1)*.008
            for col in ("open","high","low","close"):
                h1.loc[idx,col]+=shift
        live=float(h4.iloc[-1].close)
        s=spot_analyze(
            "TESTUSDT","TEST",d,h4,h1,92,7.0,
            {"regime":"BULL","breadth":.62,"dispersion_7d":8.0,"dispersion_risk":False,"risk_off":False},
            {"block":False,"recent_negative":False,"catalyst":False,"adjustment":0,
             "sources":3,"degraded":False,"global_breaking":False},
            {"healthy":True,"excellent":True,"spread_bps":2,"impact_5k_bps":5,
             "book_imbalance_20bps":.1,"flow_reliable":True,"closed_flow_ok":True,
             "closed_buy_share_5m":.55,"closed_buy_share_15m":.54,
             "buy_share":.57,"ask":live},
            {"available":True,"funding":.0001,"oi_change_pct":1,"global_ls":1.1,
             "top_position_ls":1.1,"taker_ratio":1.05},
        )
        self.assertIsNotNone(s)
        self.assertEqual(s.status,"BUY")
        self.assertLessEqual(s.entry_low,live); self.assertGreaterEqual(s.entry_high,live)

    def test_spot_live_ask_can_downgrade_closed_candle_buy_to_watch(self):
        d=_spot_frame(240,"1D",100,.35)
        h4=_spot_frame(300,"4H",150,.08)
        h1=_spot_frame(300,"1H",170,.025)
        for k in range(10):
            idx=h4.index[-10+k]; shift=-(k+1)*.10
            for col in ("open","high","low","close"):
                h4.loc[idx,col]+=shift
        closed=float(h4.iloc[-1].close)
        live=closed+2.5
        s=spot_analyze(
            "TESTUSDT","TEST",d,h4,h1,92,7.0,
            {"regime":"BULL","breadth":.62,"dispersion_7d":8.0,"dispersion_risk":False,"risk_off":False},
            {"block":False,"recent_negative":False,"catalyst":False,"adjustment":0,
             "sources":3,"degraded":False,"global_breaking":False},
            {"healthy":True,"excellent":True,"spread_bps":2,"impact_5k_bps":5,
             "book_imbalance_20bps":.1,"flow_reliable":True,"closed_flow_ok":True,
             "closed_buy_share_5m":.55,"closed_buy_share_15m":.54,
             "buy_share":.57,"ask":live},
            {"available":False},
        )
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_buy_requires_reliable_live_taker_flow(self):
        s=self._spot_candidate(micro={"healthy":True,"excellent":True,"spread_bps":2.0,
            "impact_5k_bps":5.0,"book_imbalance_20bps":.1,"flow_reliable":False,
            "buy_share":.57,"ask":173.0})
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_tick_normalization_preserves_geometry(self):
        class S: pass
        s=S(); s.entry_low=100.011; s.entry_high=100.089; s.invalidation=98.034
        s.tp1=102.071; s.tp2=104.119; s.tp3=106.191; s.feature_snapshot={}
        meta=SpotMeta("TESTUSDT","TEST","USDT","TRADING",.1,.1,1_000_000,.001,.001,5)
        out=spot_normalize(s,meta)
        self.assertIsNotNone(out)
        self.assertEqual(out.entry_low,100.0)
        self.assertEqual(out.entry_high,100.1)
        self.assertTrue(out.invalidation<out.entry_low<out.tp1<out.tp2<out.tp3)

    def test_spot_news_age_zero_is_not_treated_as_stale(self):
        snap={"sources":2,"items":[
            {"title":"Solana exploit causes stolen funds","source":"A","age_minutes":0},
        ]}
        a=spot_news_assess(snap,"SOL")
        self.assertTrue(a["block"])
        self.assertEqual(a["relevant"][0]["age_min"],0.0)

    def test_spot_news_can_use_existing_asset_tag(self):
        snap={"sources":2,"items":[
            {"title":"Protocol announces upgrade","source":"A","age_minutes":10,"assets":["SOL"]},
            {"title":"Network launches institutional integration","source":"B","age_minutes":20,"assets":["SOL"]},
        ]}
        a=spot_news_assess(snap,"SOL")
        self.assertTrue(a["catalyst"])

    def test_spot_buy_requires_buyer_dominant_live_flow(self):
        s=self._spot_candidate(micro={"healthy":True,"excellent":False,"spread_bps":2.0,
            "impact_5k_bps":5.0,"book_imbalance_20bps":.1,"flow_reliable":True,
            "closed_flow_ok":True,"closed_buy_share_5m":.55,"closed_buy_share_15m":.54,
            "buy_share":.50,"ask":173.0})
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_news_degraded_downgrades_buy(self):
        s=self._spot_candidate(news={"block":False,"catalyst":False,"adjustment":-3,
                                     "sources":0,"degraded":True})
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_futures_overlay_failure_downgrades_buy(self):
        crowd=spot_crowding({"available":False,"counterpart":True,"degraded":True})
        self.assertTrue(crowd["degraded"])
        s=self._spot_candidate(derivatives={"available":False,"counterpart":True,"degraded":True})
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_no_futures_counterpart_is_not_fake_degradation(self):
        crowd=spot_crowding({"available":False,"counterpart":False,"degraded":False})
        self.assertFalse(crowd["degraded"])

    def test_spot_outbox_expires_stale_undelivered_signal(self):
        s=self._spot_candidate()
        self.assertIsNotNone(s)
        # Force a persistable BUY if synthetic shape happens to be WATCH after stricter gates.
        s.status="BUY"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,123,"x")
        import sqlite3
        from app.config import DATABASE_PATH
        with db_session() as c:
            c.execute("UPDATE spot_deliveries SET created_at=datetime('now','-2 hours') WHERE spot_signal_id=?",(sid,))
        self.assertTrue(spot_was_sent_recently(s.symbol,72))
        self.assertEqual(expire_spot_deliveries(45),1)
        self.assertEqual(pending_spot_deliveries(10),[])
        self.assertFalse(spot_was_sent_recently(s.symbol,72))

    @patch("spot_tracker.klines_range")
    @patch("spot_tracker.klines")
    def test_spot_tracker_anchors_to_delivery_and_reconstructs_partial_hour(self,mock_klines,mock_range):
        # Signal delivered at 10:30. The 10:00 hourly candle has a fake huge high
        # before delivery and must never be used. Closed 1m bars after 10:30 are
        # included, so real post-delivery movement in that hour is not lost.
        frame=pd.DataFrame([
            {"open_time":pd.Timestamp("2026-01-01T10:00:00Z"),"close_time":pd.Timestamp("2026-01-01T10:59:59Z"),
             "open":100,"high":150,"low":90,"close":101,"volume":1,"quote_volume":1,"trades":1,"taker_buy_base":.5,"taker_buy_quote":.5},
            {"open_time":pd.Timestamp("2026-01-01T11:00:00Z"),"close_time":pd.Timestamp("2026-01-01T11:59:59Z"),
             "open":101,"high":103,"low":99,"close":102,"volume":1,"quote_volume":1,"trades":1,"taker_buy_base":.5,"taker_buy_quote":.5},
        ])
        partial=pd.DataFrame([
            {"open_time":pd.Timestamp("2026-01-01T10:31:00Z"),"close_time":pd.Timestamp("2026-01-01T10:31:59Z"),
             "open":100,"high":105,"low":98,"close":104,"volume":1,"quote_volume":1,"trades":1,"taker_buy_base":.5,"taker_buy_quote":.5},
        ])
        mock_klines.return_value=frame; mock_range.return_value=partial
        row={"id":1,"symbol":"TESTUSDT","created_at":"2026-01-01T10:00:00+00:00",
             "delivered_at":"2026-01-01T10:30:00+00:00","entry_price":100.0,
             "invalidation":80.0,"tp1":120.0,"tp2":130.0,"tp3":140.0,
             "max_favorable_pct":0,"max_adverse_pct":0,"partial_hour_processed":0,
             "tp1_hit":0,"tp2_hit":0,"tp3_hit":0,
             "invalidated":0,"return_3d":None,"return_5d":None,"return_7d":None,"return_10d":None,"state":"OPEN"}
        import asyncio
        with patch("spot_tracker.update_metrics") as update:
            ok=asyncio.run(update_spot_one(row))
        self.assertTrue(ok)
        metrics=update.call_args.kwargs
        self.assertAlmostEqual(metrics["max_favorable_pct"],5.0,places=6)
        self.assertEqual(metrics["tp1_hit"],0)
        self.assertEqual(metrics["partial_hour_processed"],1)

    def _spot_minute_flow_frame(self,buy_share=.55,n=30):
        rows=[]
        base=pd.Timestamp("2026-01-01T00:00:00Z")
        for i in range(n):
            volume=1000.0
            rows.append({
                "open_time":base+pd.Timedelta(minutes=i),
                "close_time":base+pd.Timedelta(minutes=i+1)-pd.Timedelta(milliseconds=1),
                "open":100.0,"high":100.1,"low":99.9,"close":100.0,
                "volume":volume,"quote_volume":100_000.0,
                "taker_buy_base":volume*buy_share,
            })
        return pd.DataFrame(rows)

    def test_spot_live_burst_requires_closed_1m_backdrop(self):
        now=2_000_000
        book={"bids":[(100,100)],"asks":[(100.01,100)]}
        trades=[
            {"time_ms":now-i*1000,"notional":2000,"buyer_taker":True}
            for i in range(20)
        ]
        no_context=spot_analyze_book(book,trades,now_ms=now)
        self.assertTrue(no_context["live_flow_reliable"])
        self.assertFalse(no_context["flow_reliable"])

        with_context=spot_analyze_book(
            book,trades,now_ms=now,
            minute_frame=self._spot_minute_flow_frame(.55)
        )
        self.assertTrue(with_context["flow_reliable"])
        self.assertGreaterEqual(with_context["closed_buy_share_15m"],.50)

    def test_spot_any_recent_negative_news_blocks_buy_eligibility(self):
        s=self._spot_candidate(news={
            "block":False,"recent_negative":True,"catalyst":False,
            "adjustment":-2,"sources":3,"degraded":False,"global_breaking":False,
        })
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_risk_off_market_never_buy(self):
        s=self._spot_candidate(market={
            "regime":"NEUTRAL","breadth":.34,"dispersion_7d":8.0,
            "dispersion_risk":False,"risk_off":True,
        })
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_neutral_regime_requires_higher_relative_strength(self):
        d=_spot_frame(240,"1D",100,.35)
        h4=_spot_frame(300,"4H",150,.08)
        h1=_spot_frame(300,"1H",170,.025)
        live=float(h4.iloc[-1].close)
        s=spot_analyze(
            "TESTUSDT","TEST",d,h4,h1,80,7.0,
            {"regime":"NEUTRAL","breadth":.50,"dispersion_7d":8.0,
             "dispersion_risk":False,"risk_off":False},
            {"block":False,"recent_negative":False,"catalyst":False,"adjustment":0,
             "sources":3,"degraded":False,"global_breaking":False},
            {"healthy":True,"excellent":True,"spread_bps":2,"impact_5k_bps":5,
             "book_imbalance_20bps":.1,"flow_reliable":True,"closed_flow_ok":True,
             "closed_buy_share_5m":.55,"closed_buy_share_15m":.54,
             "buy_share":.57,"ask":live},
            {"available":True,"funding":.0001,"oi_change_pct":1,"global_ls":1.1,
             "top_position_ls":1.1,"taker_ratio":1.05},
        )
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_first_delivery_updates_entry_price(self):
        s=self._spot_candidate()
        self.assertIsNotNone(s)
        s.status="BUY"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,123,"x")
        delivery_id=pending_spot_deliveries(1)[0][0]
        mark_spot_delivery_sent(delivery_id,sid,123.456)
        with db_session(row_factory=sqlite3.Row) as c:
            row=c.execute(
                "SELECT delivered_at,entry_price FROM spot_signals WHERE id=?",(sid,)
            ).fetchone()
        self.assertIsNotNone(row["delivered_at"])
        self.assertAlmostEqual(float(row["entry_price"]),123.456,places=6)

    def test_spot_flow_rejects_stale_last_trade(self):
        now=2_000_000
        book={"bids":[(100,100)],"asks":[(100.01,100)]}
        trades=[
            {"time_ms":now-5*60_000-i*1000,"notional":2000,"buyer_taker":True}
            for i in range(20)
        ]
        m=spot_analyze_book(book,trades,now_ms=now)
        self.assertFalse(m["flow_reliable"])
        self.assertGreater(m["latest_trade_age_sec"],120)

    def test_spot_global_breaking_event_downgrades_buy(self):
        s=self._spot_candidate(news={"block":False,"catalyst":False,"adjustment":0,
                                     "sources":3,"degraded":False,"global_breaking":True})
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_extreme_dispersion_downgrades_buy(self):
        market={"regime":"BULL","breadth":.62,"dispersion_7d":22.0,"dispersion_risk":True}
        s=self._spot_candidate(market=market)
        if s is not None:
            self.assertNotEqual(s.status,"BUY")

    def test_spot_undelivered_expired_rows_do_not_pollute_stats_or_history(self):
        s=self._spot_candidate(); self.assertIsNotNone(s); s.status="BUY"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,123,"x")
        with db_session() as c:
            c.execute("UPDATE spot_deliveries SET created_at=datetime('now','-2 hours') WHERE spot_signal_id=?",(sid,))
        expire_spot_deliveries(10)
        stats=spot_db_stats()
        self.assertEqual(int(stats.get("issued") or 0),0)
        self.assertEqual(recent_spot_signals(10),[])
        with db_session() as c:
            row=c.execute("SELECT state,result FROM spot_signals WHERE id=?",(sid,)).fetchone()
        self.assertEqual(tuple(row),("CLOSED","DELIVERY_EXPIRED"))

    @patch("spot_tracker.klines_range")
    @patch("spot_tracker.klines")
    def test_spot_tracker_catches_invalidation_inside_delivery_hour(self,mock_klines,mock_range):
        mock_klines.return_value=pd.DataFrame([
            {"open_time":pd.Timestamp("2026-01-01T11:00:00Z"),"close_time":pd.Timestamp("2026-01-01T11:59:59Z"),
             "open":100,"high":101,"low":99,"close":100,"volume":1,"quote_volume":1,"trades":1,"taker_buy_base":.5,"taker_buy_quote":.5},
        ])
        mock_range.return_value=pd.DataFrame([
            {"open_time":pd.Timestamp("2026-01-01T10:31:00Z"),"close_time":pd.Timestamp("2026-01-01T10:31:59Z"),
             "open":100,"high":101,"low":94,"close":95,"volume":1,"quote_volume":1,"trades":1,"taker_buy_base":.5,"taker_buy_quote":.5},
        ])
        row={"id":7,"symbol":"TESTUSDT","created_at":"2026-01-01T10:00:00+00:00",
             "delivered_at":"2026-01-01T10:30:00+00:00","entry_price":100.0,
             "invalidation":95.0,"tp1":110.0,"tp2":120.0,"tp3":130.0,
             "max_favorable_pct":0,"max_adverse_pct":0,"partial_hour_processed":0,
             "tp1_hit":0,"tp2_hit":0,"tp3_hit":0,"invalidated":0,
             "return_3d":None,"return_5d":None,"return_7d":None,"return_10d":None,"state":"OPEN"}
        import asyncio
        with patch("spot_tracker.update_metrics") as update:
            self.assertTrue(asyncio.run(update_spot_one(row)))
        metrics=update.call_args.kwargs
        self.assertEqual(metrics["invalidated"],1)
        self.assertEqual(metrics["state"],"CLOSED")
        self.assertEqual(metrics["result"],"INVALIDATED")

    def test_httpx_info_logging_is_disabled_to_protect_bot_token(self):
        import logging
        self.assertGreaterEqual(logging.getLogger("httpx").level,logging.WARNING)
        self.assertGreaterEqual(logging.getLogger("httpcore").level,logging.WARNING)

    def test_futures_safety_release_key_is_current(self):
        import sqlite3
        from app.config import DATABASE_PATH
        init_futures_safety()
        with db_session() as c:
            row=c.execute("SELECT release_key FROM v1142_safety WHERE id=1").fetchone()
        self.assertEqual(row[0],"11.7.1")

    def test_spot_tick_normalization_property_grid_and_geometry(self):
        rng=random.Random(1151)
        ticks=(1.0,.1,.01,.001,.0001,.00001)
        for _ in range(300):
            tick=rng.choice(ticks)
            base=max(tick*50,rng.uniform(.02,5000))
            risk=max(tick*5,base*rng.uniform(.005,.04))
            class S: pass
            s=S()
            s.entry_low=base+rng.uniform(-.2,.2)*risk
            s.entry_high=s.entry_low+rng.uniform(.1,.5)*risk
            s.invalidation=s.entry_low-rng.uniform(.7,1.5)*risk
            s.tp1=s.entry_high+rng.uniform(.6,1.2)*risk
            s.tp2=s.tp1+rng.uniform(.4,1.0)*risk
            s.tp3=s.tp2+rng.uniform(.4,1.0)*risk
            s.feature_snapshot={}
            meta=SpotMeta("TESTUSDT","TEST","USDT","TRADING",tick,tick,1e12,.001,.001,5)
            out=spot_normalize(s,meta)
            self.assertIsNotNone(out)
            values=(out.invalidation,out.entry_low,out.entry_high,out.tp1,out.tp2,out.tp3)
            self.assertTrue(values[0]<values[1]<=values[2]<values[3]<values[4]<values[5])
            dtick=Decimal(str(tick))
            for value in values:
                self.assertEqual(Decimal(str(value)) % dtick,Decimal("0"))

    def test_spot_v1150_schema_migrates_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/"old.db")
            c=sqlite3.connect(path)
            try:
                # Minimal V11.5.0-shaped tables: no partial_hour_processed / expired_at.
                c.execute("""
                    CREATE TABLE spot_signals(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL, delivered_at TEXT,
                        symbol TEXT NOT NULL, base_asset TEXT NOT NULL,
                        signal_status TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN',
                        score REAL NOT NULL, setup_type TEXT,
                        entry_low REAL NOT NULL, entry_high REAL NOT NULL, entry_price REAL NOT NULL,
                        invalidation REAL NOT NULL, tp1 REAL NOT NULL, tp2 REAL NOT NULL, tp3 REAL NOT NULL,
                        feature_json TEXT, market_regime TEXT, relative_percentile REAL, excess_btc_14d REAL,
                        max_favorable_pct REAL NOT NULL DEFAULT 0, max_adverse_pct REAL NOT NULL DEFAULT 0,
                        return_3d REAL, return_5d REAL, return_7d REAL, return_10d REAL,
                        tp1_hit INTEGER NOT NULL DEFAULT 0, tp2_hit INTEGER NOT NULL DEFAULT 0,
                        tp3_hit INTEGER NOT NULL DEFAULT 0, invalidated INTEGER NOT NULL DEFAULT 0,
                        closed_at TEXT, result TEXT, release_version TEXT
                    )
                """)
                c.execute("""
                    CREATE TABLE spot_deliveries(
                        id INTEGER PRIMARY KEY AUTOINCREMENT, spot_signal_id INTEGER NOT NULL,
                        chat_id INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                        delivered_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                        UNIQUE(spot_signal_id,chat_id)
                    )
                """)
                c.execute("""
                    INSERT INTO spot_signals(
                        created_at,delivered_at,symbol,base_asset,signal_status,state,score,
                        entry_low,entry_high,entry_price,invalidation,tp1,tp2,tp3,release_version
                    ) VALUES(
                        '2026-08-17T00:00:00+00:00','2026-08-17T00:01:00+00:00',
                        'BTCUSDT','BTC','BUY','OPEN',90,100,101,100.5,95,105,110,115,'11.5.0'
                    )
                """)
                c.execute("INSERT INTO spot_deliveries(spot_signal_id,chat_id,payload,created_at) VALUES(1,123,'x','2026-08-17T00:00:00+00:00')")
                c.commit()
            finally:
                c.close()
            with patch.object(spot_db_module,"DATABASE_PATH",path):
                spot_db_module.init()
            c=sqlite3.connect(path)
            try:
                sigcols={r[1] for r in c.execute("PRAGMA table_info(spot_signals)")}
                delcols={r[1] for r in c.execute("PRAGMA table_info(spot_deliveries)")}
                self.assertIn("partial_hour_processed",sigcols)
                self.assertIn("expired_at",delcols)
                row=c.execute("SELECT symbol,score,partial_hour_processed FROM spot_signals WHERE id=1").fetchone()
                self.assertEqual(row,("BTCUSDT",90.0,0))
                self.assertEqual(c.execute("SELECT COUNT(*) FROM spot_deliveries").fetchone()[0],1)
            finally:
                c.close()

    def test_futures_safety_old_schema_adds_release_key_and_resets_canary(self):
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/"old-futures.db")
            c=sqlite3.connect(path)
            try:
                c.execute("""
                    CREATE TABLE signals(
                        id INTEGER PRIMARY KEY, is_shadow INTEGER DEFAULT 0,
                        delivery_state TEXT, release_version TEXT, status TEXT,
                        activated_at TEXT, closed_at TEXT, created_at TEXT,
                        result TEXT,pnl_r REAL
                    )
                """)
                c.execute("INSERT INTO signals(id,is_shadow,delivery_state,release_version,status) VALUES(7,0,'DELIVERED','11.5.0','CLOSED')")
                c.execute("""
                    CREATE TABLE v1142_safety(
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        canary_passed INTEGER NOT NULL DEFAULT 0,
                        paused_at TEXT,pause_reason TEXT,
                        baseline_signal_id INTEGER NOT NULL DEFAULT 0,
                        probe_baseline_id INTEGER NOT NULL DEFAULT 0,
                        resumed_at TEXT,updated_at TEXT
                    )
                """)
                c.execute("INSERT INTO v1142_safety(id,canary_passed,baseline_signal_id,probe_baseline_id) VALUES(1,1,1,1)")
                c.commit()
            finally:
                c.close()
            with patch.object(futures_risk_module,"DATABASE_PATH",path):
                futures_risk_module.init()
            c=sqlite3.connect(path)
            try:
                cols={r[1] for r in c.execute("PRAGMA table_info(v1142_safety)")}
                self.assertIn("release_key",cols)
                row=c.execute("SELECT canary_passed,baseline_signal_id,probe_baseline_id,release_key FROM v1142_safety WHERE id=1").fetchone()
                self.assertEqual(row,(0,7,7,"11.7.1"))
            finally:
                c.close()

    def test_spot_outbox_cancels_buy_if_fresh_ask_left_zone(self):
        s=self._spot_candidate()
        self.assertIsNotNone(s)
        s.status="BUY"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,777,"BUY")
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*args,**kwargs): self.sent.append((args,kwargs))
        bot=Bot()
        outside=float(s.entry_high)+max(.01,abs(float(s.entry_high))*0.02)
        book={"bids":[(outside-.01,1000)],"asks":[(outside,1000),(outside+.01,1000)]}
        now=int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        trades=[{"time_ms":now-i*1000,"notional":1000,"buyer_taker":True} for i in range(30)]
        with patch("bot_v11100.spot_local_book",return_value=book), \
             patch("bot_v11100.spot_book_stability",return_value={
                 "healthy":True,"stability_score":85,"bid_replenishment_ratio":1.0
             }), \
             patch("bot_v11100.spot_agg_trades",return_value=trades), \
             patch("bot_v11100.spot_klines",return_value=self._spot_minute_flow_frame(.55)):
            delivered=asyncio.run(_deliver_spot_pending(bot,forced_chat_ids={777}))
        self.assertEqual(delivered,0)
        self.assertEqual(bot.sent,[])
        with db_session() as c:
            d=c.execute("SELECT expired_at,last_error FROM spot_deliveries WHERE spot_signal_id=?",(sid,)).fetchone()
            sig=c.execute("SELECT delivered_at,state,result FROM spot_signals WHERE id=?",(sid,)).fetchone()
        self.assertIsNotNone(d[0]); self.assertIn("left BUY zone",d[1])
        self.assertEqual(tuple(sig),(None,"CLOSED","DELIVERY_EXPIRED"))

    def test_spot_old_release_pending_buy_is_expired_before_send(self):
        s=self._spot_candidate(); self.assertIsNotNone(s); s.status="BUY"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,1778,"BUY")
        with db_session() as c:
            c.execute(
                "UPDATE spot_signals SET release_version='11.7.0-calibrated-edge' WHERE id=?",
                (sid,)
            )
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*args,**kwargs): self.sent.append((args,kwargs))
        bot=Bot()
        delivered=asyncio.run(_deliver_spot_pending(bot,forced_chat_ids={1778}))
        self.assertEqual(delivered,0)
        self.assertEqual(bot.sent,[])
        with db_session() as c:
            d=c.execute(
                "SELECT expired_at,last_error FROM spot_deliveries WHERE spot_signal_id=?",
                (sid,)
            ).fetchone()
        self.assertIsNotNone(d[0])
        self.assertIn("release changed",str(d[1]).lower())

    def test_spot_final_delivery_rechecks_two_position_cap(self):
        s=self._spot_candidate(); self.assertIsNotNone(s); s.status="BUY"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,1779,"BUY")
        with db_session() as c:
            for idx,symbol in enumerate(("CAPAUSDT","CAPBUSDT"),1):
                c.execute("""
                    INSERT INTO spot_signals(
                        created_at,symbol,base_asset,signal_status,state,score,setup_type,
                        entry_price,entry_low,entry_high,invalidation,tp1,tp2,tp3,
                        delivered_at,market_regime,relative_percentile,excess_btc_14d,
                        feature_json,release_version
                    ) VALUES(
                        CURRENT_TIMESTAMP,?,?,'BUY','OPEN',90,'TEST',
                        100,99,101,95,105,110,115,CURRENT_TIMESTAMP,
                        'BULL',90,5,'{}','11.7.1-production-hardened'
                    )
                """,(symbol,symbol.removesuffix("USDT")))
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*args,**kwargs): self.sent.append((args,kwargs))
        bot=Bot()
        delivered=asyncio.run(_deliver_spot_pending(bot,forced_chat_ids={1779}))
        self.assertEqual(delivered,0)
        self.assertEqual(bot.sent,[])
        with db_session() as c:
            d=c.execute(
                "SELECT attempts,last_error FROM spot_deliveries WHERE spot_signal_id=?",
                (sid,)
            ).fetchone()
        self.assertGreaterEqual(int(d[0]),1)
        self.assertIn("portfolio cap",str(d[1]).lower())

    def test_spot_outbox_sends_only_with_fresh_ask_inside_zone(self):
        s=self._spot_candidate()
        self.assertIsNotNone(s)
        s.status="BUY"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,778,"BUY")
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*args,**kwargs): self.sent.append((args,kwargs))
        bot=Bot()
        ask=(float(s.entry_low)+float(s.entry_high))/2
        book={"bids":[(ask-.001,1000),(ask-.01,1000)],"asks":[(ask,1000),(ask+.01,1000)]}
        now=int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        trades=[{"time_ms":now-i*1000,"notional":1000,"buyer_taker":i%5!=0} for i in range(30)]
        with patch("bot_v11100.spot_local_book",return_value=book), \
             patch("bot_v11100.spot_book_stability",return_value={
                 "healthy":True,"stability_score":85,"bid_replenishment_ratio":1.0
             }), \
             patch("bot_v11100.spot_agg_trades",return_value=trades), \
             patch("bot_v11100.spot_klines",return_value=self._spot_minute_flow_frame(.55)), \
             patch("bot_v11100.core.get_news_sentiment",return_value={"sources":3,"items":[],"breaking_events":[]}):
            delivered=asyncio.run(_deliver_spot_pending(bot,forced_chat_ids={778}))
        self.assertEqual(delivered,1)
        self.assertEqual(len(bot.sent),1)
        with db_session() as c:
            d=c.execute("SELECT delivered_at,expired_at FROM spot_deliveries WHERE spot_signal_id=?",(sid,)).fetchone()
            sig=c.execute("SELECT delivered_at,state FROM spot_signals WHERE id=?",(sid,)).fetchone()
        self.assertIsNotNone(d[0]); self.assertIsNone(d[1])
        self.assertIsNotNone(sig[0]); self.assertEqual(sig[1],"OPEN")

    def test_spot_relative_percentiles_are_tie_aware(self):
        ranks=spot_percentiles({"AAAUSDT":5.0,"BBBUSDT":5.0,"CCCUSDT":5.0})
        self.assertEqual(set(ranks.values()),{50.0})
        single=spot_percentiles({"AAAUSDT":1.0})
        self.assertEqual(single["AAAUSDT"],50.0)

    def test_spot_resolves_multiplier_prefixed_futures_counterpart(self):
        fut={"BTCUSDT","1000PEPEUSDT","1000000MOGUSDT"}
        self.assertEqual(spot_futures_counterpart("BTCUSDT",fut),"BTCUSDT")
        self.assertEqual(spot_futures_counterpart("PEPEUSDT",fut),"1000PEPEUSDT")
        self.assertEqual(spot_futures_counterpart("MOGUSDT",fut),"1000000MOGUSDT")
        self.assertIsNone(spot_futures_counterpart("NOPEUSDT",fut))

    def test_global_sqlite_wrapper_closes_legacy_with_connect_contexts(self):
        from app.config import DATABASE_PATH
        harden_database()
        conn=None
        with sqlite3.connect(DATABASE_PATH) as c:
            conn=c
            c.execute("SELECT 1").fetchone()
        self.assertIsNotNone(conn)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_spot_outbox_waits_if_live_flow_loses_confirmation(self):
        s=self._spot_candidate(); self.assertIsNotNone(s); s.status="BUY"
        sid=save_spot_signal(s,delivered=False); enqueue_spot_delivery(sid,779,"BUY")
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*args,**kwargs): self.sent.append((args,kwargs))
        bot=Bot(); ask=(float(s.entry_low)+float(s.entry_high))/2
        book={"bids":[(ask-.001,1000)],"asks":[(ask,1000),(ask+.01,1000)]}
        now=int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        trades=[{"time_ms":now-i*1000,"notional":1000,"buyer_taker":False} for i in range(30)]
        with patch("bot_v11100.spot_local_book",return_value=book), \
             patch("bot_v11100.spot_book_stability",return_value={
                 "healthy":True,"stability_score":85,"bid_replenishment_ratio":1.0
             }), \
             patch("bot_v11100.spot_agg_trades",return_value=trades), \
             patch("bot_v11100.spot_klines",return_value=self._spot_minute_flow_frame(.55)):
            delivered=asyncio.run(_deliver_spot_pending(bot,forced_chat_ids={779}))
        self.assertEqual(delivered,0); self.assertEqual(bot.sent,[])
        with db_session() as c:
            d=c.execute("SELECT delivered_at,expired_at,attempts,last_error FROM spot_deliveries WHERE spot_signal_id=?",(sid,)).fetchone()
        self.assertIsNone(d[0]); self.assertIsNone(d[1]); self.assertGreaterEqual(d[2],1)
        self.assertIn("taker-flow",d[3])

    def test_spot_outbox_expires_if_fresh_negative_news_invalidates_buy(self):
        s=self._spot_candidate(); self.assertIsNotNone(s); s.status="BUY"
        sid=save_spot_signal(s,delivered=False); enqueue_spot_delivery(sid,780,"BUY")
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*args,**kwargs): self.sent.append((args,kwargs))
        bot=Bot(); ask=(float(s.entry_low)+float(s.entry_high))/2
        book={"bids":[(ask-.001,2200)],"asks":[(ask,1000),(ask+.01,1000)]}
        now=int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        trades=[{"time_ms":now-i*1000,"notional":1000,"buyer_taker":True} for i in range(30)]
        bad_news={"sources":2,"items":[{"title":f"{s.base_asset} exploit causes stolen funds","source":"A","age_minutes":1}],"breaking_events":[]}
        with patch("bot_v11100.spot_local_book",return_value=book), \
             patch("bot_v11100.spot_book_stability",return_value={
                 "healthy":True,"stability_score":85,"bid_replenishment_ratio":1.0
             }), \
             patch("bot_v11100.spot_agg_trades",return_value=trades), \
             patch("bot_v11100.spot_klines",return_value=self._spot_minute_flow_frame(.55)), \
             patch("bot_v11100.core.get_news_sentiment",return_value=bad_news):
            delivered=asyncio.run(_deliver_spot_pending(bot,forced_chat_ids={780}))
        self.assertEqual(delivered,0); self.assertEqual(bot.sent,[])
        with db_session() as c:
            d=c.execute("SELECT delivered_at,expired_at,last_error FROM spot_deliveries WHERE spot_signal_id=?",(sid,)).fetchone()
        self.assertIsNone(d[0]); self.assertIsNotNone(d[1]); self.assertIn("news/event",d[2])

    def _spot_tracker_row(self):
        return {
            "id":991,"symbol":"TESTUSDT","created_at":"2026-01-01T10:00:00+00:00",
            "delivered_at":"2026-01-01T10:00:00+00:00","entry_price":100.0,
            "invalidation":95.0,"tp1":105.0,"tp2":110.0,"tp3":115.0,
            "max_favorable_pct":0,"max_adverse_pct":0,"partial_hour_processed":0,
            "tp1_hit":0,"tp2_hit":0,"tp3_hit":0,"invalidated":0,
            "return_3d":None,"return_5d":None,"return_7d":None,"return_10d":None,
            "state":"OPEN","result":None,
        }

    @patch("spot_tracker.update_metrics")
    @patch("spot_tracker.klines")
    def test_spot_tracker_same_bar_invalidation_and_tp_is_ambiguous(self,mock_klines,mock_update):
        mock_klines.return_value=pd.DataFrame([{
            "open_time":pd.Timestamp("2026-01-01T10:00:00Z"),
            "close_time":pd.Timestamp("2026-01-01T10:59:59Z"),
            "open":100,"high":106,"low":94,"close":101,
        }])
        self.assertTrue(asyncio.run(update_spot_one(self._spot_tracker_row())))
        metrics=mock_update.call_args.kwargs
        self.assertEqual(metrics["result"],"AMBIGUOUS_INVALIDATION_TP")
        self.assertEqual(metrics["state"],"CLOSED")
        self.assertEqual(metrics["invalidated"],0)

    @patch("spot_tracker.update_metrics")
    @patch("spot_tracker.klines")
    def test_spot_tracker_tp3_first_wins_over_later_invalidation(self,mock_klines,mock_update):
        mock_klines.return_value=pd.DataFrame([
            {"open_time":pd.Timestamp("2026-01-01T10:00:00Z"),"close_time":pd.Timestamp("2026-01-01T10:59:59Z"),"open":100,"high":116,"low":99,"close":114},
            {"open_time":pd.Timestamp("2026-01-01T11:00:00Z"),"close_time":pd.Timestamp("2026-01-01T11:59:59Z"),"open":114,"high":200,"low":90,"close":120},
        ])
        self.assertTrue(asyncio.run(update_spot_one(self._spot_tracker_row())))
        metrics=mock_update.call_args.kwargs
        self.assertEqual(metrics["result"],"TP3")
        self.assertEqual(metrics["invalidated"],0)
        self.assertAlmostEqual(metrics["max_favorable_pct"],16.0,places=6)
        self.assertAlmostEqual(metrics["max_adverse_pct"],-1.0,places=6)

    @patch("spot_tracker.update_metrics")
    @patch("spot_tracker.klines")
    def test_spot_tracker_invalidation_first_wins_over_later_tp(self,mock_klines,mock_update):
        mock_klines.return_value=pd.DataFrame([
            {"open_time":pd.Timestamp("2026-01-01T10:00:00Z"),"close_time":pd.Timestamp("2026-01-01T10:59:59Z"),"open":100,"high":104,"low":94,"close":96},
            {"open_time":pd.Timestamp("2026-01-01T11:00:00Z"),"close_time":pd.Timestamp("2026-01-01T11:59:59Z"),"open":96,"high":120,"low":93,"close":118},
        ])
        self.assertTrue(asyncio.run(update_spot_one(self._spot_tracker_row())))
        metrics=mock_update.call_args.kwargs
        self.assertEqual(metrics["result"],"INVALIDATED")
        self.assertEqual(metrics["invalidated"],1)
        self.assertAlmostEqual(metrics["max_favorable_pct"],4.0,places=6)
        self.assertAlmostEqual(metrics["max_adverse_pct"],-6.0,places=6)

    @patch("spot_tracker.update_metrics")
    @patch("spot_tracker.klines")
    def test_spot_tracker_known_tp1_then_later_stop_is_not_ambiguous(self,mock_klines,mock_update):
        mock_klines.return_value=pd.DataFrame([
            {
                "open_time":pd.Timestamp("2026-01-01T10:00:00Z"),
                "close_time":pd.Timestamp("2026-01-01T10:59:59Z"),
                "open":100,"high":106,"low":99,"close":105,
            },
            {
                "open_time":pd.Timestamp("2026-01-01T11:00:00Z"),
                "close_time":pd.Timestamp("2026-01-01T11:59:59Z"),
                "open":105,"high":106,"low":94,"close":96,
            },
        ])
        self.assertTrue(asyncio.run(update_spot_one(self._spot_tracker_row())))
        metrics=mock_update.call_args.kwargs
        self.assertEqual(metrics["result"],"INVALIDATED")
        self.assertEqual(metrics["tp1_hit"],1)
        self.assertIsNotNone(metrics["first_tp1_at"])
        self.assertIsNotNone(metrics["first_invalidation_at"])
        self.assertLess(
            pd.Timestamp(metrics["first_tp1_at"]),
            pd.Timestamp(metrics["first_invalidation_at"])
        )

    def test_spot_leveraged_suffix_filter_does_not_drop_jup_or_soup(self):
        self.assertTrue(spot_is_leveraged_base("BTCUP"))
        self.assertTrue(spot_is_leveraged_base("ETHDOWN"))
        self.assertFalse(spot_is_leveraged_base("JUP"))
        self.assertFalse(spot_is_leveraged_base("SOUP"))


    def test_v1191_uses_isolated_futures_evidence_cohort(self):
        import app.db as runtime_db
        self.assertEqual(APP_VERSION,"11.10.0")
        self.assertEqual(FUTURES_RELEASE_VERSION,"11.7.1-futures-evidence")
        self.assertEqual(runtime_db.APP_VERSION,FUTURES_RELEASE_VERSION)

    def test_spot_portfolio_withholds_highly_correlated_duplicate_buy(self):
        a=self._spot_candidate(); b=self._spot_candidate()
        self.assertIsNotNone(a); self.assertIsNotNone(b)
        a.symbol="AAAUSDT"; a.base_asset="AAA"; a.status="BUY"; a.score=95
        b.symbol="BBBUSDT"; b.base_asset="BBB"; b.status="BUY"; b.score=90
        fa=_spot_frame(240,"1D",100,.35)
        fb=fa.copy()
        for col in ("open","high","low","close"):
            fb[col]=fb[col]*1.7
        rows={"AAAUSDT":{"daily":fa},"BBBUSDT":{"daily":fb}}
        out=spot_portfolio_diversify([a,b],rows,.90)
        self.assertEqual(out[0].symbol,"AAAUSDT")
        self.assertEqual(out[0].status,"BUY")
        self.assertEqual(out[1].status,"WATCH")
        portfolio=out[1].feature_snapshot["portfolio"]
        self.assertTrue(portfolio["buy_withheld"])
        self.assertEqual(portfolio["correlated_with"],"AAAUSDT")
        self.assertGreaterEqual(portfolio["corr30"],.90)

    def test_spot_watchlist_persists_watch_candidate(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="WATCH"; s.symbol="WATCHUSDT"; s.base_asset="WATCH"
        watch_id=upsert_spot_watch(s)
        self.assertIsInstance(watch_id,int)
        rows=active_spot_watches(10)
        row=next(r for r in rows if r["symbol"]=="WATCHUSDT")
        self.assertEqual(row["status"],"ACTIVE")
        self.assertAlmostEqual(float(row["entry_low"]),float(s.entry_low),places=6)

    def test_spot_watch_recheck_refuses_stale_cross_sectional_context(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="WATCH"; s.symbol="STALEUSDT"; s.base_asset="STALE"
        upsert_spot_watch(s)
        with db_session() as c:
            c.execute("UPDATE spot_watchlist SET updated_at=datetime('now','-2 hours') WHERE symbol='STALEUSDT'")
        row=next(r for r in active_spot_watches(10) if r["symbol"]=="STALEUSDT")
        result,error=asyncio.run(spot_recheck_watch(row,max_context_age_minutes=45))
        self.assertIsNone(result)
        self.assertIn("stale",error)

    def test_spot_active_portfolio_count_tracks_open_delivered_buys(self):
        a=self._spot_candidate(); b=self._spot_candidate()
        self.assertIsNotNone(a); self.assertIsNotNone(b)
        for s,symbol in ((a,"AAAUSDT"),(b,"BBBUSDT")):
            s.symbol=symbol; s.base_asset=symbol.removesuffix("USDT"); s.status="BUY"
            s.feature_snapshot.setdefault("portfolio",{})["cluster_key"]=symbol
            save_spot_signal(s,delivered=True)
        self.assertEqual(spot_active_open_count(),2)
        self.assertEqual(spot_active_portfolio_clusters(),{"AAAUSDT","BBBUSDT"})

    def test_watchtower_runtime_contract_revalidates_before_promotion(self):
        src=Path(__file__).with_name("bot_v11100.py").read_text(encoding="utf-8")
        start=src.index("async def spot_watch_job")
        end=src.index("async def _send_spot_results",start)
        block=src[start:end]
        self.assertIn("spot_local_book",block)
        self.assertIn("spot_recheck_watch",block)
        self.assertIn("upsert_spot_watch(signal)",block)
        self.assertIn("save_spot_signal(signal,delivered=False)",block)
        self.assertIn("spot_reserved_count",block)
        self.assertIn("active_count>=2",block)



    def _insert_futures_calibration_row(self,pnl,setup="CAL_TEST",side="LONG",timeframe="1H",regime="LONG"):
        with db_session() as c:
            c.execute("""
                INSERT INTO signals(
                    created_at,symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,status,
                    closed_at,result,pnl_r,activated_at,setup_type,market_regime,
                    release_version,is_shadow,delivery_state,delivered_at
                ) VALUES(
                    CURRENT_TIMESTAMP,'CALUSDT',?,?,90,100,95,105,110,115,'CLOSED',
                    CURRENT_TIMESTAMP,?, ?,CURRENT_TIMESTAMP,?,?,
                    '11.7.1-futures-evidence',0,'DELIVERED',CURRENT_TIMESTAMP
                )
            """,(
                timeframe,side,"TP1" if float(pnl)>0 else "SL",float(pnl),setup,regime
            ))

    def test_calibrated_probability_refuses_small_forward_sample(self):
        for _ in range(29):
            self._insert_futures_calibration_row(1.0)
        s=SimpleNamespace(
            setup_type="CAL_TEST",timeframe="1H",side="LONG",
            market_context={"bias":"LONG"},production_regime="LONG",
        )
        est=calibrated_futures(s)
        self.assertFalse(est.available)
        self.assertEqual(est.n,29)
        self.assertIn("29/30",calibrated_text(est))

    def test_calibrated_probability_has_uncertainty_not_fake_99_9(self):
        for i in range(50):
            self._insert_futures_calibration_row(1.0 if i<30 else -1.0)
        s=SimpleNamespace(
            setup_type="CAL_TEST",timeframe="1H",side="LONG",
            market_context={"bias":"LONG"},production_regime="LONG",
        )
        est=calibrated_futures(s)
        self.assertTrue(est.available)
        # Point estimate is Wilson-shrunk rather than raw 30/50=60%.
        self.assertGreater(est.probability,.58)
        self.assertLess(est.probability,.60)
        self.assertLess(est.lower,est.probability)
        self.assertGreater(est.upper,est.probability)
        text=calibrated_text(est)
        self.assertIn("30/50",text)
        self.assertIn("95% CI",text)
        self.assertNotIn("99.9",text)

    def test_wilson_interval_penalizes_perfect_small_sample(self):
        lo,hi=wilson_interval(30,30)
        self.assertLess(lo,.90)
        self.assertEqual(hi,1.0)
        from v1170_calibration import _estimate_from_values
        est=_estimate_from_values([1.0]*30,"perfect-small")
        self.assertLess(est.probability,1.0)
        self.assertFalse(calibrated_text(est).startswith("100%"))

    def test_futures_independent_evidence_vetoes_order_flow_conflict(self):
        s=SimpleNamespace(
            side="LONG",estimated_cost_r=.10,market_context={"bias":"LONG"},
            feature_snapshot={
                "decision":{"score_gap":25,"side":"LONG"},
                "technical":{"adx":28,"plus_di":30,"minus_di":15,"rsi":58,
                             "macd_hist":1,"taker_imbalance10":.05},
                "derivatives":{"taker_ratio":.90,"oi_change_pct":1.0,"price_change_pct":.8,
                               "spread_bps":2.0},
                "alpha_v112":{"ofi_5m":-.08},
                "market":{"bias":"LONG","breadth_blocked":False},
                "news":{"score":0,"breaking":False,"event_risk":0},
                "execution_v113":{"l2_state":"BALANCED"},
                "meta_v113":{"ready":False,"score":.5,"threshold":.6},
            }
        )
        audit=futures_evidence(s)
        self.assertFalse(audit.eligible)
        self.assertTrue(any("order flow" in x for x in audit.hard_conflicts))

    def test_spot_independent_evidence_vetoes_risk_off(self):
        s=self._spot_candidate()
        self.assertIsNotNone(s)
        s.feature_snapshot["market"]["risk_off"]=True
        audit=spot_evidence(s)
        self.assertFalse(audit.eligible)
        self.assertTrue(any("market regime" in x for x in audit.hard_conflicts))

    def test_spot_buy_now_requires_two_separated_full_confirmations(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="READYUSDT"; s.base_asset="READY"
        self.assertIsInstance(upsert_spot_watch(s),int)
        first=record_spot_ready(s.symbol,s.score,ask=s.micro.get("ask"),min_gap_sec=0)
        second=record_spot_ready(s.symbol,s.score,ask=s.micro.get("ask"),min_gap_sec=0)
        self.assertEqual(first,1)
        self.assertEqual(second,2)
        row=get_spot_watch(s.symbol)
        self.assertEqual(int(row["confirm_streak"]),2)
        self.assertEqual(row["candidate_state"],"READY_PENDING")

    def test_spot_watch_resets_confirmation_when_setup_degrades(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="RESETUSDT"; s.base_asset="RESET"
        upsert_spot_watch(s)
        record_spot_ready(s.symbol,s.score,min_gap_sec=0)
        reset_spot_ready(s.symbol,"price left zone")
        row=get_spot_watch(s.symbol)
        self.assertEqual(int(row["confirm_streak"]),0)
        self.assertEqual(row["candidate_state"],"WATCH")

    def test_spot_card_never_calls_first_confirmation_an_entry(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.spot_entry_state="READY_PENDING"; s.spot_confirm_streak=1
        text=spot_card(s,True)
        self.assertIn("ПОКА НЕ ПОКУПАТЬ",text)
        self.assertNotIn("ВХОД РАЗРЕШЁН СЕЙЧАС",text)
        s.spot_entry_state="BUY_NOW"; s.spot_confirm_streak=2
        text=spot_card(s,True)
        self.assertIn("ВХОД РАЗРЕШЁН СЕЙЧАС",text)

    def test_spot_auto_requires_persistent_2_of_2_before_delivery(self):
        src=Path(__file__).with_name("bot_v11100.py").read_text(encoding="utf-8")
        start=src.index("async def spot_auto_job")
        end=src.index("async def spot_tracker_job",start)
        block=src[start:end]
        self.assertIn('if streak<2:',block)
        self.assertIn('_decorate_spot_entry(s,"BUY_NOW",streak)',block)
        self.assertIn('no persistent 2/2 BUY NOW',block)

    def test_active_spot_view_contains_fixed_exit_plan(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="ACTIVEUSDT"; s.base_asset="ACTIVE"
        save_spot_signal(s,delivered=True)
        text=spot_active_text()
        self.assertIn("ACTIVEUSDT",text)
        self.assertIn("invalidation",text.lower())
        self.assertIn("не переписываются",text)

    def test_spot_save_fail_safe_default_is_undelivered_and_current_release(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="STAMPUSDT"; s.base_asset="STAMP"
        sid=save_spot_signal(s)
        with db_session(row_factory=sqlite3.Row) as c:
            row=c.execute(
                "SELECT delivered_at,release_version FROM spot_signals WHERE id=?",(sid,)
            ).fetchone()
        self.assertIsNone(row["delivered_at"])
        self.assertEqual(row["release_version"],SPOT_RELEASE_VERSION)
        self.assertEqual(SPOT_RELEASE_VERSION,"11.8.1-market-intelligence")

    def test_spot_watch_geometry_change_resets_ready_streak(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="GEOMUSDT"; s.base_asset="GEOM"
        upsert_spot_watch(s)
        self.assertEqual(record_spot_ready(s.symbol,s.score,min_gap_sec=0),1)
        row=get_spot_watch(s.symbol)
        self.assertEqual(int(row["confirm_streak"]),1)

        s.entry_low=float(s.entry_low)*1.001
        s.entry_high=float(s.entry_high)*1.001
        s.invalidation=float(s.invalidation)*.999
        upsert_spot_watch(s)
        row=get_spot_watch(s.symbol)
        self.assertEqual(int(row["confirm_streak"]),0)
        self.assertIsNone(row["last_ready_at"])
        self.assertEqual(record_spot_ready(s.symbol,s.score,min_gap_sec=0),1)

    def test_spot_old_release_watch_is_cancelled_on_init(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="WATCH"; s.symbol="OLDWATCHUSDT"; s.base_asset="OLDWATCH"
        upsert_spot_watch(s)
        with db_session() as c:
            c.execute("""
                UPDATE spot_watchlist SET release_key='11.7.0-old',status='ACTIVE'
                WHERE symbol=?
            """,(s.symbol,))
        init_spot_watch()
        row=get_spot_watch(s.symbol)
        self.assertEqual(row["status"],"CANCELLED")
        self.assertIn("release",str(row["last_reason"]).lower())

    def test_futures_old_release_arm_is_cancelled_on_init(self):
        s=_arm_signal()
        arm_id=arm_entry_now(s,"unit")
        with entry_now_module._db() as c:
            c.execute("""
                UPDATE v1142_armed
                SET release_key='11.5.2-futures-entry-now',status='ACTIVE'
                WHERE id=?
            """,(arm_id,))
        entry_now_module.init()
        row=get_entry_arm(arm_id)
        self.assertEqual(row["status"],"CANCELLED")
        self.assertIn("release",str(row["last_reason"]).lower())

    def test_pending_futures_arm_leaves_trigger_loop_but_stays_on_live_ws(self):
        s=_arm_signal()
        arm_id=arm_entry_now(s,"unit")
        mark_entry_pending_delivery(arm_id,4321)
        row=get_entry_arm(arm_id)
        self.assertEqual(row["status"],"PENDING_DELIVERY")
        self.assertFalse(any(int(r["id"])==arm_id for r in current_entry_rows(20)))
        self.assertIn("TESTUSDT",armed_symbols(20))

    def test_futures_delivery_expiry_fails_signal_only_after_all_recipients_expire(self):
        s=_arm_signal()
        s.feature_snapshot={
            "delivery_meta":{"source":"entry_now_v1142"},
            "entry_now_v1142":{"arm_id":999},
        }
        sid=app_db.save_pending(s)
        app_db.enqueue_delivery(sid,501,"x")
        app_db.enqueue_delivery(sid,502,"x")
        init_v1171_delivery()
        rows=pending_v1171_futures(10)
        ids={int(r[2]):int(r[0]) for r in rows}
        expire_v1171_futures(ids[501],sid,"test one")
        with db_session() as c:
            state=c.execute(
                "SELECT delivery_state FROM signals WHERE id=?",(sid,)
            ).fetchone()[0]
        self.assertEqual(state,"PENDING")
        expire_v1171_futures(ids[502],sid,"test two")
        with db_session() as c:
            state=c.execute(
                "SELECT delivery_state,status FROM signals WHERE id=?",(sid,)
            ).fetchone()
        self.assertEqual(tuple(state),("FAILED","DELIVERY_FAILED"))

    def test_failed_futures_delivery_releases_pending_arm_after_restart(self):
        s=_arm_signal()
        arm_id=arm_entry_now(s)
        mark_entry_pending_delivery(arm_id,777001)
        with db_session() as c:
            c.execute("""
                INSERT INTO signals(
                    id,created_at,symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,
                    status,release_version,is_shadow,delivery_state
                ) VALUES(
                    777001,CURRENT_TIMESTAMP,'TESTUSDT','1H','LONG',90,101,99,103,105,107,
                    'DELIVERY_FAILED','11.7.1-futures-evidence',0,'FAILED'
                )
            """)
        self.assertEqual(str(get_entry_arm(arm_id)["status"]),"PENDING_DELIVERY")
        self.assertEqual(reconcile_failed_futures_arms(),1)
        self.assertEqual(str(get_entry_arm(arm_id)["status"]),"CANCELLED")

    def test_disabled_futures_subscriber_is_visible_for_expiry(self):
        s=_arm_signal()
        sid=app_db.save_pending(s)
        app_db.enqueue_delivery(sid,601,"x")
        with db_session() as c:
            c.execute("""
                INSERT INTO subscribers(chat_id,enabled) VALUES(601,0)
                ON CONFLICT(chat_id) DO UPDATE SET enabled=0
            """)
        rows=pending_v1171_futures(10)
        row=next(r for r in rows if int(r[2])==601)
        self.assertEqual(int(row[6]),0)

    def test_stale_futures_retry_is_rejected_before_market_reanalysis(self):
        ctx={
            "release_version":"11.7.1-futures-evidence",
            "features":{
                "delivery_meta":{"source":"entry_now_v1142"},
                "entry_now_v1142":{"arm_id":99},
            },
        }
        with patch("bot_v11100.futures_delivery_context",return_value=ctx), \
             patch("bot_v11100.futures_delivery_age",return_value=9999), \
             patch("bot_v11100.entry_row") as arm_lookup:
            ok,reason,_=asyncio.run(_validate_futures_delivery(123))
        self.assertFalse(ok)
        self.assertIn("stale",reason)
        arm_lookup.assert_not_called()

    def test_futures_retry_release_mismatch_is_rejected(self):
        ctx={
            "release_version":"11.5.2-futures-entry-now",
            "features":{
                "delivery_meta":{"source":"entry_now_v1142"},
                "entry_now_v1142":{"arm_id":99},
            },
        }
        with patch("bot_v11100.futures_delivery_context",return_value=ctx), \
             patch("bot_v11100.futures_delivery_age") as age:
            ok,reason,_=asyncio.run(_validate_futures_delivery(123))
        self.assertFalse(ok)
        self.assertIn("release changed",reason)
        age.assert_not_called()

    def test_explicit_manual_futures_recipient_can_deliver_with_auto_off(self):
        s=_arm_signal()
        s.feature_snapshot={
            "delivery_meta":{"source":"entry_now_v1142"},
            "entry_now_v1142":{"arm_id":999},
        }
        sid=app_db.save_pending(s)
        app_db.enqueue_delivery(sid,701,"ENTRY")
        with db_session() as c:
            c.execute("""
                INSERT INTO subscribers(chat_id,enabled) VALUES(701,0)
                ON CONFLICT(chat_id) DO UPDATE SET enabled=0
            """)
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*a,**k): self.sent.append((a,k))
        bot=Bot()
        def mark_sent_for_test(delivery_id):
            with db_session() as c:
                c.execute(
                    "UPDATE signal_deliveries SET delivered_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(delivery_id),)
                )
        with patch(
            "bot_v11100._validate_futures_delivery",
            return_value=(True,"ok",999)
        ), patch(
            "bot_v11100.core.mark_delivery_sent",
            side_effect=mark_sent_for_test
        ):
            delivered=asyncio.run(
                _deliver_forced_futures(bot,sid,701,"ENTRY")
            )
        self.assertEqual(delivered,1)
        self.assertEqual(len(bot.sent),1)
        with db_session() as c:
            row=c.execute("""
                SELECT delivered_at FROM signal_deliveries
                WHERE signal_id=? AND chat_id=701
            """,(sid,)).fetchone()
        self.assertIsNotNone(row[0])

    def test_spot_pending_buy_reserves_portfolio_slot(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="RESERVEUSDT"; s.base_asset="RESERVE"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,801,"BUY")
        self.assertEqual(spot_reserved_count(),1)
        rows=spot_reserved_signals(10)
        self.assertEqual(rows[0]["symbol"],"RESERVEUSDT")

    def test_active_position_correlation_guard_blocks_high_corr(self):
        a=_spot_frame(70,"1D",100,.35)
        b=a.copy()
        for col in ("open","high","low","close"):
            b[col]=b[col]*1.8
        with patch("spot_scanner.klines",side_effect=[a,b]):
            risk=asyncio.run(
                spot_active_correlation_risk("AAAUSDT",["BBBUSDT"],.90)
            )
        self.assertFalse(risk["degraded"])
        self.assertTrue(risk["blocked"])
        self.assertGreaterEqual(risk["corr"],.90)
        self.assertEqual(risk["with_symbol"],"BBBUSDT")

    def test_spot_probability_uses_tp1_before_stop_not_positive_close(self):
        base=pd.Timestamp("2026-01-01T00:00:00Z")
        losing_plan={
            "delivered_at":base.isoformat(),"return_7d":8.0,
            "first_tp1_at":None,"first_invalidation_at":None,
        }
        tp1_then_stop={
            "delivered_at":base.isoformat(),"return_7d":-3.0,
            "first_tp1_at":(base+pd.Timedelta(days=3)).isoformat(),
            "first_invalidation_at":(base+pd.Timedelta(days=4)).isoformat(),
        }
        ambiguous_at=(base+pd.Timedelta(days=2)).isoformat()
        ambiguous={
            "delivered_at":base.isoformat(),"return_7d":5.0,
            "first_tp1_at":ambiguous_at,"first_invalidation_at":ambiguous_at,
        }
        late_tp1={
            "delivered_at":base.isoformat(),"return_7d":2.0,
            "first_tp1_at":(base+pd.Timedelta(days=9)).isoformat(),
            "first_invalidation_at":None,
        }
        stop_first={
            "delivered_at":base.isoformat(),"return_7d":1.0,
            "first_tp1_at":(base+pd.Timedelta(days=5)).isoformat(),
            "first_invalidation_at":(base+pd.Timedelta(days=2)).isoformat(),
        }
        self.assertEqual(_spot_success_value(losing_plan),-1.0)
        self.assertEqual(_spot_success_value(tp1_then_stop),1.0)
        self.assertIsNone(_spot_success_value(ambiguous))
        self.assertEqual(_spot_success_value(late_tp1),-1.0)
        self.assertEqual(_spot_success_value(stop_first),-1.0)

    def test_spot_auto_disabled_recipient_is_expired_not_sent(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="CONSENTUSDT"; s.base_asset="CONSENT"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,901,"BUY")
        with db_session() as c:
            c.execute("""
                INSERT INTO subscribers(chat_id,enabled) VALUES(901,0)
                ON CONFLICT(chat_id) DO UPDATE SET enabled=0
            """)
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*a,**k): self.sent.append((a,k))
        bot=Bot()
        delivered=asyncio.run(_deliver_spot_pending(bot))
        self.assertEqual(delivered,0)
        self.assertEqual(bot.sent,[])
        with db_session() as c:
            row=c.execute("""
                SELECT delivered_at,expired_at,last_error FROM spot_deliveries
                WHERE spot_signal_id=?
            """,(sid,)).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNotNone(row[1])
        self.assertIn("AUTO alerts disabled",row[2])

    def test_spot_fresh_extreme_crowding_blocks_final_send(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="CROWDUSDT"; s.base_asset="CROWD"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,902,"BUY")
        ask=(float(s.entry_low)+float(s.entry_high))/2
        book={"bids":[(ask-.001,2500)],"asks":[(ask,1200),(ask+.01,1200)]}
        now=int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        trades=[
            {"time_ms":now-i*1000,"notional":1000,"buyer_taker":True}
            for i in range(30)
        ]
        crowd={
            "available":True,"degraded":False,"extreme":True,
            "funding":.0021,"oi_change_pct":8.0,"global_ls":2.8,
        }
        class Bot:
            def __init__(self): self.sent=[]
            async def send_message(self,*a,**k): self.sent.append((a,k))
        bot=Bot()
        with patch("bot_v11100.spot_local_book",return_value=book), \
             patch("bot_v11100.spot_book_stability",return_value={
                 "healthy":True,"stability_score":85,"bid_replenishment_ratio":1.0
             }), \
             patch("bot_v11100.spot_agg_trades",return_value=trades), \
             patch("bot_v11100.spot_klines",return_value=self._spot_minute_flow_frame(.56)), \
             patch("bot_v11100.spot_fresh_derivatives_risk",return_value=crowd):
            delivered=asyncio.run(
                _deliver_spot_pending(bot,forced_chat_ids={902})
            )
        self.assertEqual(delivered,0)
        self.assertEqual(bot.sent,[])
        with db_session() as c:
            row=c.execute("""
                SELECT expired_at,last_error FROM spot_deliveries
                WHERE spot_signal_id=?
            """,(sid,)).fetchone()
        self.assertIsNotNone(row[0])
        self.assertIn("EXTREME",row[1])

    def test_spot_ready_streak_is_capped_at_two(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="CAP2USDT"; s.base_asset="CAP2"
        upsert_spot_watch(s)
        self.assertEqual(record_spot_ready(s.symbol,s.score,min_gap_sec=0),1)
        self.assertEqual(record_spot_ready(s.symbol,s.score,min_gap_sec=0),2)
        self.assertEqual(record_spot_ready(s.symbol,s.score,min_gap_sec=0),2)
        self.assertEqual(int(get_spot_watch(s.symbol)["confirm_streak"]),2)

    def test_spot_telegram_send_exception_is_terminal_not_blind_retried(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.symbol="AMBIGSPOTUSDT"; s.base_asset="AMBIGSPOT"
        sid=save_spot_signal(s,delivered=False)
        enqueue_spot_delivery(sid,9901,"BUY")
        ask=(float(s.entry_low)+float(s.entry_high))/2
        book={"bids":[(ask-.001,2500)],"asks":[(ask,1200),(ask+.01,1200)]}
        now=int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        trades=[
            {"time_ms":now-i*1000,"notional":1000,"buyer_taker":True}
            for i in range(30)
        ]
        class Bot:
            async def send_message(self,*a,**k):
                raise TimeoutError("telegram outcome unknown")
        with patch("bot_v11100.spot_local_book",return_value=book), \
             patch("bot_v11100.spot_book_stability",return_value={
                 "healthy":True,"stability_score":85,"bid_replenishment_ratio":1.0
             }), \
             patch("bot_v11100.spot_agg_trades",return_value=trades), \
             patch("bot_v11100.spot_klines",return_value=self._spot_minute_flow_frame(.56)), \
             patch("bot_v11100.spot_fresh_derivatives_risk",return_value={
                 "available":False,"counterpart":False,"degraded":False,"extreme":False
             }), \
             patch("bot_v11100.core.get_news_sentiment",return_value={
                 "sources":3,"items":[],"breaking_events":[]
             }):
            delivered=asyncio.run(
                _deliver_spot_pending(Bot(),forced_chat_ids={9901})
            )
        self.assertEqual(delivered,0)
        with db_session() as c:
            row=c.execute("""
                SELECT delivered_at,expired_at,sending_at,last_error
                FROM spot_deliveries WHERE spot_signal_id=?
            """,(sid,)).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNotNone(row[1])
        self.assertIsNotNone(row[2])
        self.assertIn("outcome unknown",row[3])
        self.assertEqual(pending_spot_deliveries(10),[])

    def test_manual_futures_send_exception_is_terminal_not_blind_retried(self):
        s=_arm_signal()
        s.feature_snapshot={
            "delivery_meta":{"source":"entry_now_v1142"},
            "entry_now_v1142":{"arm_id":999},
        }
        sid=app_db.save_pending(s)
        app_db.enqueue_delivery(sid,9902,"ENTRY")
        class Bot:
            async def send_message(self,*a,**k):
                raise TimeoutError("telegram outcome unknown")
        with patch(
            "bot_v11100._validate_futures_delivery",
            return_value=(True,"ok",999)
        ):
            delivered=asyncio.run(
                _deliver_forced_futures(Bot(),sid,9902,"ENTRY")
            )
        self.assertEqual(delivered,0)
        with db_session() as c:
            row=c.execute("""
                SELECT delivered_at,expired_at,sending_at,last_error
                FROM signal_deliveries
                WHERE signal_id=? AND chat_id=9902
            """,(sid,)).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNotNone(row[1])
        self.assertIsNotNone(row[2])
        self.assertIn("outcome unknown",row[3])
        self.assertFalse(any(int(r[1])==sid for r in pending_v1171_futures(20)))


    def test_v118_local_orderbook_applies_bridge_and_detects_gap(self):
        b=LocalBook("TESTUSDT")
        b.load_snapshot({
            "lastUpdateId":100,"bids":[(99.0,10.0)],"asks":[(101.0,10.0)],
            "fetched_at":1000.0,
        })
        state=b.apply_event({
            "E":1001000,"U":101,"u":102,"b":[["99.5","8"]],"a":[["101","0"],["100.5","7"]]
        },now=1001.0)
        self.assertEqual(state,"APPLIED")
        self.assertEqual(b.last_update_id,102)
        self.assertTrue(b.synced)
        self.assertIn(99.5,b.bids)
        self.assertNotIn(101.0,b.asks)
        gap=b.apply_event({"E":1002000,"U":104,"u":104,"b":[],"a":[]},now=1002.0)
        self.assertEqual(gap,"GAP")
        self.assertFalse(b.synced)
        self.assertGreaterEqual(b.gaps,1)

    def test_v118_local_orderbook_stability_requires_fresh_synced_book(self):
        now=time.time()
        b=LocalBook("TESTUSDT")
        b.load_snapshot({
            "lastUpdateId":200,"bids":[(100.0,100.0),(99.9,100.0)],
            "asks":[(100.02,100.0),(100.1,100.0)],"fetched_at":now,
        })
        b.last_event_ts=now; b.last_exchange_event_ms=int(now*1000); b.last_exchange_lag_sec=0.0
        for i in range(12):
            b.history.append((now-5.5+i*.5,2.0,20_000.0,18_000.0,.05))
        spot_orderbook_module._books["TESTUSDT"]=b
        spot_orderbook_module._connected=True
        h=spot_local_stability("TESTUSDT",3.0)
        self.assertTrue(h["healthy"])
        self.assertGreaterEqual(h["stability_score"],65)
        self.assertGreaterEqual(h["bid_replenishment_ratio"],.6)
        b.last_event_ts=now-10
        self.assertFalse(spot_local_stability("TESTUSDT",3.0)["healthy"])

    def test_v118_local_orderbook_warmup_cannot_authorize_buy(self):
        now=time.time(); b=LocalBook("WARMUSDT")
        b.load_snapshot({
            "lastUpdateId":300,"bids":[(100.0,100.0)],
            "asks":[(100.02,100.0)],"fetched_at":now,
        })
        b.last_event_ts=now; b.last_exchange_event_ms=int(now*1000); b.last_exchange_lag_sec=0.0
        b.history.append((now,2.0,10_000.0,9_000.0,.05))
        spot_orderbook_module._books["WARMUSDT"]=b
        spot_orderbook_module._connected=True
        h=spot_local_stability("WARMUSDT",3.0)
        self.assertTrue(h["healthy"])
        self.assertLess(h["stability_score"],65)
        self.assertIn("warming",h["reason"])

    def test_v118_orderbook_reset_discards_pre_reconnect_history(self):
        b=LocalBook("RESETUSDT")
        b.load_snapshot({
            "lastUpdateId":10,"bids":[(99.0,1.0)],"asks":[(101.0,1.0)],
            "fetched_at":100.0,
        })
        b.last_event_ts=101.0; b.history.append((101.0,2.0,10,10,0))
        b.reset("reconnect")
        self.assertFalse(b.synced)
        self.assertEqual(b.last_event_ts,0.0)
        self.assertEqual(len(b.history),0)

    def test_v118_recent_orderbook_gap_blocks_stability(self):
        now=time.time(); b=LocalBook("GAPUSDT")
        b.load_snapshot({
            "lastUpdateId":400,"bids":[(100.0,100.0)],
            "asks":[(100.02,100.0)],"fetched_at":now,
        })
        b.last_event_ts=now; b.last_exchange_event_ms=int(now*1000); b.last_exchange_lag_sec=0.0; b.last_gap_ts=now-5
        for i in range(12):
            b.history.append((now-5.5+i*.5,2.0,20_000.0,18_000.0,.05))
        spot_orderbook_module._books["GAPUSDT"]=b
        spot_orderbook_module._connected=True
        h=spot_local_stability("GAPUSDT",3.0)
        self.assertTrue(h["healthy"])
        self.assertLess(h["stability_score"],65)
        self.assertIn("gap",h["reason"])

    def test_v118_exchange_timestamp_lag_blocks_spot_book_even_if_receipt_is_fresh(self):
        now=time.time(); b=LocalBook("LAGUSDT")
        b.load_snapshot({
            "lastUpdateId":500,"bids":[(100.0,100.0)],
            "asks":[(100.02,100.0)],"fetched_at":now,
        })
        b.last_event_ts=now
        b.last_exchange_event_ms=int((now-8)*1000)
        b.last_exchange_lag_sec=8.0
        for i in range(12):
            b.history.append((now-5.5+i*.5,2.0,20_000.0,18_000.0,.05))
        spot_orderbook_module._books["LAGUSDT"]=b
        spot_orderbook_module._connected=True
        h=spot_local_stability("LAGUSDT",3.0)
        self.assertFalse(h["healthy"])
        self.assertIn("lag",h["reason"])

    def test_v118_futures_live_flow_rejects_exchange_lagged_aggtrade(self):
        live_module._trade_flow.clear()
        old_ms=int((time.time()-8)*1000)
        live_handle({"data":{
            "e":"aggTrade","E":old_ms,"T":old_ms,"s":"LAGFUTUSDT",
            "p":"100","q":"10","m":False
        }})
        self.assertIsNone(live_flow("LAGFUTUSDT",60,20))

    def test_v118_broad_spot_scan_can_only_arm_ready_one_of_two(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"
        _arm_spot_candidate(s)
        _arm_spot_candidate(s)
        row=get_spot_watch(s.symbol)
        self.assertIsNotNone(row)
        self.assertEqual(int(row["confirm_streak"] or 0),1)
        self.assertEqual(str(getattr(s,"spot_entry_state","")),"READY_PENDING")

    def test_v118_spot_challenger_requires_local_orderbook_evidence(self):
        s=self._spot_candidate(); self.assertIsNotNone(s)
        s.status="BUY"; s.score=95
        s.feature_snapshot.setdefault("evidence_v117",{}).update({
            "support":8,"conflict":0,"hard_conflicts":[]
        })
        s.feature_snapshot["required_score"]=82
        s.feature_snapshot["headroom_r"]=1.2
        s.feature_snapshot.setdefault("market",{})["regime"]="BULL"
        s.feature_snapshot.setdefault("micro",{}).update({
            "buy_share":.60,"closed_buy_share_15m":.56
        })
        accepted,_,_=v1180_challenger_decision("SPOT",s)
        self.assertFalse(accepted)
        s.feature_snapshot["local_orderbook_v118"]={
            "healthy":True,"stability_score":85,"bid_replenishment_ratio":1.0
        }
        accepted,_,_=v1180_challenger_decision("SPOT",s)
        self.assertTrue(accepted)

    def test_v1181_challenger_requires_accepted_and_rejected_forward_samples(self):
        self.assertGreaterEqual(MIN_CHALLENGER_RESOLVED,50)
        self.assertGreaterEqual(MIN_REJECTED_RESOLVED,15)
        with db_session() as c:
            for i in range(50):
                c.execute("""
                    INSERT INTO v1180_compare(
                        market,source_id,symbol,challenger_accept,champion_score,
                        challenger_score,created_at,resolved_at,success,outcome_value
                    ) VALUES('FUTURES',?,'TESTUSDT',1,90,95,CURRENT_TIMESTAMP,
                             CURRENT_TIMESTAMP,1,1.0)
                """,(800000+i,))
        s=v1180_summary("FUTURES")
        self.assertEqual(s["challenger"]["n"],50)
        self.assertEqual(s["rejected"]["n"],0)
        self.assertFalse(s["promotion_candidate"])

    def test_v1181_edge_lab_prunes_failed_undelivered_production_rows(self):
        with db_session() as c:
            c.execute("""
                INSERT INTO signals(
                    id,created_at,symbol,timeframe,side,score,entry,stop,tp1,tp2,tp3,
                    status,release_version,is_shadow,delivery_state
                ) VALUES(880001,CURRENT_TIMESTAMP,'FAILUSDT','1H','LONG',90,100,95,105,110,115,
                         'DELIVERY_FAILED','11.7.1-futures-evidence',0,'FAILED')
            """)
            c.execute("""
                INSERT INTO v1180_compare(
                    market,source_id,symbol,challenger_accept,champion_score,
                    challenger_score,created_at
                ) VALUES('FUTURES',880001,'FAILUSDT',1,90,95,CURRENT_TIMESTAMP)
            """)
        v1180_sync_outcomes()
        with db_session() as c:
            n=c.execute(
                "SELECT COUNT(*) FROM v1180_compare WHERE market='FUTURES' AND source_id=880001"
            ).fetchone()[0]
        self.assertEqual(n,0)

    def test_v1181_challenger_never_promotes_below_50_forward_results(self):
        init_v1180_lab()
        with db_session() as c:
            for i in range(49):
                c.execute("""
                    INSERT INTO v1180_compare(
                        market,source_id,symbol,challenger_accept,champion_score,
                        challenger_score,created_at,resolved_at,success,outcome_value
                    ) VALUES('FUTURES',?,?,1,90,95,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,1.0)
                """,(900000+i,f"T{i}USDT"))
        s=v1180_summary("FUTURES")
        self.assertEqual(s["challenger"]["n"],49)
        self.assertFalse(s["promotion_candidate"])

    def test_v118_active_manager_never_widens_original_futures_plan(self):
        row={
            "id":1,"symbol":"TESTUSDT","side":"LONG",
            "entry":100.0,"stop":95.0,"tp1":105.0,
        }
        with patch("v1180_manager.futures_flow",return_value={"buy_share":.60}):
            state,reason,r=v1180_futures_state(row,106.0)
        self.assertEqual(state,"PROTECT")
        self.assertIn("не расширять",reason)
        self.assertGreaterEqual(r,1.0)
        with patch("v1180_manager.futures_flow",return_value={"buy_share":.40}):
            state,reason,r=v1180_futures_state(row,94.0)
        self.assertEqual(state,"EXIT")
        self.assertIn("STOP",reason)

    def test_v118_futures_tp1_protection_is_latched_by_mfe(self):
        row={
            "id":1,"symbol":"TESTUSDT","side":"LONG",
            "entry":100.0,"stop":95.0,"tp1":105.0,"max_favorable_r":1.2,
        }
        with patch("v1180_manager.futures_flow",return_value={"buy_share":.55}):
            state,reason,r=v1180_futures_state(row,101.0)
        self.assertEqual(state,"PROTECT")
        self.assertIn("не расширять",reason)

    def test_v118_spot_tp1_protection_is_latched_after_pullback(self):
        row={
            "id":1,"symbol":"TESTUSDT","entry_price":100.0,
            "invalidation":95.0,"tp1":105.0,"tp1_hit":1,
        }
        with patch("v1180_manager.spot_local_book",return_value=None), \
             patch("v1180_manager.spot_book_health",return_value={"healthy":False,"reason":"test"}), \
             patch("v1180_manager.spot_book_ticker",return_value={"bid":100.9,"ask":101.1}):
            state,reason,px,r=asyncio.run(v1180_spot_state(row))
        self.assertEqual(state,"PROTECT")
        self.assertIn("TP1",reason)

    def test_v118_manager_reconciles_closed_source_rows(self):
        with v1180_manager_module._db() as c:
            c.execute(
                """INSERT INTO v1180_manager(
                    market,source_id,state,last_reason,highest_state,updated_at
                ) VALUES('FUTURES',987654,'HOLD','old','HOLD',CURRENT_TIMESTAMP)"""
            )
        self.assertGreaterEqual(v1180_reconcile_closed(),1)
        with v1180_manager_module._db() as c:
            row=c.execute(
                "SELECT state FROM v1180_manager WHERE market='FUTURES' AND source_id=987654"
            ).fetchone()
        self.assertEqual(row["state"],"CLOSED")

    def test_v118_orderbook_provider_prioritizes_reserved_positions(self):
        with patch("bot_v11100.spot_reserved_signals",return_value=[
                {"symbol":"ACTIVEUSDT"},{"symbol":"PENDINGUSDT"}
             ]), patch("bot_v11100.active_spot_watches",return_value=[
                {"symbol":"WATCHUSDT","score":99}
             ]):
            self.assertEqual(
                _spot_orderbook_symbols(3),
                ("ACTIVEUSDT","PENDINGUSDT","WATCHUSDT")
            )

    def test_v118_failure_lab_ignores_legacy_spot_release(self):
        base=pd.Timestamp("2026-01-01T00:00:00Z")
        with db_session() as c:
            c.execute("""
                INSERT INTO spot_signals(
                    created_at,symbol,base_asset,signal_status,state,score,setup_type,
                    entry_price,entry_low,entry_high,invalidation,tp1,tp2,tp3,
                    delivered_at,market_regime,relative_percentile,excess_btc_14d,
                    feature_json,release_version,return_7d,invalidated,
                    first_invalidation_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,(
                base.isoformat(),"LEGACYSPOTUSDT","LEGACYSPOT","BUY","CLOSED",90,"TEST",
                100,99,101,95,105,110,115,base.isoformat(),"BULL",90,5,"{}",
                "11.7.1-production-hardened",-5.0,1,
                (base+pd.Timedelta(days=2)).isoformat()
            ))
            sid=int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        v1180_sync_failures()
        with v1180_manager_module._db() as c:
            n=int(c.execute(
                "SELECT COUNT(*) FROM v1180_failures WHERE market='SPOT' AND source_id=?",
                (sid,)
            ).fetchone()[0])
        self.assertEqual(n,0)

    def test_v118_failure_intelligence_spot_bad_headroom(self):
        row={
            "feature_json":json.dumps({
                "headroom_r":.55,"news":{},"micro":{"buy_share":.58}
            }),
            "market_regime":"BULL",
        }
        category,detail=v1180_classify_spot(row,{})
        self.assertEqual(category,"BAD_HEADROOM")
        self.assertIn("0.55R",detail)


    def test_v119_positive_edge_needs_mature_sample(self):
        immature=[{"pnl_r":.8,"event_time":f"2026-06-{(i%10)+1:02d}T12:00:00+00:00"} for i in range(30)]
        mature=[{"pnl_r":.8,"event_time":f"2026-07-{(i%20)+1:02d}T12:00:00+00:00"} for i in range(80)]
        immature=v1190_edge_stats(immature,"test-immature")
        mature=v1190_edge_stats(mature,"test-mature")
        self.assertEqual(immature.adjustment,0.0)
        self.assertGreater(mature.adjustment,0.0)
        self.assertGreater(mature.lcb90_r,0.0)

    def test_v119_negative_edge_haircut_arrives_early(self):
        weak=v1190_edge_stats([-.6]*25,"test")
        self.assertLess(weak.adjustment,0.0)
        self.assertLess(weak.lcb90_r,0.0)

    def test_v119_selection_edge_only_resolves_near_tie(self):
        a=_arm_signal(); b=_arm_signal()
        a.symbol="AUSDT"; b.symbol="BUSDT"
        a.professional_rank=90.0; a.score=91; a.decision_priority=90.0
        a.expected_net_r_lcb=None; a.estimated_cost_r=.10
        b.professional_rank=89.0; b.score=90; b.decision_priority=91.0
        b.expected_net_r_lcb=.35; b.estimated_cost_r=.10
        self.assertEqual(sorted([a,b],key=v1190_selection_key,reverse=True)[0].symbol,"BUSDT")
        b.decision_priority=87.0
        self.assertEqual(sorted([a,b],key=v1190_selection_key,reverse=True)[0].symbol,"AUSDT")

    def test_v119_edge_annotation_is_batch_history_and_diagnostic(self):
        s=_arm_signal(); s.feature_snapshot={}
        history=[{
            "pnl_r":.7,"setup_type":s.setup_type,"timeframe":s.timeframe,
            "side":s.side,"market_regime":"LONG",
            "event_time":f"2026-07-{(i%20)+1:02d}T12:00:00+00:00"
        } for i in range(80)]
        with patch("v11100_edge._all_history",return_value=history) as fetch:
            out=v1190_annotate_edges([s])
        fetch.assert_called_once()
        self.assertIs(out[0],s)
        self.assertGreater(s.decision_priority,s.professional_rank)
        self.assertEqual(s.edge_sample_n,80)
        self.assertIn("decision_edge_v11100",s.feature_snapshot)

    def test_v119_blackbox_records_final_set_and_redacts_secrets(self):
        a=_arm_signal(); b=_arm_signal()
        a.symbol="AUSDT"; b.symbol="BUSDT"
        for x,p,lcb in ((a,91.0,.2),(b,89.0,.1)):
            x.decision_priority=p; x.expected_net_r_lcb=lcb; x.expected_net_r=.3
            x.edge_sample_n=80; x.feature_snapshot={"api_key":"SHOULD_NOT_APPEAR"}
        n=record_many_v1190_blackbox(
            [a,b],"FINAL_DECISION",selected_ids={id(a)},scan_id="test-scan",pipeline="main"
        )
        self.assertEqual(n,2)
        rows=recent_v1190_blackbox(10)
        finals=[r for r in rows if r["stage"]=="FINAL_DECISION"]
        self.assertEqual(len(finals),2)
        self.assertEqual(sum(int(r["selected"]) for r in finals),1)
        self.assertNotIn("SHOULD_NOT_APPEAR","".join(r["payload_json"] for r in finals))
        payload=json.loads(finals[0]["payload_json"])
        self.assertEqual(payload["decision_contract"]["schema"],"11.10.0-policy-v1")
        self.assertEqual(len(payload["decision_contract"]["fingerprint"]),64)
        with entry_now_module._db() as c:
            self.assertGreaterEqual(int(c.execute("SELECT COUNT(*) FROM v11100_policy_contract").fetchone()[0]),1)

    def test_v119_decision_replay_reproduces_original_top(self):
        a=_arm_signal(); b=_arm_signal()
        a.symbol="AUSDT"; b.symbol="BUSDT"
        a.professional_rank=90; a.score=90; a.decision_priority=91
        a.expected_net_r_lcb=.2; a.estimated_cost_r=.1
        b.professional_rank=89; b.score=95; b.decision_priority=89
        b.expected_net_r_lcb=.1; b.estimated_cost_r=.1
        record_many_v1190_blackbox(
            [a,b],"FINAL_DECISION",selected_ids={id(a)},scan_id="replay-scan",pipeline="main"
        )
        report=[r for r in v1190_replay(10) if r["scan_id"]=="replay-scan"]
        self.assertEqual(len(report),1)
        self.assertTrue(report[0]["same_top"])
        self.assertEqual(report[0]["replay_top"],"AUSDT")


if __name__=="__main__":
    unittest.main()
