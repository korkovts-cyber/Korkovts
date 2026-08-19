"""V11.19 UI additions for execution plan and deep-scan transparency."""
from __future__ import annotations

from html import escape


def install(v11_ui_module):
    original = v11_ui_module.card

    def card(signal, priority=False):
        text = original(signal, priority)
        if not bool(getattr(signal, "deep_analysis", False)):
            return text

        side = str(getattr(signal, "side", "LONG")).upper()
        entry = float(getattr(signal, "entry_high" if side == "LONG" else "entry_low", 0) or 0)
        stop = float(getattr(signal, "stop", 0) or 0)
        stop_pct = abs(entry-stop)/entry*100 if entry > 0 else 0.0
        lev = int(getattr(signal, "leverage", 1) or 1)
        risk = float(getattr(signal, "position_risk_pct", .35) or .35)
        scanned = int(getattr(signal, "deep_scan_universe", 0) or 0)
        rank = int(getattr(signal, "deep_scan_rank", 0) or 0)
        lev_reason = escape(str(getattr(signal, "leverage_reason", ""))[:180])

        execution = (
            "\n\n🧭 <b>DEEP EXECUTION PLAN</b>\n"
            f"🔬 Deep derivatives: <b>{scanned} liquid markets</b> · analysis order <b>#{rank}</b>\n"
            f"💼 Risk budget: <b>{risk:.2f}% of capital</b> · stop distance <b>{stop_pct:.2f}%</b>\n"
            f"⚙️ Recommended max leverage: <b>{lev}×</b>\n"
            f"↳ {lev_reason}\n"
            "📌 Position size is calculated from the STOP; leverage must not increase the preset loss.\n"
            "🚪 TP1: partial reduction · TP2: core target · TP3: runner only while structure remains valid."
        )
        marker = "\n<i>YK rule:"
        if marker in text:
            return text.replace(marker, execution + marker, 1)
        return text + execution

    v11_ui_module.card = card
    return card
