"""V11.23.8 · signal reachability + Spot Manager noise cleanup.

Changes:
- suppress repetitive SPOT MANAGER · RISK REVIEW Telegram cards;
- preserve actual Spot BUY/SELL/TP/SL/invalidation messages;
- promote only 3/5 Futures candidates that have the core trio
  TREND + LOCATION + EXECUTION to an actionable/armed setup;
- keep 4/5 and 5/5 logic unchanged and keep live micro revalidation authoritative.
"""
from __future__ import annotations

import inspect

import bot_v11191 as runtime
import v11235_signal_delivery_ui as ui235

base = runtime.base
core = base.core
VERSION = "11.23.8"

_ORIGINAL_GATE = ui235.apply_final_gate_v11235


def final_gate_v11238(signal):
    signal = _ORIGINAL_GATE(signal)
    snap = dict((getattr(signal, "feature_snapshot", {}) or {}).get("final_gate_v11235") or {})
    families = dict(snap.get("families") or {})
    aligned = int(snap.get("aligned", 0) or 0)

    core_three = bool(
        families.get("TREND") and families.get("LOCATION") and families.get("EXECUTION")
    )

    # A 3/5 setup is promoted only when it contains the three non-negotiable
    # concepts: direction/trend, valid location, and execution/risk quality.
    # It is ARMED, not ENTER_NOW; the existing fresh micro revalidation must
    # still confirm an actual entry.
    if aligned == 3 and core_three:
        signal.strong_prime_eligible = False
        signal.strong_auto_eligible = True
        signal.strong_signal_label = "ACTIONABLE_SETUP"
        old = str(getattr(signal, "entry_now_state", "SETUP") or "SETUP").upper()
        signal.entry_now_state = old if old == "ENTER_NOW" else "ARMED"
        signal.professional_rank = max(float(getattr(signal, "professional_rank", 0) or 0), 80.0)
        snap2 = signal.feature_snapshot.setdefault("final_gate_v11238", {})
        snap2.update({
            "activated_3of5_core_trio": True,
            "core_trio": ["TREND", "LOCATION", "EXECUTION"],
            "live_micro_confirmation_required": True,
        })
    return signal


def _is_spot_manager_noise(text) -> bool:
    s = str(text or "").upper()
    return "SPOT MANAGER" in s and "RISK REVIEW" in s


def _install_send_filter():
    # python-telegram-bot Application uses ExtBot. Patch the class method before
    # core.main() builds the application. This catches send_message calls and
    # Message.reply_text paths while targeting only the exact unwanted card.
    import telegram
    from telegram.ext import ExtBot

    patched = set()
    for cls in (telegram.Bot, ExtBot):
        original = getattr(cls, "send_message", None)
        if original is None or id(original) in patched:
            continue
        if getattr(original, "_v11238_spot_filter", False):
            continue

        async def filtered(self, *args, __orig=original, **kwargs):
            text = kwargs.get("text")
            if text is None and len(args) >= 2:
                text = args[1]
            if _is_spot_manager_noise(text):
                core.log.info("V11.23.8 suppressed repetitive SPOT MANAGER RISK REVIEW")
                return None
            return await __orig(self, *args, **kwargs)

        filtered._v11238_spot_filter = True
        setattr(cls, "send_message", filtered)
        patched.add(id(original))


def install():
    ui235.apply_final_gate_v11235 = final_gate_v11238
    # V11.23.6 resolves the gate through ui235 at runtime, so no scanner rewrite
    # is needed here.
    _install_send_filter()

    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    core.APP_VERSION = VERSION
    return True
