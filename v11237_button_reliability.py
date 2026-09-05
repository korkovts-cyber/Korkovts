"""V11.23.7 · Telegram button reliability hotfix.

Fixes silent inline-button failures without changing trading logic.
Common control-center actions are dispatched directly to the current app.bot
handlers after an immediate callback ACK. Namespaced/legacy buttons fall back to
the already-installed production callback chain.
"""
from __future__ import annotations

import asyncio

import bot_v11191 as runtime

base = runtime.base
core = base.core
VERSION = "11.23.7"

_ORIGINAL_CALLBACK = core.callback

# These actions are stable app.bot controls and do not need the layered legacy
# callback router. Calling the live handler objects here avoids stale callback
# aliases after late overlays rebind scan/UI functions.
_DIRECT = {
    "scan": core.scan_cmd,
    "short_scan": core.short_scan_cmd,
    "news": core.news_status,
    "movers": core.movers,
    "prices": core.prices,
    "alerts_on": core.alerts_on,
    "alerts_off": core.alerts_off,
    "status": core.status,
    "memory": core.memory,
    "system": core.system_status,
    "lab": core.lab_status,
    "clear_chat": core.clear_chat,
}


async def _safe_ack(query, text=None):
    try:
        await query.answer(text=text)
    except Exception:
        # Expired/double ACK must never make a real button action disappear.
        pass


async def _reply_failure(query, data, exc):
    core.log.exception("V11.23.7 button action failed: %s", data)
    try:
        await query.message.reply_text(
            "⚠️ Кнопка принята, но действие завершилось ошибкой.\n"
            f"Команда: <code>{str(data)[:80]}</code>\n"
            f"Ошибка: <code>{type(exc).__name__}</code>\n"
            "Попробуй ещё раз через несколько секунд.",
            parse_mode=base.ParseMode.HTML,
            reply_markup=base.main_menu(),
        )
    except Exception:
        core.log.exception("V11.23.7 failed to send button error card")


async def callback_v11237(update, context):
    query = getattr(update, "callback_query", None)
    if query is None:
        return await _ORIGINAL_CALLBACK(update, context)

    data = str(getattr(query, "data", "") or "")

    # Control Center should always recover instantly even if a previous panel's
    # legacy router is unhealthy.
    if data == "v11:menu":
        await _safe_ack(query)
        try:
            return await query.message.reply_text(
                "🎛 <b>YK CONTROL CENTER</b>",
                parse_mode=base.ParseMode.HTML,
                reply_markup=base.main_menu(),
            )
        except Exception as exc:
            await _reply_failure(query, data, exc)
            return None

    handler = _DIRECT.get(data)
    if handler is not None:
        # ACK before any network-heavy scan so Telegram never leaves the button
        # spinning while a 2–4 minute market pass runs.
        await _safe_ack(query, "Принято")
        try:
            return await handler(update, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _reply_failure(query, data, exc)
            return None

    # analyze:/price:/v11:/v112:/v113:/v114:/v115:/v116:/v117:/v118:/etc.
    # retain the full mature production router for specialized panels.
    try:
        return await _ORIGINAL_CALLBACK(update, context)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A specialized panel may fail, but it must no longer fail silently.
        await _safe_ack(query)
        await _reply_failure(query, data, exc)
        return None


def install():
    core.callback = callback_v11237
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    core.APP_VERSION = VERSION
    return True
