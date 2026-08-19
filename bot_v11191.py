"""Korkovts Signal AI V11.19.3 · CODE-QUALITY AUDITED SIGNAL ENGINE."""
from __future__ import annotations

# 1) Futures scanner patch before app.bot/bot_v11180 bind scan functions.
import app.scanner as scanner
from v11191_futures_engine import (
    scan as futures_scan,
    scan_short as futures_scan_short,
    scan_status as futures_scan_status,
)
scanner.scan=futures_scan
scanner.scan_short=futures_scan_short
scanner.scan_status=futures_scan_status

import app.bot as core
core.scan=futures_scan
core.scan_short=futures_scan_short
core.scan_status=futures_scan_status

# 2) Resilient clock patch before bot_v11180 imports the function by value.
import v1141_integrity
from v11191_integrity import clock_status as resilient_clock_status
v1141_integrity.clock_status=resilient_clock_status

# 3) Spot full-universe patch before bot_v11180 imports spot_scan aliases.
import v11191_spot_engine
v11191_spot_engine.install()

# 4) UI overlays.
import v11_ui
from v11190_ui import install as install_v11190_ui
from v11191_ui import install as install_v11191_ui
install_v11190_ui(v11_ui)
install_v11191_ui(v11_ui)

# 5) Hardened V11.18 remains the final execution/risk/delivery authority.
import bot_v11180 as base

APP_VERSION="11.19.3"
base.APP_VERSION=APP_VERSION
base.config.APP_VERSION=APP_VERSION
base.core.APP_VERSION=APP_VERSION

# Defensive rebinding in case an imported compatibility module retained aliases.
base.integrity_clock_status=resilient_clock_status
base.spot_scan=v11191_spot_engine.scan
base.spot_scan_status=v11191_spot_engine.status

# 6) Final Spot delivery parity: telemetry outage is not directional evidence.
# Actual negative/global-breaking news and EXTREME crowding remain untouched.
_original_delivery_spot_news=base.spot_assess_news
_original_delivery_spot_crowding=base.spot_fresh_derivatives_risk

def _delivery_spot_news(snapshot,base_asset):
    row=dict(_original_delivery_spot_news(snapshot,base_asset) or {})
    if row.get("degraded") and not (
        row.get("block") or row.get("recent_negative") or row.get("global_breaking")
    ):
        row["auxiliary_degraded"]=True
        row["degraded"]=False
    return row

async def _delivery_spot_crowding(symbol):
    row=dict(await _original_delivery_spot_crowding(symbol) or {})
    if row.get("degraded") and not row.get("extreme"):
        row["auxiliary_degraded"]=True
        row["available"]=False
        row["degraded"]=False
    return row

base.spot_assess_news=_delivery_spot_news
base.spot_fresh_derivatives_risk=_delivery_spot_crowding

if __name__=="__main__":
    base.core.main()
