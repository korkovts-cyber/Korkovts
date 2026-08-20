"""Korkovts Signal AI V11.21.5 · CODE-QUALITY AUDITED SIGNAL ENGINE."""
from __future__ import annotations
import asyncio
import re
from contextlib import asynccontextmanager

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

from v11200_data_architecture import install as install_v11200_data_architecture
install_v11200_data_architecture()

# 6) Hardened V11.18 remains the final execution/risk/delivery authority.
import bot_v11180 as base

from v11200_data_architecture import install_after_base as install_v11200_after_base
install_v11200_after_base()

from v11210_signal_engine import install as install_v11210_signal_engine
install_v11210_signal_engine(base)

APP_VERSION="11.21.5"
base.APP_VERSION=APP_VERSION
base.config.APP_VERSION=APP_VERSION
base.core.APP_VERSION=APP_VERSION

# V11.21.5 concurrency contract.
_v11205_research_gate=asyncio.Lock()

@asynccontextmanager
async def _v11205_full_scan_guard(timeout_sec):
    """Serialize heavy research with a real bounded timeout."""
    timeout=max(1.0,float(timeout_sec))
    loop=asyncio.get_running_loop()
    deadline=loop.time()+timeout
    gate_acquired=False
    scan_acquired=False
    try:
        await asyncio.wait_for(_v11205_research_gate.acquire(),timeout=timeout)
        gate_acquired=True
        remaining=max(.05,deadline-loop.time())
        await asyncio.wait_for(base.core._scan_lock.acquire(),timeout=remaining)
        scan_acquired=True
        yield
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"full-market research lock timeout after {timeout:.0f}s") from exc
    finally:
        if scan_acquired and base.core._scan_lock.locked():
            base.core._scan_lock.release()
        if gate_acquired and _v11205_research_gate.locked():
            _v11205_research_gate.release()

# Defensive rebinding in case an imported compatibility module retained aliases.
base.integrity_clock_status=resilient_clock_status
base.spot_scan=v11191_spot_engine.scan
base.spot_scan_status=v11191_spot_engine.status

# Serialize every heavy Spot full scan (manual or AUTO) with heavy Futures full
# scans. Install AFTER defensive rebinding so this wrapper cannot be overwritten.
_original_spot_scan_v11202=base.spot_scan
async def _serialized_spot_scan_v11202(*args,**kwargs):
    from v11191_futures_engine import FULL_SCAN_BUDGET_SEC
    timeout=min(225,max(150,int(FULL_SCAN_BUDGET_SEC)+35))
    async with _v11205_full_scan_guard(timeout):
        return await _original_spot_scan_v11202(*args,**kwargs)
base.spot_scan=_serialized_spot_scan_v11202

import v112_health as _v11196_health
base.health_check=_v11196_health.check
base.health_text=_v11196_health.text


# V11.21.5 HEALTH FIX
# The inherited callback detached HEALTH into a background UI task. If that task
# failed or timed out, Telegram showed no new card and the user only saw stale
# PAUSE. Intercept HEALTH directly: ACK immediately, bounded forced refresh,
# update the runtime snapshot, then always send either fresh health or an error.
_original_core_callback_v11203=base.core.callback

async def _callback_v11203(update,context):
    query=update.callback_query
    data=str(getattr(query,"data","") or "")
    if data!="v112:health":
        return await _original_core_callback_v11203(update,context)

    try:
        await query.answer("Health check запущен")
    except Exception:
        pass

    try:
        health=await __import__("asyncio").wait_for(
            base.health_check(force=True),timeout=15
        )
        base._last_health=health
        await query.message.reply_text(
            base.health_text(health),
            parse_mode=base.ParseMode.HTML,
            reply_markup=base.main_menu(),
        )
    except Exception as exc:
        base.core.log.exception("V11.21.5 HEALTH refresh failed")
        try:
            await query.message.reply_text(
                "⚠️ <b>HEALTH CHECK FAILED · V11.21.5</b>\\n"
                f"Причина: <code>{type(exc).__name__}: {str(exc)[:240]}</code>\\n"
                "Старый PAUSE не считается новым результатом этой проверки.",
                parse_mode=base.ParseMode.HTML,
                reply_markup=base.main_menu(),
            )
        except Exception:
            base.core.log.exception("V11.21.5 HEALTH error reply failed")
    return None

base.core.callback=_callback_v11203


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

def _v11207_blocked_diag(reason):
    return {"status":"blocked","reason":str(reason or ""),"liquid":0,"prefiltered":0,"deep_checked":0,"final":0,"production_pool":0,"production_rejects":0,"scan_started":False}

# 7) V11.21.5 scan-lock scheduler parity.
# A legitimate full-universe scan may take close to its bounded ~3-minute
# budget. The previous 90s wait was incompatible with that design.
async def _run_automatic_scan_v11194(context,scanner_fn,label):
    chats=[]
    heartbeat=str(label).upper()=="1H"
    try:
        chats=list(base.core.subscribers())
        if not chats:
            return

        # Do not launch another 180-symbol research cycle while production is
        # already hard-paused. In particular this breaks the self-sustaining
        # rate-limit loop: PAUSE -> heavy scan -> more weight -> PAUSE.
        health=await base.health_check(force=True)
        base._last_health=health
        if bool(getattr(health,"hard_pause",False)) or str(getattr(health,"status","")).upper()=="PAUSE":
            if heartbeat:
                reason=", ".join(getattr(health,"reasons",()) or ()) or "PRODUCTION HEALTH PAUSE"
                await base._send_auto_heartbeat(
                    context.bot,chats,_v11207_blocked_diag(reason),
                    scan_error=f"PRODUCTION HEALTH PAUSE: {reason}",
                )
            base.core.log.warning("V11.21.5 automatic %s skipped before scan: health PAUSE",label)
            return

        from v11191_futures_engine import FULL_SCAN_BUDGET_SEC
        wait_limit=min(225,max(150,int(FULL_SCAN_BUDGET_SEC)+35))
        async with _v11205_full_scan_guard(wait_limit):
            health=await base.health_check(force=True)
            base._last_health=health
            if bool(getattr(health,"hard_pause",False)) or str(getattr(health,"status","")).upper()=="PAUSE":
                if heartbeat:
                    reason=", ".join(getattr(health,"reasons",()) or ()) or "PRODUCTION HEALTH PAUSE"
                    await base._send_auto_heartbeat(
                        context.bot,chats,base.scanner.scan_status().get("main",{}),
                        scan_error=f"PRODUCTION HEALTH PAUSE: {reason}",
                    )
                return
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
            "V11.21.5 automatic %s setups=%s armed=%s cancelled=%s "
            "entry_now=%s",
            label,len(fresh),armed,cancelled,triggered,
        )
        if heartbeat:
            await base._send_auto_heartbeat(
                context.bot,chats,base.scanner.scan_status().get("main",{}),
                fresh_setups=len(fresh),triggered=triggered,
            )
    except Exception as exc:
        base.core.log.exception("V11.21.5 automatic %s scan failed",label)
        if heartbeat and chats:
            await base._send_auto_heartbeat(
                context.bot,chats,base.scanner.scan_status().get("main",{}),
                scan_error=f"{type(exc).__name__}: {exc}",
            )

base.core._run_automatic_scan=_run_automatic_scan_v11194


# V11.21.5 truthful AUTO heartbeat.
# A 0→0→0→0 funnel is not a market result when Health/lock prevented startup.
_original_heartbeat_text_v11206=base.heartbeat_text

def _heartbeat_text_v11206(diagnostics,**kwargs):
    text=_original_heartbeat_text_v11206(diagnostics,**kwargs)
    d=dict(diagnostics or {})
    scan_error=str(kwargs.get("scan_error") or "")
    counts=(
        int(d.get("liquid",0) or 0),
        int(d.get("prefiltered",0) or 0),
        int(d.get("deep_checked",0) or 0),
        int(d.get("final",0) or 0),
    )
    scan_started=bool(d.get("scan_started",True))
    if scan_error and (counts==(0,0,0,0) or not scan_started):
        text=re.sub(
            r"🔎 Рынок: <b>0</b> ликвидных → <b>0</b> кандидатов → <b>0</b> deep-check → <b>0</b> финальных",
            "🔎 Рынок: <b>СКАН НЕ ЗАПУЩЕН / НЕТ ПОЛНОГО ПРОХОДА</b>",
            text,
            count=1,
        )
    if not scan_error and scan_started:
        fast=int(d.get("deep_screen_candidates",d.get("prefiltered",0)) or 0)
        full=int(d.get("deep_checked",0) or 0)
        if fast or full:
            primary=int(d.get("primary_frames_ok",0) or 0)
            multi=int(d.get("multiframe_ok",0) or 0)
            text += (
                f"\n🧬 Analysis funnel: <b>{primary}</b> liquid-primary → "
                f"<b>{multi}</b> multi-TF → <b>{fast}</b> fast-screen → "
                f"<b>{full}</b> full-deep"
            )
        top=list(d.get("top_rejections") or [])
        if not top:
            rejects=dict(d.get("rejections") or {})
            top=[
                {"reason":str(k),"count":int(v)}
                for k,v in sorted(
                    ((k,v) for k,v in rejects.items() if str(k)!="PASS"),
                    key=lambda item:(-int(item[1]),str(item[0]))
                )[:3]
            ]
        if top:
            rendered=", ".join(
                f"{base.escape(str(row.get('reason','?')))} ×{int(row.get('count',0) or 0)}"
                for row in top[:3]
            )
            text += f"\n🧱 Главные блокеры: <code>{rendered}</code>"
        examples=list(d.get("deep_rejections") or [])
        if examples and int(d.get("final",0) or 0)==0:
            ex=examples[0]
            issues="; ".join(str(x) for x in (ex.get("issues") or [])[:2])
            if issues:
                text += (
                    f"\n🔬 Ближайший: <b>{base.escape(str(ex.get('symbol','?')))} "
                    f"{base.escape(str(ex.get('side','?')))}</b> — "
                    f"{base.escape(issues[:220])}"
                )
    return text

base.heartbeat_text=_heartbeat_text_v11206

# 8) V11.21.5 truthful scan diagnostics.
# Legacy manual handlers collapsed every exception into "mandatory source unavailable".
# Keep safety fail-closed, but surface the actual stage/reason to Telegram.

def _v11199_scan_error_text(exc, diagnostics=None, label="Futures"):
    d=dict(diagnostics or {})
    exc_reason=str(exc or "").strip()
    diag_reason=str(d.get("reason") or d.get("source_error") or "").strip()

    # Current safety/lock failures must not be hidden by stale diagnostics
    # left by the previous scan cycle.
    priority_tokens=(
        "PRODUCTION HEALTH PAUSE",
        "rate-limit cooldown",
        "full-market research lock timeout",
        "BINANCE CLOCK PAUSE",
    )
    if exc_reason and any(token.lower() in exc_reason.lower() for token in priority_tokens):
        reason=exc_reason
    elif d.get("source_stage")=="ERROR" and d.get("source_error"):
        reason=str(d.get("source_error"))
    else:
        reason=diag_reason or exc_reason or type(exc).__name__

    reason=reason.strip() or type(exc).__name__

    if d.get("source_stage")=="ERROR" and d.get("source_error") and not any(
        token.lower() in reason.lower() for token in priority_tokens
    ):
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
            "Кандидаты проходят ARMED-мониторинг; при подтверждении бот пришлёт 🚨 LONG NOW / SHORT NOW с входом, стопом и целями.",
            reply_markup=base.main_menu()
        )
        from v11191_futures_engine import FULL_SCAN_BUDGET_SEC
        wait_limit=min(225,max(150,int(FULL_SCAN_BUDGET_SEC)+35))
        async with _v11205_full_scan_guard(wait_limit):
            health=await base.health_check(force=True)
            base._last_health=health
            if bool(getattr(health,"hard_pause",False)) or str(getattr(health,"status","")).upper()=="PAUSE":
                reason=", ".join(getattr(health,"reasons",()) or ()) or "PRODUCTION HEALTH PAUSE"
                raise RuntimeError(f"PRODUCTION HEALTH PAUSE: {reason}")
            results=await base.core.scan()
        await base.core._send_results(
            context.bot,update.effective_chat.id,results,
            diagnostics=base.core.scan_status().get("main")
        )
    except Exception as exc:
        base.core.log.exception("V11.21.5 manual market scan failed")
        text=_v11199_scan_error_text(
            exc,base.core.scan_status().get("main"),"Futures"
        )
        await msg.reply_text(text,reply_markup=base.main_menu())

if hasattr(base,"scan_cmd_v1142"):
    base.scan_cmd_v1142=_prime_scan_cmd_v11199
if hasattr(base,"scan_cmd"):
    base.scan_cmd=_prime_scan_cmd_v11199
# app.bot.callback resolves the module-global core.scan_cmd.
base.core.scan_cmd=_prime_scan_cmd_v11199



# V11.21.5 truthful manual Spot diagnostics.
if hasattr(base,"spot_cmd"):
    async def _spot_cmd_v11202(update,context):
        msg=update.effective_message
        await msg.reply_text(
            "🟢 Анализирую Binance Spot: полный рынок + deep execution/flow. "
            "Сильный кандидат проходит WATCH → BUY READY → 🟢 BUY NOW при подтверждённой зоне и потоке.",
            reply_markup=base.main_menu(),
        )
        try:
            results=await base.spot_scan(force=False)
            await base._send_spot_results(
                context.bot,update.effective_chat.id,results,automatic=False
            )
        except Exception as exc:
            base.core.log.exception("V11.21.5 Spot manual scan failed")
            d=dict(base.spot_scan_status() or {})
            reason=str(d.get("reason") or f"{type(exc).__name__}: {exc}")
            await msg.reply_text(
                f"⚠️ Spot scan не завершён.\nПричина: {reason}",
                reply_markup=base.main_menu(),
            )
    base.spot_cmd=_spot_cmd_v11202

# Wrap FAST 15M with the same production routing/locking contract as PRIME.
if hasattr(base,"short_scan_cmd_v1142"):
    async def _short_scan_cmd_v11199(update,context):
        msg=update.effective_message
        try:
            from v11191_futures_engine import FULL_SCAN_BUDGET_SEC
            wait_limit=min(225,max(150,int(FULL_SCAN_BUDGET_SEC)+35))
            async with _v11205_full_scan_guard(wait_limit):
                health=await base.health_check(force=True)
                base._last_health=health
                if bool(getattr(health,"hard_pause",False)) or str(getattr(health,"status","")).upper()=="PAUSE":
                    reason=", ".join(getattr(health,"reasons",()) or ()) or "PRODUCTION HEALTH PAUSE"
                    raise RuntimeError(f"PRODUCTION HEALTH PAUSE: {reason}")
                results=await base.core.scan_short()

            await base.core._send_results(
                context.bot,update.effective_chat.id,results,short=True,
                diagnostics=base.core.scan_status().get("short")
            )
        except Exception as exc:
            base.core.log.exception("V11.21.5 manual short scan failed")
            text=_v11199_scan_error_text(
                exc,base.core.scan_status().get("short"),"FAST Futures"
            )
            await msg.reply_text(text,reply_markup=base.main_menu())

    base.short_scan_cmd_v1142=_short_scan_cmd_v11199
    # app.bot.callback resolves the module-global core.short_scan_cmd.
    base.core.short_scan_cmd=_short_scan_cmd_v11199


# V11.21.5: Spot Watchtower also consumes Futures derivatives REST for each
# watched counterpart. It must never overlap the 36->14 Futures research pass.
# ENTRY NOW is deliberately NOT put behind this gate.
_original_spot_watch_job_v11213=getattr(base,"spot_watch_job",None)
if _original_spot_watch_job_v11213 is not None:
    async def _spot_watch_job_v11213(context):
        # If a heavy scan owns the slot, skip this 2-minute watch tick. The next
        # watch tick will revalidate; do not queue behind a 2-3 minute scan.
        if _v11205_research_gate.locked():
            base.core.log.info("V11.21.5 Spot Watchtower skipped: research gate busy")
            return
        acquired=False
        try:
            await asyncio.wait_for(_v11205_research_gate.acquire(),timeout=.15)
            acquired=True

            # Recheck Production Health before the WATCH job starts its own
            # Futures counterpart snapshots.
            health=await base.health_check(force=True)
            base._last_health=health
            if bool(getattr(health,"hard_pause",False)) or str(getattr(health,"status","")).upper()=="PAUSE":
                base.core.log.warning("V11.21.5 Spot Watchtower skipped: Production Health PAUSE")
                return

            # Legacy/manual scan lock is a second guard against any unwrapped
            # research path that may already be active.
            if base.core._scan_lock.locked():
                base.core.log.info("V11.21.5 Spot Watchtower skipped: scan lock busy")
                return

            return await _original_spot_watch_job_v11213(context)
        except asyncio.TimeoutError:
            return
        finally:
            if acquired and _v11205_research_gate.locked():
                _v11205_research_gate.release()

    base.spot_watch_job=_spot_watch_job_v11213

# Serialize scheduled Fast Radar against heavy full-market research.
_original_fast_radar_job_v11205=getattr(base,"fast_radar_job",None)
if _original_fast_radar_job_v11205 is not None:
    async def _fast_radar_job_v11205(context):
        if _v11205_research_gate.locked():
            return
        acquired=False
        try:
            await asyncio.wait_for(_v11205_research_gate.acquire(),timeout=.10)
            acquired=True
            if base.core._scan_lock.locked():
                return
            return await _original_fast_radar_job_v11205(context)
        except asyncio.TimeoutError:
            return
        finally:
            if acquired and _v11205_research_gate.locked():
                _v11205_research_gate.release()
    base.fast_radar_job=_fast_radar_job_v11205

# Fail fast if a later patch silently restores stale Telegram handlers.
if base.core.scan_cmd is not _prime_scan_cmd_v11199:
    raise RuntimeError("PRIME FUTURES routing invariant failed")
if hasattr(base,"short_scan_cmd_v1142") and base.core.short_scan_cmd is not _short_scan_cmd_v11199:
    raise RuntimeError("FAST FUTURES routing invariant failed")
if base.core.callback is not _callback_v11203:
    raise RuntimeError("HEALTH callback routing invariant failed")
if base.spot_scan is not _serialized_spot_scan_v11202:
    raise RuntimeError("SPOT full-scan routing invariant failed")
if _original_spot_watch_job_v11213 is not None and base.spot_watch_job is not _spot_watch_job_v11213:
    raise RuntimeError("SPOT WATCHTOWER routing invariant failed")


if __name__=="__main__":
    base.core.main()
