"""V11.19.3 UI annotation."""
from __future__ import annotations

def install(v11_ui_module):
    original=v11_ui_module.card
    def card(signal,priority=False):
        text=original(signal,priority)
        deep=(getattr(signal,"feature_snapshot",{}) or {}).get("deep_market_v11190") or {}
        if deep:
            text+=(
                "\n\n🌐 <b>FULL-UNIVERSE REVIEW</b>\n"
                f"Market rank: LONG <b>{float(deep.get('soft_long',0)):.0f}</b> · "
                f"SHORT <b>{float(deep.get('soft_short',0)):.0f}</b>\n"
                "Сначала просмотрен весь ликвидный USDT-M рынок; тяжёлый derivatives/L2 "
                "анализ выполнен для широкого финального shortlist."
            )
        return text
    v11_ui_module.card=card
    return card
