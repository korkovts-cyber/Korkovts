"""V11.21.1 · Signal Engine Rebalance + state compatibility."""
from __future__ import annotations
from dataclasses import replace
import math
import v1142_entry_now as entry_base
import v1170_evidence as evidence_mod
import v11150_strong as strong_mod
import spot_db
import spot_watch

DANGEROUS_INDICATOR_TOKENS=(
    "sweep","opposite","adverse","spread","thin","liquidity",
    "cvd and oi","2+ independent context conflicts","conflict",
 )

FUTURES_COHORT="11.21.1-signal-engine"
SPOT_COHORT="11.21.1-spot-signal-engine"

def _f(v,default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)

def install(base):
    # Decision semantics changed materially. Never evaluate old ARMED/WATCH rows
    # under V11.21.1 rules, and keep forward statistics in a clean cohort.
    entry_base.FUTURES_RELEASE_KEY=FUTURES_COHORT
    spot_db.SPOT_RELEASE_VERSION=SPOT_COHORT
    spot_watch.SPOT_RELEASE_KEY=SPOT_COHORT
    base.FUTURES_RELEASE_VERSION=FUTURES_COHORT
    base.STRATEGY_VERSION=FUTURES_COHORT
    base.config.STRATEGY_VERSION=FUTURES_COHORT
    base.core.STRATEGY_VERSION=FUTURES_COHORT
    base.db.STRATEGY_VERSION=FUTURES_COHORT
    base.db.APP_VERSION=FUTURES_COHORT
    base.SPOT_RELEASE_VERSION=SPOT_COHORT

    original_evidence=base.futures_evidence_audit
    def futures_evidence(signal):
        audit=original_evidence(signal)
        if audit.eligible or audit.hard_conflicts:
            return audit
        snap=dict(getattr(signal,"feature_snapshot",{}) or {})
        market=dict(snap.get("market") or getattr(signal,"market_context",{}) or {})
        independent=bool(market.get("independent_mode"))
        min_support=3 if independent else 4
        eligible=(not audit.hard_conflicts and int(audit.support)>=min_support and int(audit.conflict)<=1)
        if not eligible:
            return audit
        out=evidence_mod.Audit(
            True,int(audit.support),int(audit.neutral),int(audit.conflict),
            tuple(audit.hard_conflicts),tuple(audit.families),
            f"{audit.summary} · V11.21 ARMED relief",
        )
        try:
            signal.feature_snapshot.setdefault("evidence_v117",{}).update({
                "eligible":True,"v11210_armed_relief":True,
                "required_support":min_support,"summary":out.summary,
            })
            signal.evidence_support=out.support
            signal.evidence_conflicts=out.conflict
            signal.evidence_summary=out.summary
        except Exception:
            pass
        return out
    base.futures_evidence_audit=futures_evidence

    original_indicator_gate=base._indicator_gate
    def indicator_gate(signal,prime=False):
        if original_indicator_gate(signal,prime):
            return True
        if prime:
            return False
        d=dict((getattr(signal,"feature_snapshot",{}) or {}).get("indicator_edge_v11151") or {})
        blockers=[str(x or "").lower() for x in (d.get("blockers") or [])]
        if any(any(token in b for token in DANGEROUS_INDICATOR_TOKENS) for b in blockers):
            return False
        available=bool(d.get("available",True))
        support=int(d.get("support",0) or 0)
        score=_f(d.get("score",0))
        conflicts=int(d.get("unique_conflicts",d.get("conflicts",0)) or 0)
        if conflicts>=2:
            return False
        allow=(not available) or support>=2 or score>=52.0
        if allow:
            d["v11210_armed_relief"]=True
            d["auto_eligible"]=True
            signal.feature_snapshot.setdefault("indicator_edge_v11151",{}).update(d)
        return bool(allow)
    base._indicator_gate=indicator_gate

    original_strong=base.assess_strong_signal
    def strong_assess(signal):
        a=original_strong(signal)
        if a.auto_eligible or a.blockers:
            return a
        snap=dict(getattr(signal,"feature_snapshot",{}) or {})
        evidence=dict(snap.get("evidence_v117") or {})
        rank=_f(getattr(signal,"professional_rank",0))
        support=int(evidence.get("support",getattr(signal,"evidence_support",0)) or 0)
        conflict=int(evidence.get("conflict",getattr(signal,"evidence_conflicts",0)) or 0)
        hard=list(evidence.get("hard_conflicts") or [])
        allow=(not hard and rank>=80.0 and support>=4 and conflict<=1 and indicator_gate(signal,False))
        if not allow:
            return a
        return strong_mod.StrongAssessment(
            "STRONG_ARMED",max(50.0,float(a.score)),True,False,
            tuple(a.reasons)+("V11.21 qualified for ARMED monitoring",),tuple(),
        )
    base.assess_strong_signal=strong_assess

    def strong_annotate(signal):
        a=strong_assess(signal)
        signal.strong_signal_label=a.label
        signal.strong_signal_score=float(a.score)
        signal.strong_auto_eligible=bool(a.auto_eligible)
        signal.strong_prime_eligible=bool(a.prime_eligible)
        signal.feature_snapshot.setdefault("strong_consensus_v11150",{}).update({
            "schema":"11.21.1-strong-consensus-compat",
            "label":a.label,"score":float(a.score),
            "auto_eligible":bool(a.auto_eligible),
            "prime_eligible":bool(a.prime_eligible),
            "reasons":list(a.reasons),"blockers":list(a.blockers),
            "v11211_state_coherent":True,
            "professional_rank_changed":False,
        })
        return signal

    def strong_annotate_many(rows):
        return [strong_annotate(x) for x in (rows or [])]

    # bot_v11180 looks these globals up at runtime; keep decision and UI fields
    # generated by the exact same V11.21.1 assessment.
    base.annotate_strong_signal=strong_annotate
    base.annotate_strong_signals=strong_annotate_many

    def spot_was_sent_recently_current(symbol,hours=72):
        # Deduplicate inside the current strategy cohort only. An older engine's
        # delivery must not suppress the first valid V11.21.1 BUY for 72 hours.
        spot_db.init()
        with spot_db._db() as c:
            row=c.execute("""
                SELECT 1 FROM spot_signals s
                WHERE s.symbol=? AND s.signal_status='BUY'
                  AND COALESCE(s.release_version,'')=?
                  AND julianday(COALESCE(s.delivered_at,s.created_at))>=julianday('now',?)
                  AND (s.delivered_at IS NOT NULL OR EXISTS(
                        SELECT 1 FROM spot_deliveries d
                        WHERE d.spot_signal_id=s.id AND d.delivered_at IS NULL
                          AND d.expired_at IS NULL))
                LIMIT 1
            """,(str(symbol).upper(),SPOT_COHORT,f"-{int(hours)} hours")).fetchone()
        return bool(row)

    spot_db.was_sent_recently=spot_was_sent_recently_current
    base.spot_was_sent_recently=spot_was_sent_recently_current

    original_evaluate=entry_base.evaluate
    def evaluate(row,frame1,frame3,px=None,bk=None,flow_row=None):
        a=original_evaluate(row,frame1,frame3,px=px,bk=bk,flow_row=flow_row)
        state=str(a.state).upper()
        reason=str(a.reason or "")
        side=str((row or {}).get("side") or "").upper()

        if state=="CANCEL" and "move escaped entry by >0.25R" in reason:
            dist=abs(_f(a.distance_r,999))
            if dist<=0.50:
                return replace(
                    a,state="WAIT",score=min(72.0,max(55.0,_f(a.score))),
                    reason="momentum extension <=0.50R; keep ARMED for retest",
                )
        if state!="WAIT":
            return a

        rlow=reason.lower()
        hard_wait_tokens=(
            "live price unavailable","best bid/ask unavailable",
            "price outside exact entry corridor","spread ",
            "confirmation unavailable",
        )
        if any(token in rlow for token in hard_wait_tokens):
            return a

        checks=int(bool(a.one_min_ok))+int(bool(a.three_min_ok))+int(bool(a.flow_ok))
        live_flow=str(a.flow_source or "").startswith("live_")
        flow_share=_f(a.flow_share,.5)
        non_opposing=(flow_share>=.48 if side=="LONG" else flow_share<=.52)
        allow=(live_flow and non_opposing and checks>=2 and _f(a.score)>=65.0 and _f(a.spread_bps,999)<=4.0)
        if allow:
            return replace(
                a,state="READY",score=max(80.0,_f(a.score)),
                reason=("V11.21 2-of-3 micro consensus; "+reason)[:500],
            )
        return a
    entry_base.evaluate=evaluate
    return True
