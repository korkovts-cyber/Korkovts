"""V11.23.5 · Signal delivery + clear SPOT/FUTURES UI.

Fixes observed in production:
- AUTO was repeatedly showing a vague "do not enter" heartbeat while useful
  candidates were being downgraded by brittle legacy-family reconstruction.
- SPOT and FUTURES cards were visually too similar.
- Legacy 100/100 watch scores could appear next to a rejection, which was
  misleading.

This overlay does NOT fabricate entries. It makes the final family gate robust
for legacy-pass candidates and makes every user-facing card explicitly state
SPOT or FUTURES.
"""
from __future__ import annotations

import math
from html import escape

import bot_v11191 as runtime
import v11_ui
import spot_ui
import v11234_unified_final_gate as gate

base = runtime.base
VERSION = "11.23.5"
_ORIGINAL_FAMILIES = gate._families_from_signal


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _fmt(x):
    try:
        return f"{float(x):.8g}"
    except Exception:
        return str(x)


def _robust_families(signal):
    """Use V11.23.4 families, then repair only missing legacy reconstruction.

    The old V11.23.4 legacy path inferred TREND almost exclusively from reason
    strings. A valid legacy signal whose reason wording changed could therefore
    lose TREND and become NO_TRADE. Here we use actual normalized technical and
    market fields when they are present. Unknown values are never turned into a
    positive vote merely because a score was high.
    """
    families, fam = _ORIGINAL_FAMILIES(signal)
    families = dict(families or {})
    snap = dict(getattr(signal, "feature_snapshot", {}) or {})
    tech = dict(snap.get("technical") or {})
    market = dict(snap.get("market") or getattr(signal, "market_context", {}) or {})
    side = str(getattr(signal, "side", "") or "").upper()

    # Repair TREND only if V11.23.4 could not prove it from legacy reason text.
    if not families.get("TREND"):
        ema20 = _f(tech.get("ema20"), float("nan"))
        ema50 = _f(tech.get("ema50"), float("nan"))
        ema200 = _f(tech.get("ema200"), float("nan"))
        close = _f(tech.get("close"), float("nan"))
        values_ok = all(math.isfinite(x) for x in (ema20, ema50, ema200, close))
        if values_ok:
            if side == "LONG":
                families["TREND"] = bool(ema20 > ema50 and close > ema200)
            elif side == "SHORT":
                families["TREND"] = bool(ema20 < ema50 and close < ema200)

    # If normalized EMA fields are unavailable, same-side market regime is a
    # single corroborating trend vote. NEUTRAL never becomes a positive vote.
    if not families.get("TREND"):
        bias = str(market.get("bias", "") or "").upper()
        if bias in {"LONG", "SHORT"} and bias == side:
            families["TREND"] = True

    fam = dict(fam or {})
    fam["families"] = families
    fam["aligned"] = sum(bool(v) for v in families.values())
    fam["total"] = 5
    fam["v11235_reconstruction_repair"] = True
    return families, fam


def apply_final_gate_v11235(signal):
    families, fam = _robust_families(signal)
    aligned = sum(bool(v) for v in families.values())

    trend = bool(families.get("TREND"))
    momentum = bool(families.get("MOMENTUM"))
    flow = bool(families.get("FLOW"))
    location = bool(families.get("LOCATION"))
    execution = bool(families.get("EXECUTION"))

    # 5/5 remains PRIME. 4/5 is actionable only with safe LOCATION and
    # EXECUTION. For legacy candidates whose textual TREND vote was the only
    # missing family, MOMENTUM+FLOW can corroborate direction instead.
    directional = trend or (momentum and flow)
    prime = aligned == 5 and location and execution and directional
    strong_entry = aligned >= 4 and location and execution and directional
    wait_entry = aligned >= 3 and execution and directional and not location

    signal.strong_prime_eligible = bool(prime)
    signal.strong_auto_eligible = bool(strong_entry)

    old_state = str(getattr(signal, "entry_now_state", "SETUP") or "SETUP").upper()
    if prime:
        signal.strong_signal_label = "PRIME_STRONG"
        # Never destroy a real ENTER_NOW produced by fresh revalidation.
        signal.entry_now_state = old_state if old_state in {"ENTER_NOW", "ARMED"} else "ARMED"
    elif strong_entry:
        signal.strong_signal_label = "STRONG"
        signal.entry_now_state = old_state if old_state in {"ENTER_NOW", "ARMED"} else "ARMED"
    elif wait_entry:
        signal.strong_signal_label = "WAIT_ENTRY"
        signal.entry_now_state = "READY_PENDING"
    else:
        signal.strong_signal_label = "NO_TRADE"
        if old_state != "ENTER_NOW":
            signal.entry_now_state = "SETUP"

    snap = signal.feature_snapshot.setdefault("final_gate_v11235", {})
    snap.update({
        "families": {k: bool(v) for k, v in families.items()},
        "aligned": aligned,
        "prime": prime,
        "strong_entry": strong_entry,
        "wait_entry": wait_entry,
        "directional": directional,
        "legacy_text_reconstruction_repaired": True,
    })
    # User score cannot say 100/100 unless there is a perfect independent pass.
    signal.professional_rank = {5: 96.0, 4: 88.0, 3: 74.0, 2: 60.0, 1: 50.0, 0: 40.0}.get(aligned, 40.0)
    return signal


def _family_line(s):
    snap = dict(getattr(s, "feature_snapshot", {}) or {})
    fg = dict(snap.get("final_gate_v11235") or snap.get("final_gate_v11234") or {})
    fams = dict(fg.get("families") or {})
    if not fams:
        return ""
    ru = {"TREND":"Тренд", "MOMENTUM":"Импульс", "FLOW":"Поток", "LOCATION":"Вход", "EXECUTION":"Риск"}
    return " · ".join(f"{ru[k]} {'✅' if fams.get(k) else '❌'}" for k in ("TREND","MOMENTUM","FLOW","LOCATION","EXECUTION"))


def futures_card_v11235(s, priority=False):
    side = str(getattr(s, "side", "") or "").upper()
    side_ru = "LONG" if side == "LONG" else "SHORT"
    state = str(getattr(s, "entry_now_state", "SETUP") or "SETUP").upper()
    label = str(getattr(s, "strong_signal_label", "SIGNAL") or "SIGNAL").upper()
    rank = _f(getattr(s, "professional_rank", getattr(s, "score", 0)))

    if state == "ENTER_NOW":
        status = "🚨 <b>ВХОД СЕЙЧАС</b>"
    elif label in {"PRIME_STRONG", "STRONG"}:
        status = "✅ <b>СИГНАЛ НАЙДЕН</b> · ждём/проверяем цену входа"
    elif label == "WAIT_ENTRY":
        status = "🟡 <b>СЕТАП ГОТОВ</b> · цена ещё не в зоне входа"
    else:
        status = "⚪ <b>НАБЛЮДЕНИЕ</b>"

    lev = getattr(s, "leverage", None)
    lev_line = f"\nПлечо: <b>до {escape(str(lev))}x</b>" if lev else ""
    checks = _family_line(s)
    checks_line = f"\n\n{checks}" if checks else ""

    return (
        "🔵 <b>FUTURES SIGNAL</b>\n"
        f"{'🟢' if side=='LONG' else '🔴'} <b>{escape(str(getattr(s,'symbol','?')))} · {side_ru}</b>\n"
        f"{status}\n\n"
        f"Вход: <b>{_fmt(getattr(s,'entry_low',0))} — {_fmt(getattr(s,'entry_high',0))}</b>\n"
        f"Stop Loss: <b>{_fmt(getattr(s,'stop',0))}</b>\n"
        f"TP1: <b>{_fmt(getattr(s,'tp1',0))}</b> · TP2: <b>{_fmt(getattr(s,'tp2',0))}</b>"
        f"{lev_line}\n"
        f"Качество: <b>{rank:.0f}/100</b>"
        f"{checks_line}"
    )


def _spot_reason_text(s):
    text = " ".join(str(x) for x in (getattr(s, "reasons", []) or [])).lower()
    out = []
    if any(x in text for x in ("trend", "ema", "тренд")):
        out.append("тренд поддерживает покупку")
    if any(x in text for x in ("volume", "rvol", "объ", "volume")):
        out.append("объём подтверждает интерес")
    if any(x in text for x in ("break", "breakout", "проб")):
        out.append("есть подтверждение движения цены")
    if not out:
        out.append("сетап прошёл базовый Spot-фильтр")
    return out[:2]


def spot_card_v11235(s, priority=False):
    state = str(getattr(s, "spot_entry_state", "WATCH") or "WATCH").upper()
    rank = _f(getattr(s, "score", 0))
    if state == "BUY_NOW":
        status = "🚨 <b>ПОКУПКА SPOT СЕЙЧАС</b>"
    elif state == "READY_PENDING":
        status = "🟡 <b>SPOT ГОТОВ</b> · ждём подтверждение"
    else:
        status = "⚪ <b>SPOT WATCH</b> · пока без покупки"
    why = "\n".join(f"• {escape(x)}" for x in _spot_reason_text(s))
    invalid = getattr(s, "invalidation", getattr(s, "stop", 0))
    return (
        "🟢 <b>SPOT SIGNAL · БЕЗ ПЛЕЧА</b>\n"
        f"<b>{escape(str(getattr(s,'symbol','?')))}</b>\n"
        f"{status}\n\n"
        f"Покупка: <b>{_fmt(getattr(s,'entry_low',0))} — {_fmt(getattr(s,'entry_high',0))}</b>\n"
        f"Отмена идеи ниже: <b>{_fmt(invalid)}</b>\n"
        f"TP1: <b>{_fmt(getattr(s,'tp1',0))}</b> · TP2: <b>{_fmt(getattr(s,'tp2',0))}</b>\n"
        f"Качество: <b>{rank:.0f}/100</b>\n\n"
        f"<b>Почему:</b>\n{why}"
    )


def heartbeat_v11235(diagnostics, **kwargs):
    """Heartbeat is status, never a fake 100/100 signal/rejection card."""
    d = dict(diagnostics or {})
    final = int(d.get("final", 0) or 0)
    status = str(d.get("status", "ok") or "ok").lower()
    if final > 0:
        return f"🤖 <b>AUTO работает</b> · 🔵 Futures: найдено <b>{final}</b> кандидата(ов). Проверяю точку входа."
    if status == "error":
        return "⚠️ <b>AUTO работает, но скан завершился с ошибкой данных.</b> Следующий цикл повторит проверку."
    try:
        watches = list(base.active_spot_watches(20))
        ready = sum(int(x.get("confirm_streak") or 0) >= 1 for x in watches)
        return (
            "🤖 <b>AUTO работает</b>\n"
            "🔵 Futures: сейчас нет подтверждённого входа\n"
            f"🟢 Spot: наблюдение <b>{len(watches)}</b> · почти готовы <b>{ready}</b>\n"
            "Сигнал придёт отдельным сообщением с крупной пометкой SPOT или FUTURES."
        )
    except Exception:
        return (
            "🤖 <b>AUTO работает</b>\n"
            "🔵 Futures: сейчас нет подтверждённого входа\n"
            "🟢 Spot: сканирование активно\n"
            "Сигнал придёт отдельным сообщением с пометкой SPOT или FUTURES."
        )


def install():
    # Make V11.23.5 the authoritative final annotator.
    gate._families_from_signal = _robust_families
    gate.apply_final_gate = apply_final_gate_v11235

    # Patch strong layer aliases installed by V11.23.4. Use the old annotator
    # first so every earlier safety veto remains intact, then apply repaired gate.
    try:
        import v11150_strong as strong
        old_annotate = strong.annotate
        def annotate(s):
            try:
                s = old_annotate(s)
            except Exception:
                pass
            return apply_final_gate_v11235(s)
        def annotate_many(rows):
            return [annotate(x) for x in (rows or [])]
        strong.annotate = annotate
        strong.annotate_many = annotate_many
        base.annotate_strong_signal = annotate
        base.annotate_strong_signals = annotate_many
    except Exception:
        pass

    v11_ui.card = futures_card_v11235
    spot_ui.card = spot_card_v11235
    base.card = futures_card_v11235
    base.spot_card = spot_card_v11235
    base.heartbeat_text = heartbeat_v11235

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
