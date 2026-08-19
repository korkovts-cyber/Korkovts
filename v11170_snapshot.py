"""V11.17 coherent market snapshot contract for final ENTRY decisions.

V11.17.1 hardening keeps this layer negative-only and fail-closed.  The
fingerprint intentionally excludes local capture time so identical evidence can
be compared across replay/challenger runs.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib, json, math, time
from typing import Any

SCHEMA="11.17-market-snapshot-v2"
V11180_EVIDENCE_SCHEMA="11.18-market-snapshot-evidence-v1"
MAX_BOOK_AGE_SEC=3.0
MAX_EXCHANGE_LAG_SEC=2.5
MAX_DERIV_ACQUIRE_MS=12000.0
MAX_CLOCK_SKEW_SEC=2.0


def _safe(v,depth=0):
    if depth>8: return "<max-depth>"
    if v is None or isinstance(v,(str,bool,int)): return v
    if isinstance(v,float): return v if math.isfinite(v) else str(v)
    if isinstance(v,dict):
        out={}
        for k,x in v.items():
            key=str(k)
            if any(t in key.lower() for t in ("token","secret","api_key","password","authorization")): continue
            out[key]=_safe(x,depth+1)
        return out
    if isinstance(v,(list,tuple,set)): return [_safe(x,depth+1) for x in list(v)[:160]]
    try: return float(v)
    except Exception: return str(v)[:400]


def fingerprint(payload):
    raw=json.dumps(_safe(payload),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()

@dataclass(frozen=True)
class SnapshotDecision:
    eligible: bool
    score: float
    captured_at: float
    symbol: str
    timeframe: str
    book_age_sec: float | None
    exchange_lag_sec: float | None
    derivatives_acquire_ms: float | None
    coherence_ok: bool
    reasons: tuple[str,...]
    blockers: tuple[str,...]
    snapshot_fingerprint: str

    def as_dict(self): return asdict(self)


def assess(signal:Any, book:dict|None, now:float|None=None):
    now=float(now or time.time())
    fs=dict(getattr(signal,"feature_snapshot",{}) or {})
    reasons=[]; blockers=[]; score=100.0
    symbol=str(getattr(signal,"symbol","") or "").upper()
    timeframe=str(getattr(signal,"timeframe","") or "")

    coherence_raw=fs.get("data_coherence_v11100")
    coherence=dict(coherence_raw or {})
    if not coherence_raw:
        coh_ok=False
        blockers.append("market-data coherence metadata unavailable"); score-=60
    else:
        coh_ok=bool(coherence.get("eligible",False)) and bool(coherence.get("observable",False))
        if not bool(coherence.get("eligible",False)):
            blockers.append("market-data coherence contract failed"); score-=60
        elif not bool(coherence.get("observable",False)):
            blockers.append("market-data coherence timestamps unobservable"); score-=45
        else:
            reasons.append("closed-candle coherence OK")

    fresh_raw=fs.get("data_freshness_v1142")
    fresh=dict(fresh_raw or {})
    deriv=fresh.get("derivatives_acquire_ms")
    started=fresh.get("acquire_started_ms"); finished=fresh.get("acquire_finished_ms")
    try: deriv=float(deriv) if deriv is not None and math.isfinite(float(deriv)) else None
    except Exception: deriv=None
    try: started=float(started) if started is not None and math.isfinite(float(started)) else None
    except Exception: started=None
    try: finished=float(finished) if finished is not None and math.isfinite(float(finished)) else None
    except Exception: finished=None
    max_deriv=8000.0 if timeframe.upper()=="15M" else MAX_DERIV_ACQUIRE_MS
    wall_duration=(finished-started) if started is not None and finished is not None else None
    # Duration is the legacy production contract.  When wall-clock endpoints are
    # also present, verify them rather than requiring them and breaking older
    # valid snapshots/tests.  Missing/zero/non-finite duration remains fail-closed.
    endpoints_present=(started is not None or finished is not None)
    endpoints_consistent=(started is not None and started>0 and finished is not None and finished>=started and wall_duration is not None and wall_duration>=0 and abs(wall_duration-deriv)<=max(1500.0,deriv*0.50)) if (deriv is not None and endpoints_present) else True
    timing_consistent=(deriv is not None and deriv>0 and endpoints_consistent)
    if not fresh_raw or not timing_consistent:
        blockers.append("derivatives timing metadata unavailable/inconsistent"); score-=35
    elif deriv>max_deriv:
        blockers.append(f"derivatives acquisition stale {deriv/1000:.1f}s"); score-=35
    else:
        reasons.append(f"derivatives acquisition {deriv/1000:.1f}s")

    book_age=None; exchange_lag=None
    if not book:
        blockers.append("sequence-synchronised Futures L2 snapshot unavailable"); score-=50
    else:
        try: book_age=float(book.get("event_age_sec"))
        except Exception: book_age=None
        try: exchange_lag=float(book.get("exchange_lag_sec"))
        except Exception: exchange_lag=None
        try: fetched_at=float(book.get("fetched_at"))
        except Exception: fetched_at=None
        if fetched_at is None or not math.isfinite(fetched_at):
            blockers.append("Futures L2 capture timestamp unavailable"); score-=20
        else:
            skew=fetched_at-now
            if abs(skew)>MAX_CLOCK_SKEW_SEC:
                blockers.append(f"Futures L2/local clock skew {skew:+.2f}s"); score-=30
        if not bool(book.get("sequence_synced")) or not bool(book.get("healthy")):
            blockers.append("Futures L2 not healthy/sequence-synchronised"); score-=50
        if book_age is None or not math.isfinite(book_age) or book_age<0 or book_age>MAX_BOOK_AGE_SEC:
            blockers.append("Futures L2 stale"); score-=35
        else: reasons.append(f"L2 age {book_age:.2f}s")
        if exchange_lag is None or not math.isfinite(exchange_lag) or exchange_lag<0 or exchange_lag>MAX_EXCHANGE_LAG_SEC:
            blockers.append("exchange depth lag too high"); score-=30
        else: reasons.append(f"exchange depth lag {exchange_lag:.2f}s")

    # Do not include captured_at/fetched_at in the canonical fingerprint: it is
    # an evidence identity, not a wall-clock identity.
    payload={
        "schema":SCHEMA,"v11180_evidence_schema":V11180_EVIDENCE_SCHEMA,
        "symbol":symbol,"timeframe":timeframe,
        "coherence":coherence,"freshness":fresh,
        "book":{k:_safe((book or {}).get(k)) for k in (
            "lastUpdateId","event_age_sec","exchange_lag_sec","spread_bps",
            "stability_score","bid_replenishment_ratio","ask_replenishment_ratio",
            "median_imbalance_20bps","book_samples","book_coverage_sec",
            "bid_depth_change_2s","ask_depth_change_2s","spread_ratio_2s",
            "microprice_drift_bps_2s","adverse_long_share_5s","adverse_short_share_5s",
            "gaps","resyncs")},
        "strong":fs.get("strong_consensus_v11150") or fs.get("strong_signal_v11150"),
        "indicator_edge":fs.get("indicator_edge_v11151"),
        "adaptive_edge":fs.get("adaptive_edge_v11160"),
    }
    fp=fingerprint(payload)
    score=max(0.0,min(100.0,score))
    eligible=not blockers and score>=80.0
    d=SnapshotDecision(eligible,round(score,2),now,symbol,timeframe,book_age,exchange_lag,deriv,coh_ok,tuple(reasons),tuple(dict.fromkeys(blockers)),fp)
    if isinstance(getattr(signal,"feature_snapshot",None),dict):
        signal.feature_snapshot.setdefault("market_snapshot_v11170",{}).update(d.as_dict()|{
            "schema":SCHEMA,"v11180_evidence_schema":V11180_EVIDENCE_SCHEMA,"negative_only":True
        })
    return d
