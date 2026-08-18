"""Deterministic non-secret decision-policy fingerprint for V11.10.

A replay is only meaningful if we know which thresholds produced the decision.
This module records a compact hash of decision-relevant config/environment values
without ever including tokens, credentials or account data.
"""
from __future__ import annotations

import hashlib
import json
import os

import app.config as config
from v11100_edge import (
    MIN_HISTORY,MIN_POSITIVE_HISTORY,MIN_NEGATIVE_DAYS,MIN_POSITIVE_DAYS,
    PRIOR_STRENGTH,NONSTATIONARITY_FLOOR_R,BOOTSTRAP_SIMS,
)
from v11100_protections import LOOKBACK_DAYS,PAIR_LOCK_HOURS,SETUP_QUARANTINE_HOURS

SCHEMA="11.10.0-policy-v1"


def _value(name,default=None):
    return getattr(config,name,default)


def payload():
    return {
        "schema":SCHEMA,
        "futures":{
            "min_signal_score":_value("MIN_SIGNAL_SCORE"),
            "min_24h_quote_volume":_value("MIN_24H_QUOTE_VOLUME"),
            "max_symbols_to_scan":_value("MAX_SYMBOLS_TO_SCAN"),
            "deep_analysis_limit":_value("DEEP_ANALYSIS_LIMIT"),
            "neutral_score_penalty":_value("NEUTRAL_REGIME_SCORE_PENALTY"),
            "neutral_max_signals":_value("NEUTRAL_REGIME_MAX_SIGNALS"),
            "round_trip_cost_pct":_value("ROUND_TRIP_COST_PCT"),
            "signal_cooldown_hours":_value("SIGNAL_COOLDOWN_HOURS"),
        },
        "robust_edge":{
            "min_history":MIN_HISTORY,
            "min_positive_history":MIN_POSITIVE_HISTORY,
            "min_negative_days":MIN_NEGATIVE_DAYS,
            "min_positive_days":MIN_POSITIVE_DAYS,
            "prior_strength":PRIOR_STRENGTH,
            "nonstationarity_floor_r":NONSTATIONARITY_FLOOR_R,
            "bootstrap_sims":BOOTSTRAP_SIMS,
        },
        "protections":{
            "lookback_days":LOOKBACK_DAYS,
            "pair_lock_hours":PAIR_LOCK_HOURS,
            "setup_quarantine_hours":SETUP_QUARANTINE_HOURS,
        },
        "spot_env":{
            # Explicitly whitelist only non-secret decision parameters.
            "min_24h_quote_volume":os.getenv("SPOT_MIN_24H_QUOTE_VOLUME","5000000"),
            "prefilter_limit":os.getenv("SPOT_PREFILTER_LIMIT","36"),
            "deep_limit":os.getenv("SPOT_DEEP_LIMIT","12"),
            "final_limit":os.getenv("SPOT_FINAL_LIMIT","4"),
        },
    }


def fingerprint_from_payload(data)->str:
    raw=json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def contract():
    data=payload()
    return {"schema":SCHEMA,"fingerprint":fingerprint_from_payload(data),"policy":data}
