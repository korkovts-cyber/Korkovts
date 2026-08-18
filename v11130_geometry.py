"""V11.13 geometry contract for Futures ENTRY confirmation.

Pure helpers only.  A confirmation streak belongs to one concrete entry geometry;
if a later scan materially moves entry/stop/targets, two confirmations must start
again from zero rather than being stitched across two different trade plans.
"""
from __future__ import annotations
import math

GEOMETRY_ENTRY_R = 0.12
GEOMETRY_STOP_R = 0.10
GEOMETRY_TP1_R = 0.15


def _f(value, default=0.0):
    try:
        value=float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) else float(default)


def material_geometry_change(old, new) -> tuple[bool, str]:
    """Return whether a refreshed setup is materially different in risk units."""
    side=str((new or {}).get("side") or (old or {}).get("side") or "").upper()
    old_lo=_f((old or {}).get("entry_low")); old_hi=_f((old or {}).get("entry_high"))
    new_lo=_f((new or {}).get("entry_low")); new_hi=_f((new or {}).get("entry_high"))
    old_stop=_f((old or {}).get("stop")); new_stop=_f((new or {}).get("stop"))
    old_tp1=_f((old or {}).get("tp1")); new_tp1=_f((new or {}).get("tp1"))
    if min(old_lo,old_hi,new_lo,new_hi,old_stop,new_stop,old_tp1,new_tp1)<=0:
        return True,"invalid geometry refresh"
    old_mid=(old_lo+old_hi)/2.0; new_mid=(new_lo+new_hi)/2.0
    old_r=abs(old_mid-old_stop)
    if old_r<=0 or not math.isfinite(old_r):
        return True,"old risk unit invalid"
    entry_shift=abs(new_mid-old_mid)/old_r
    stop_shift=abs(new_stop-old_stop)/old_r
    tp1_shift=abs(new_tp1-old_tp1)/old_r
    crossed=(side=="LONG" and new_stop>=new_lo) or (side=="SHORT" and new_stop<=new_hi)
    changed=(entry_shift>GEOMETRY_ENTRY_R or stop_shift>GEOMETRY_STOP_R or
             tp1_shift>GEOMETRY_TP1_R or crossed)
    reason=(f"entry {entry_shift:.2f}R stop {stop_shift:.2f}R tp1 {tp1_shift:.2f}R"
            + (" invalid-side" if crossed else ""))
    return changed,reason
