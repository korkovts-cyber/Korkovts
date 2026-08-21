"""V11.22.3 · End-to-end Futures + Spot production repair."""
from __future__ import annotations
import asyncio
import time
import bot_v11191 as runtime
import v11200_data_architecture as data_arch
import v11196_api_resilience as api_resilience
import v11191_futures_engine as futures
import v11191_spot_engine as spot_engine
import spot_market

base=runtime.base
VERSION="11.22.3"

# FUTURES: 155/210 was deterministic. 65s * 2.4 req/s ~= 156 starts.
# Restore enough light-request throughput while keeping V11.22.1 proactive
# request-weight reservation in front of dispatch.
data_arch.REQUESTS_PER_SEC=4.0
data_arch.MIN_START_GAP=1.0/data_arch.REQUESTS_PER_SEC
api_resilience.SOFT_WEIGHT_CEILING=min(
    int(getattr(api_resilience,"SOFT_WEIGHT_CEILING",800) or 800),800
)
try:
    import v11221_production_reachability as p221
    p221.WEIGHT_BUDGET_PER_MIN=min(
        int(getattr(p221,"WEIGHT_BUDGET_PER_MIN",780) or 780),780
    )
    p221.WEIGHT_RESERVE_CRITICAL=max(
        120,int(getattr(p221,"WEIGHT_RESERVE_CRITICAL",120) or 120)
    )
except Exception:
    p221=None

futures.FULL_SCAN_BUDGET_SEC=max(
    320,int(getattr(futures,"FULL_SCAN_BUDGET_SEC",280) or 280)
)
# Never "fix" this by lying about coverage.
futures.MIN_FRAME_COVERAGE=max(
    .90,float(getattr(futures,"MIN_FRAME_COVERAGE",.90) or .90)
)

# SPOT: all liquid symbols still receive daily ranking; only expensive deep
# 4H/1H/1m + L2 + aggTrades + derivatives work is bounded.
spot_engine.SPOT_DEEP_SHORTLIST=24
spot_market._sem=asyncio.Semaphore(5)
_spot_pace_lock=asyncio.Lock()
_spot_next_start=0.0
_spot_weight_lock=asyncio.Lock()
_spot_weight={"minute":None,"reserved":0,"waits":0,"requests":0}
SPOT_WEIGHT_BUDGET=900
SPOT_RPS=5.0
_original_spot_get=spot_market._get

def _spot_est_weight(path,params=None):
    path=str(path or ""); params=dict(params or {})
    if path.endswith("/ticker/24hr"): return 2 if params.get("symbol") else 80
    if path.endswith("/exchangeInfo"): return 20
    if path.endswith("/klines"): return 2
    if path.endswith("/depth"): return 5 if int(params.get("limit",100) or 100)<=100 else 25
    if path.endswith("/aggTrades"): return 4
    if path.endswith("/ticker/bookTicker"): return 4
    return 2

async def _spot_reserve(path,params=None):
    global _spot_next_start
    weight=_spot_est_weight(path,params)
    while True:
        now=time.time(); minute=int(now//60)
        async with _spot_weight_lock:
            if _spot_weight["minute"]!=minute:
                _spot_weight["minute"]=minute
                _spot_weight["reserved"]=0
            if int(_spot_weight["reserved"])+weight<=SPOT_WEIGHT_BUDGET:
                _spot_weight["reserved"]+=weight
                _spot_weight["requests"]+=1
                break
            _spot_weight["waits"]+=1
        await asyncio.sleep(max(.05,60.15-(now%60)))
    async with _spot_pace_lock:
        mono=time.monotonic()
        wait=max(0.0,_spot_next_start-mono)
        if wait: await asyncio.sleep(wait)
        _spot_next_start=max(time.monotonic(),_spot_next_start)+(1.0/SPOT_RPS)

async def spot_get_v11223(path,params=None):
    await _spot_reserve(path,params)
    return await _original_spot_get(path,params)

spot_market._get=spot_get_v11223

# Manual scans must always terminate with useful feedback.
async def spot_cmd_v11223(update,context):
    msg=update.effective_message
    await msg.reply_text(
        "🟢 Запускаю полный Spot scan: весь ликвидный рынок → daily rank → "
        "24 deep-кандидата → BUY/READY/WATCH. Максимум ~3.5 минуты."
    )
    try:
        results=await asyncio.wait_for(base.spot_scan(force=False),timeout=210.0)
        return await base._send_spot_results(
            context.bot,update.effective_chat.id,results,automatic=False
        )
    except asyncio.TimeoutError:
        d=dict(base.spot_scan_status() or {})
        reason=(
            f"timeout 210s · daily {int(d.get('daily_ok',0))}/"
            f"{int(d.get('liquid',0))} · deep {int(d.get('deep_checked',0))} · "
            f"errors {int(d.get('errors',0))}"
        )
        base.core.log.error("V11.22.3 Spot timeout: %s",reason)
        return await msg.reply_text(
            "⚠️ <b>SPOT SCAN НЕ ЗАВЕРШЁН</b>\n"
            f"Причина: <code>{base.escape(reason)}</code>\n"
            "Неполные данные не выдаются как сигнал.",
            parse_mode=base.ParseMode.HTML,reply_markup=base.main_menu()
        )
    except Exception as exc:
        d=dict(base.spot_scan_status() or {})
        reason=str(d.get("reason") or f"{type(exc).__name__}: {exc}")
        base.core.log.exception("V11.22.3 Spot scan failed")
        return await msg.reply_text(
            "⚠️ <b>SPOT SCAN НЕ ЗАВЕРШЁН</b>\n"
            f"Причина: <code>{base.escape(reason[:500])}</code>",
            parse_mode=base.ParseMode.HTML,reply_markup=base.main_menu()
        )

base.spot_cmd=spot_cmd_v11223

async def scan_cmd_v11223(update,context):
    msg=update.effective_message
    if base.core._scan_lock.locked():
        return await msg.reply_text(
            "⏳ Другой полный скан уже выполняется. Дождись завершения.",
            reply_markup=base.main_menu()
        )
    await msg.reply_text(
        "🔎 Запускаю полный Futures scan: actionable рынок → multi-TF → "
        "fast derivatives → full-deep."
    )
    try:
        async with base.core._scan_lock:
            results=await base.core.scan()
        return await base.core._send_results(
            context.bot,update.effective_chat.id,results,
            diagnostics=base.core.scan_status().get("main")
        )
    except Exception as exc:
        d=dict(base.core.scan_status().get("main") or {})
        reason=str(d.get("reason") or f"{type(exc).__name__}: {exc}")
        base.core.log.exception("V11.22.3 Futures scan failed")
        return await msg.reply_text(
            "⚠️ <b>FUTURES SCAN НЕ ЗАВЕРШЁН</b>\n"
            f"Причина: <code>{base.escape(reason[:500])}</code>\n"
            f"Primary: <b>{int(d.get('primary_frames_ok',0))}/"
            f"{int(d.get('primary_frames_target',0))}</b> · "
            f"Multi-TF: <b>{int(d.get('multiframe_ok',0))}/"
            f"{int(d.get('multiframe_target',0))}</b>",
            parse_mode=base.ParseMode.HTML,reply_markup=base.main_menu()
        )

base.core.scan_cmd=scan_cmd_v11223

_old_hb=base.heartbeat_text
def heartbeat_text_v11223(diagnostics,**kwargs):
    text=_old_hb(diagnostics,**kwargs)
    try:
        d=dict(diagnostics or {})
        ok=int(d.get("primary_frames_ok",0) or 0)
        target=int(d.get("primary_frames_target",0) or 0)
        if target:
            text+=f"\n🧭 Primary coverage: <b>{ok}/{target}</b> ({ok/max(1,target)*100:.0f}%)"
        text+=(
            f"\n⚙️ Futures research: <b>{data_arch.REQUESTS_PER_SEC:.1f} req/s</b> "
            f"· Spot deep <b>{spot_engine.SPOT_DEEP_SHORTLIST}</b>"
            f"\n🟢 Spot API: <b>{SPOT_RPS:.1f} req/s</b> · "
            f"weight <b>{int(_spot_weight['reserved'])}/{SPOT_WEIGHT_BUDGET}</b>"
        )
    except Exception:
        pass
    return text
base.heartbeat_text=heartbeat_text_v11223

_old_health=base.health_text
def health_text_v11223(h):
    text=_old_health(h)
    for old in ("V11.22.2","V11.22.1","V11.22.0","V11.21.9","V11.21.8","V11.21.7","V11.21.6"):
        text=text.replace(old,VERSION)
    return text
base.health_text=health_text_v11223

def install():
    base.spot_cmd=spot_cmd_v11223
    base.core.scan_cmd=scan_cmd_v11223
    base.APP_VERSION=VERSION
    base.config.APP_VERSION=VERSION
    base.core.APP_VERSION=VERSION
    return True
