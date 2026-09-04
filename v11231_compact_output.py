"""V11.23.1 · Compact trader-facing Telegram output.

Normal mode shows only:
- decision
- symbol / side / quality
- entry / stop / targets
- 2-3 plain-language reasons

Engineering telemetry remains available in System / Health.
"""
from __future__ import annotations

from html import escape
import bot_v11191 as runtime
import v11_ui
import spot_ui

base = runtime.base
VERSION = "11.23.1"

_old_futures_card = v11_ui.card
_old_spot_card = spot_ui.card


def _fmt(x):
    try:
        return f"{float(x):.8g}"
    except Exception:
        return str(x)


def _plain_reasons(signal, limit=3):
    snap = dict(getattr(signal, "feature_snapshot", {}) or {})
    fam = dict(snap.get("family_consensus_v11230") or {})
    out = []

    families = dict(fam.get("families") or {})
    ru = {
        "TREND": "тренд 1H/4H подтверждён",
        "MOMENTUM": "импульс достаточный",
        "FLOW": "OI и taker-flow подтверждают движение",
        "LOCATION": "цена находится в рабочей зоне входа",
        "EXECUTION": "ликвидность и условия исполнения нормальные",
    }
    for key in ("TREND", "FLOW", "LOCATION", "MOMENTUM", "EXECUTION"):
        if families.get(key):
            out.append(ru[key])

    if not out:
        for r in list(getattr(signal, "reasons", []) or []):
            s = str(r).strip()
            if s and "Independent families" not in s:
                out.append(s)

    # Deduplicate while preserving order.
    seen = set()
    clean = []
    for r in out:
        key = r.lower()
        if key not in seen:
            seen.add(key)
            clean.append(r)
        if len(clean) >= limit:
            break
    return clean


def futures_card_compact(s, priority=False):
    side = str(getattr(s, "side", "") or "").upper()
    icon = "🟢" if side == "LONG" else "🔴"
    prime = bool(getattr(s, "strong_prime_eligible", False))
    strong = bool(getattr(s, "strong_auto_eligible", False))
    label = "PRIME" if prime else ("STRONG" if strong else "SIGNAL")
    state = str(getattr(s, "entry_now_state", "SETUP") or "SETUP").upper()

    if state == "ENTER_NOW":
        action = "✅ ВХОД РАЗРЕШЁН"
    elif state in {"ARMED", "READY_PENDING"}:
        action = "⏳ ЖДЁМ ПОДТВЕРЖДЕНИЕ"
    else:
        action = "⚪ СЕТАП"

    reasons = _plain_reasons(s, 3)
    why = "\n".join(f"• {escape(x)}" for x in reasons) if reasons else "• условия прошли основной фильтр"

    return (
        f"{icon} <b>{escape(str(getattr(s,'symbol','?')))} · {side} · {label}</b>\n"
        f"{action}\n\n"
        f"Вход: <b>{_fmt(getattr(s,'entry_low',0))} — {_fmt(getattr(s,'entry_high',0))}</b>\n"
        f"Стоп: <b>{_fmt(getattr(s,'stop',0))}</b>\n"
        f"TP1: <b>{_fmt(getattr(s,'tp1',0))}</b> · TP2: <b>{_fmt(getattr(s,'tp2',0))}</b>\n"
        f"Качество: <b>{float(getattr(s,'professional_rank',getattr(s,'score',0)) or 0):.0f}/100</b>\n\n"
        f"<b>Почему:</b>\n{why}"
    )


def spot_card_compact(s, priority=False):
    state = str(getattr(s, "spot_entry_state", "WATCH") or "WATCH").upper()
    if state == "BUY_NOW":
        action = "✅ ПОКУПКА РАЗРЕШЕНА"
    elif state == "READY_PENDING":
        action = "⏳ ЖДЁМ 2/2"
    else:
        action = "⚪ ПОКА НЕ ПОКУПАЕМ"

    reasons = list(getattr(s, "reasons", []) or [])[:3]
    why = "\n".join(f"• {escape(str(x))}" for x in reasons) if reasons else "• сетап ещё не готов к покупке"

    return (
        f"🟢 <b>{escape(str(getattr(s,'symbol','?')))} · SPOT</b>\n"
        f"{action}\n\n"
        f"BUY: <b>{_fmt(getattr(s,'entry_low',0))} — {_fmt(getattr(s,'entry_high',0))}</b>\n"
        f"Стоп: <b>{_fmt(getattr(s,'invalidation',0))}</b>\n"
        f"TP1: <b>{_fmt(getattr(s,'tp1',0))}</b> · TP2: <b>{_fmt(getattr(s,'tp2',0))}</b>\n"
        f"Качество: <b>{float(getattr(s,'score',0) or 0):.0f}/100</b>\n\n"
        f"<b>Почему:</b>\n{why}"
    )


def heartbeat_compact(diagnostics, **kwargs):
    d = dict(diagnostics or {})
    final = int(d.get("final", 0) or 0)
    if final > 0:
        return f"✅ Сканирование завершено · найдено достойных сетапов: <b>{final}</b>"

    top_long = list(d.get("top_long_watch") or [])
    top_short = list(d.get("top_short_watch") or [])
    best = (top_long + top_short)[:1]

    if best:
        row = best[0]
        symbol = escape(str(row.get("symbol", "?")))
        side = escape(str(row.get("side", "")))
        score = float(row.get("score", 0) or 0)
        return (
            "⚪ <b>СЕЙЧАС НЕ ВХОДИМ</b>\n"
            f"Лучший кандидат: <b>{symbol} {side}</b> · {score:.0f}/100\n"
            "Причина: не хватает подтверждений для безопасного входа."
        )

    rejections = dict(d.get("rejections") or {})
    # Prefer a human reason instead of API telemetry.
    if int(rejections.get("FINAL_STRATEGY_REJECT", 0) or 0):
        reason = "лучшие монеты не набрали достаточный независимый консенсус"
    elif int(rejections.get("DEEP_EXECUTION_TIMEOUT", 0) or 0):
        reason = "часть кандидатов не успела пройти финальную проверку"
    elif int(rejections.get("DERIVATIVES_INCOMPLETE", 0) or 0):
        reason = "не хватило качественных derivatives-данных"
    else:
        reason = "нет сетапа с достаточным качеством и безопасной точкой входа"

    return f"⚪ <b>СЕЙЧАС НЕ ВХОДИМ</b>\nПричина: {reason}."


def install():
    v11_ui.card = futures_card_compact
    spot_ui.card = spot_card_compact

    # bot_v11191 imports card functions by value; patch those aliases too.
    base.card = futures_card_compact
    base.spot_card = spot_card_compact
    base.heartbeat_text = heartbeat_compact

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True


# V11.23.4 compact override: show independent family rating and WAIT explicitly.
_old_futures_card_v11231 = futures_card_compact

def futures_card_v11234(s, priority=False):
    snap = dict(getattr(s, "feature_snapshot", {}) or {})
    fg = dict(snap.get("final_gate_v11234") or {})
    fams = dict(fg.get("families") or {})
    aligned = int(fg.get("aligned", 0) or 0)
    side = str(getattr(s, "side", "") or "").upper()
    icon = "🟢" if side == "LONG" else "🔴"
    label = str(getattr(s, "strong_signal_label", "SIGNAL") or "SIGNAL")

    if label == "WAIT_ENTRY":
        action = "⏳ СИЛЬНЫЙ СЕТАП · ЖДЁМ ВХОД"
    elif label == "PRIME_STRONG":
        action = "🏆 PRIME · ВХОД МОЖНО РАССМАТРИВАТЬ"
    elif label == "STRONG":
        action = "✅ STRONG · ВХОД МОЖНО РАССМАТРИВАТЬ"
    else:
        action = "⚪ ПОКА НЕ ВХОДИМ"

    ru = {
        "TREND":"Тренд",
        "MOMENTUM":"Импульс",
        "FLOW":"Поток",
        "LOCATION":"Точка входа",
        "EXECUTION":"Исполнение",
    }
    checks = " · ".join(
        f"{ru[k]} {'✅' if fams.get(k) else '❌'}"
        for k in ("TREND","MOMENTUM","FLOW","LOCATION","EXECUTION")
    ) if fams else ""

    return (
        f"{icon} <b>{escape(str(getattr(s,'symbol','?')))} · {side}</b>\n"
        f"{action}\n\n"
        f"Вход: <b>{_fmt(getattr(s,'entry_low',0))} — {_fmt(getattr(s,'entry_high',0))}</b>\n"
        f"Стоп: <b>{_fmt(getattr(s,'stop',0))}</b>\n"
        f"TP1: <b>{_fmt(getattr(s,'tp1',0))}</b> · TP2: <b>{_fmt(getattr(s,'tp2',0))}</b>\n"
        f"Факторы: <b>{aligned}/5</b>\n\n"
        f"{checks}"
    )

def install_v11234_output():
    v11_ui.card = futures_card_v11234
    base.card = futures_card_v11234
    return True
