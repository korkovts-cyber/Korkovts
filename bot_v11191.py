"""Korkovts Signal AI V11.19.9 · CODE-QUALITY AUDITED SIGNAL ENGINE."""
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
from v11195_geometry import install as install_v11195_geometry
install_v11195_geometry(scanner,core)

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

# 5) Reserve Binance health/ENTRY capacity before V11.18 installs the governor.
from v11196_api_resilience import install as install_v11196_api_resilience
install_v11196_api_resilience()

# 6) Hardened V11.18 remains the final execution/risk/delivery authority.
import bot_v11180 as base

APP_VERSION="11.19.9"
base.APP_VERSION=APP_VERSION
base.config.APP_VERSION=APP_VERSION
base.core.APP_VERSION=APP_VERSION

# Defensive rebinding in case an imported compatibility module retained aliases.
base.integrity_clock_status=resilient_clock_status
base.spot_scan=v11191_spot_engine.scan
base.spot_scan_status=v11191_spot_engine.status
import v112_health as _v11196_health
base.health_check=_v11196_health.check
base.health_text=_v11196_health.text

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

# 7) V11.19.9 scan-lock scheduler parity.
# A legitimate full-universe scan may take close to its bounded ~3-minute
# budget. The previous 90s wait was incompatible with that design.
async def _run_automatic_scan_v11194(context,scanner_fn,label):
    chats=[]
    heartbeat=str(label).upper()=="1H"
    try:
        chats=list(base.core.subscribers())
        if not chats:
            return

        from v11191_futures_engine import FULL_SCAN_BUDGET_SEC
        wait_limit=min(225,max(150,int(FULL_SCAN_BUDGET_SEC)+35))
        waited=0
        while base.core._scan_lock.locked() and waited<wait_limit:
            await __import__("asyncio").sleep(5)
            waited+=5

        if base.core._scan_lock.locked():
            diag=base.scanner.scan_status().get("main",{}) if heartbeat else {}
            if heartbeat:
                await base._send_auto_heartbeat(
                    context.bot,chats,diag,
                    scan_error=(
                        f"full-universe scan всё ещё держит scan-lock после "
                        f"{waited}с; это превышает допустимый bounded budget"
                    ),
                )
            return

        async with base.core._scan_lock:
            all_results=await scanner_fn()

        fresh=[
            row for row in all_results
            if not base.core.was_sent_recently(
                row.symbol,row.side,base.core.SIGNAL_COOLDOWN_HOURS,row.timeframe
            )
        ]
        armed=cancelled=triggered=0
        for result in fresh:
            result,assessment,streak=await base._arm_and_check(
                result,f"auto_{label}"
            )
            if assessment.state=="CANCEL":
                cancelled+=1
                continue
            armed+=1
            if assessment.state=="READY" and streak>=2:
                outcome=await base._trigger_arm(
                    context,result.entry_now_arm_id,assessment
                )
                if outcome and outcome[0]=="PRODUCTION":
                    triggered+=1

        base.core.log.info(
            "V11.19.9 automatic %s setups=%s armed=%s cancelled=%s "
            "entry_now=%s waited=%ss",
            label,len(fresh),armed,cancelled,triggered,waited,
        )
        if heartbeat:
            await base._send_auto_heartbeat(
                context.bot,chats,base.scanner.scan_status().get("main",{}),
                fresh_setups=len(fresh),triggered=triggered,
            )
    except Exception as exc:
        base.core.log.exception("V11.19.9 automatic %s scan failed",label)
        if heartbeat and chats:
            await base._send_auto_heartbeat(
                context.bot,chats,base.scanner.scan_status().get("main",{}),
                scan_error=f"{type(exc).__name__}: {exc}",
            )

base.core._run_automatic_scan=_run_automatic_scan_v11194


# 8) V11.19.9 truthful scan diagnostics.
# Legacy manual handlers collapsed every exception into "mandatory source unavailable".
# Keep safety fail-closed, but surface the actual stage/reason to Telegram.

def _v11199_scan_error_text(exc, diagnostics=None, label="Futures"):
    d=dict(diagnostics or {})
    reason=str(d.get("reason") or d.get("source_error") or str(exc) or type(exc).__name__)
    reason=reason.strip() or type(exc).__name__

    # Prefer structured stage diagnostics when available.
    if d.get("source_stage")=="ERROR":
        src=d.get("source_error") or reason
        return f"⚠️ {label} scan не завершён.\nПричина: {src}"

    if "deep shortlist deadline coverage incomplete" in reason:
        return f"⚠️ {label} scan не завершён.\nПричина: {reason}\nПолный deep-analysis не успел завершиться в допустимое окно."

    if "fast derivatives screen coverage incomplete" in reason:
        return f"⚠️ {label} scan не завершён.\nПричина: {reason}\nБыстрый derivatives-screen получил недостаточное покрытие."

    if "rate-limit cooldown" in reason.lower():
        return f"⚠️ {label} scan временно отложен.\nПричина: {reason}"

    if "timeout" in reason.lower():
        return f"⚠️ {label} scan не завершён.\nTimeout: {reason}"

    return f"⚠️ {label} scan не завершён.\nПричина: {type(exc).__name__}: {reason}"


# Wrap legacy manual PRIME FUTURES command with truthful exception output.
_original_prime_scan_cmd=getattr(base,"scan_cmd_v1142",None) or getattr(base,"scan_cmd",None)

async def _prime_scan_cmd_v11199(update,context):
    msg=update.effective_message
    try:
        await msg.reply_text(
            "🔎 Ищу сильные Futures-сетапы.\n"
            "Найденные кандидаты сначала перейдут в ARMED; вход будет отдельным 🚨 ENTRY NOW.",
            reply_markup=base.main_menu()
        )
        results=await base.core.scan()
        await base.core._send_results(
            context.bot,update.effective_chat.id,results,
            diagnostics=base.core.scan_status().get("main")
        )
    except Exception as exc:
        base.core.log.exception("V11.19.9 manual market scan failed")
        text=_v11199_scan_error_text(
            exc,base.core.scan_status().get("main"),"Futures"
        )
        await msg.reply_text(text,reply_markup=base.main_menu())

if hasattr(base,"scan_cmd_v1142"):
    base.scan_cmd_v1142=_prime_scan_cmd_v11199
if hasattr(base,"scan_cmd"):
    base.scan_cmd=_prime_scan_cmd_v11199


# Wrap short scan when that public handler exists.
if hasattr(base,"short_scan_cmd_v1142"):
    async def _short_scan_cmd_v11199(update,context):
        msg=update.effective_message
        try:
            results=await base.core.scan_short()
            await base.core._send_results(
                context.bot,update.effective_chat.id,results,
                diagnostics=base.core.scan_status().get("short")
            )
        except Exception as exc:
            base.core.log.exception("V11.19.9 manual short scan failed")
            text=_v11199_scan_error_text(
                exc,base.core.scan_status().get("short"),"FAST Futures"
            )
            await msg.reply_text(text,reply_markup=base.main_menu())
    base.short_scan_cmd_v1142=_short_scan_cmd_v11199


if __name__=="__main__":
    base.core.main()
