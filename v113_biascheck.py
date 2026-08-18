"""Synthetic lookahead / recursive indicator self-test for V11.4.1.

This does not replace historical walk-forward testing. It is a deterministic
startup guard against two implementation errors:
- a future candle changing an already-computed historical indicator;
- extreme indicator instability caused solely by startup history length.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd

from app.indicators import enrich


NUMERIC=(
    "ema20","ema50","ema200","rsi","atr","adx","bb_width","efficiency20",
    "ichimoku_a","ichimoku_b","mfi","cmf20",
)


def _frame(n=720):
    i=np.arange(n,dtype=float)
    base=100+0.035*i+2.2*np.sin(i/17)+.6*np.sin(i/5.3)
    close=base+.12*np.sin(i/2.7)
    open_=close-.08*np.cos(i/3.1)
    high=np.maximum(open_,close)+.35+.05*np.sin(i/4)
    low=np.minimum(open_,close)-.35-.05*np.cos(i/4.5)
    volume=1000+150*np.sin(i/11)+40*np.cos(i/3.7)
    taker=volume*(.50+.08*np.sin(i/13))
    return pd.DataFrame({
        "open_time":pd.date_range("2026-01-01",periods=n,freq="min",tz="UTC"),
        "open":open_,"high":high,"low":low,"close":close,
        "volume":volume,"taker_buy_base":taker,
    })


def _close(a,b,tol=1e-9):
    scale=max(1.0,abs(float(a)),abs(float(b)))
    return abs(float(a)-float(b))<=tol*scale


def lookahead_check():
    df=_frame()
    cutoff=519
    prefix=enrich(df.iloc[:cutoff+1].copy())
    full=enrich(df.copy())
    if prefix.empty or cutoff not in full.index:
        return False,["indicator warmup produced no comparable row"]

    a=prefix.iloc[-1]
    b=full.loc[cutoff]
    issues=[]
    for name in NUMERIC:
        if not _close(a[name],b[name],1e-10):
            issues.append(f"{name} changed after future candles were appended")
    if int(a.supertrend_dir)!=int(b.supertrend_dir):
        issues.append("supertrend_dir changed after future candles were appended")

    # Cumulative indicators may have large levels, but the causal row itself
    # still must be exactly unchanged when future data is appended.
    for name in ("obv","obv_ema20","cvd","cvd_ema20"):
        if not _close(a[name],b[name],1e-10):
            issues.append(f"{name} changed after future candles were appended")
    return not issues,issues


def recursive_check():
    df=_frame()
    full=enrich(df.copy())
    tail=enrich(df.iloc[-500:].reset_index(drop=True))
    if full.empty or tail.empty:
        return False,["not enough enriched rows"]

    a=full.iloc[-1]
    b=tail.iloc[-1]
    issues=[]

    # Long-memory EMA is expected to move slightly with startup length, but a
    # large final-state drift would mean runtime/backtest inconsistency.
    limits={
        "ema20":.001,"ema50":.002,"ema200":.010,
        "rsi":.02,"atr":.02,"adx":.05,"bb_width":.05,
        "efficiency20":.02,"ichimoku_a":.01,"ichimoku_b":.01,
        "mfi":.05,"cmf20":.05,
    }
    for name,limit in limits.items():
        av=float(a[name]); bv=float(b[name])
        denom=max(1e-9,abs(av),abs(bv))
        drift=abs(av-bv)/denom
        if drift>limit:
            issues.append(f"{name} recursive drift {drift*100:.2f}% > {limit*100:.2f}%")

    if int(a.supertrend_dir)!=int(b.supertrend_dir):
        issues.append("supertrend direction depends on startup history")

    # Strategy-relevant cumulative-indicator relationships should keep direction.
    for value,ema in (("obv","obv_ema20"),("cvd","cvd_ema20")):
        sign_a=np.sign(float(a[value])-float(a[ema]))
        sign_b=np.sign(float(b[value])-float(b[ema]))
        if sign_a!=sign_b:
            issues.append(f"{value} confirmation flips with startup history")

    return not issues,issues


def run():
    look_ok,look_issues=lookahead_check()
    rec_ok,rec_issues=recursive_check()
    return {
        "lookahead_ok":look_ok,
        "recursive_ok":rec_ok,
        "issues":look_issues+rec_issues,
    }


if __name__=="__main__":
    result=run()
    if not result["lookahead_ok"] or not result["recursive_ok"]:
        raise SystemExit(
            "V11.4 INDICATOR SELF-TEST FAILED:\n- "+"\n- ".join(result["issues"])
        )
    print("V11.4 INDICATOR SELF-TEST: OK")
