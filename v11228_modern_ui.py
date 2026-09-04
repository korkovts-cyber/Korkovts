"""V11.22.8 · Modern hierarchical Telegram UI.

UI-only release:
- compact main menu
- folder-style submenus
- existing callback_data preserved
- no trading logic, thresholds, scanner or API logic changed
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import bot_v11191 as runtime
import v11_ui

base = runtime.base
VERSION = "11.22.8"


def _b(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def main_menu(analyze_symbol=None):
    rows = [
        [_b("🎯  СИГНАЛЫ", "ui:signals")],
        [_b("🟢  SPOT", "v115:spot"), _b("📰  НОВОСТИ", "news")],
        [_b("🚀  РЫНОК", "movers"), _b("🔔  AUTO", "ui:auto")],
    ]
    if analyze_symbol:
        sym = str(analyze_symbol).upper()
        coin = sym.removesuffix("USDT")
        rows.append([_b(f"↻  ПЕРЕПРОВЕРИТЬ {coin}", f"analyze:{sym}")])
    rows.append([_b("🗂  ЕЩЁ", "ui:more")])
    return InlineKeyboardMarkup(rows)


def signals_menu():
    return InlineKeyboardMarkup([
        [_b("🏆  PRIME FUTURES", "scan")],
        [_b("⚡  FAST · 15M", "short_scan"), _b("🚨  ENTRY NOW", "v1142:entrynow")],
        [_b("👀  EARLY WATCH", "v1114:early"), _b("📍  FUTURES LIVE", "v1142:active")],
        [_b("←  ГЛАВНОЕ МЕНЮ", "ui:home")],
    ])


def auto_menu():
    return InlineKeyboardMarkup([
        [_b("🔔  AUTO · ON", "alerts_on"), _b("🔕  AUTO · OFF", "alerts_off")],
        [_b("📍  FUTURES LIVE", "v1142:active"), _b("📍  SPOT LIVE", "v117:spotactive")],
        [_b("🧠  MEMORY · 24H", "memory")],
        [_b("←  ГЛАВНОЕ МЕНЮ", "ui:home")],
    ])


def more_menu():
    return InlineKeyboardMarkup([
        [_b("📊  АНАЛИТИКА", "ui:analytics"), _b("🧪  ЛАБОРАТОРИЯ", "ui:labs")],
        [_b("🗂  ИСТОРИЯ", "ui:history"), _b("⚙️  СИСТЕМА", "ui:system")],
        [_b("🧹  ОЧИСТИТЬ ЧАТ", "clear_chat")],
        [_b("←  ГЛАВНОЕ МЕНЮ", "ui:home")],
    ])


def analytics_menu():
    return InlineKeyboardMarkup([
        [_b("📈  PERFORMANCE", "v1114:performance"), _b("🎯  PRECISION", "v113:meta")],
        [_b("🧠  ADAPTIVE EDGE", "v1116:adaptive"), _b("🎞  ENTRY REPLAY", "v1116:replay")],
        [_b("🧿  FINAL RISK", "v1117:risk"), _b("🎯  ENTRY QUALITY", "v114:entry")],
        [_b("🧭  FAILURE MAP", "v1117:attribution"), _b("🧬  FACTORS", "v112:lab")],
        [_b("←  НАЗАД", "ui:more")],
    ])


def labs_menu():
    return InlineKeyboardMarkup([
        [_b("🧪  CHALLENGER", "v1117:challenger"), _b("🧪  EDGE LAB", "v118:edgelab")],
        [_b("🧫  LAB", "lab"), _b("🧪  ROBUST", "v113:robust")],
        [_b("🧭  MANAGER", "v118:manager")],
        [_b("←  НАЗАД", "ui:more")],
    ])


def history_menu():
    return InlineKeyboardMarkup([
        [_b("🗂  FUTURES HISTORY", "status"), _b("🗂  SPOT HISTORY", "v115:spothistory")],
        [_b("📊  SPOT STATS", "v115:spotstats"), _b("👁  SPOT WATCH", "v116:spotwatch")],
        [_b("📍  SPOT LIVE", "v117:spotactive")],
        [_b("←  НАЗАД", "ui:more")],
    ])


def system_menu():
    return InlineKeyboardMarkup([
        [_b("📡  HEALTH", "v112:health"), _b("🛡  SYSTEM", "system")],
        [_b("🛡  SAFETY", "v1142:safety"), _b("🧠  MEMORY · 24H", "memory")],
        [_b("←  НАЗАД", "ui:more")],
    ])


_old_callback = base.core.callback


async def callback_v11228(update, context):
    q = update.callback_query
    data = str(getattr(q, "data", "") or "")

    menus = {
        "ui:home": main_menu,
        "ui:signals": signals_menu,
        "ui:auto": auto_menu,
        "ui:more": more_menu,
        "ui:analytics": analytics_menu,
        "ui:labs": labs_menu,
        "ui:history": history_menu,
        "ui:system": system_menu,
    }
    builder = menus.get(data)
    if builder is None:
        return await _old_callback(update, context)

    try:
        await q.answer()
    except Exception:
        pass

    markup = builder()
    try:
        # Folder navigation: same card, only keyboard changes.
        await q.edit_message_reply_markup(reply_markup=markup)
    except Exception:
        try:
            await q.message.reply_text("YK • CONTROL", reply_markup=markup)
        except Exception:
            base.core.log.exception("V11.22.8 menu navigation failed")
    return None


def install():
    # Patch every public menu alias that older layers may have retained.
    v11_ui.main_menu = main_menu
    base.main_menu = main_menu
    base.core.menu = main_menu
    base.core.callback = callback_v11228

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
