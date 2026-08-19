"""V11.12.2 AUTO PULSE helpers.

Pure decision helpers only: no network, Telegram, DB or exchange side effects.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping
from html import escape

AUTO_FULL_SCAN_MIN = 10
FAST_RADAR_INTERVAL_SEC = 60
FAST_RADAR_MAX_CANDIDATES = 2
FAST_RADAR_MIN_24H_QUOTE = 10_000_000.0
FAST_RADAR_MOVE_PCT = 0.35
FAST_RADAR_SOFT_MOVE_PCT = 0.20
FAST_RADAR_VOLUME_DELTA = 250_000.0


@dataclass(frozen=True)
class RadarPick:
    symbol: str
    move_pct: float
    volume_delta: float
    score: float
    source: str


def _finite(x, default=0.0):
    try:
        v=float(x)
    except (TypeError, ValueError):
        return float(default)
    return v if math.isfinite(v) else float(default)


def ticker_snapshot(tickers: Mapping[str, Mapping]) -> dict[str, tuple[float, float]]:
    """Keep only finite positive price/volume data for the next 60-second delta."""
    out={}
    for raw_symbol,row in (tickers or {}).items():
        symbol=str(raw_symbol).upper()
        price=_finite((row or {}).get("price"))
        quote_volume=_finite((row or {}).get("quote_volume"))
        if price>0 and quote_volume>=0:
            out[symbol]=(price,quote_volume)
    return out


def choose_radar_symbols(
    tickers: Mapping[str, Mapping],
    previous: Mapping[str, tuple[float, float]] | None,
    near_symbols: Iterable[str]=(),
    active_symbols: Iterable[str]=(),
    cooldown_symbols: Iterable[str]=(),
    max_candidates: int=FAST_RADAR_MAX_CANDIDATES,
) -> tuple[list[RadarPick], dict[str, tuple[float, float]]]:
    """Select a tiny focused set for deep re-analysis between 10-minute scans.

    This function NEVER promotes a trade. It only decides which symbols deserve
    the already-existing full Production analysis. Active/cooldown symbols are
    excluded because they are already monitored or intentionally deduplicated.
    """
    current=ticker_snapshot(tickers)
    previous=dict(previous or {})
    excluded={str(x).upper() for x in active_symbols} | {str(x).upper() for x in cooldown_symbols}
    picks=[]
    seen=set()

    # Reserve at most one focused slot for an old near-candidate. With the
    # default two-slot budget, the second slot remains available for a genuinely
    # new ticker impulse so AUTO cannot get stuck rechecking yesterday's shortlist.
    near_budget=1
    for raw in near_symbols or ():
        symbol=str(raw).upper()
        if symbol in seen or symbol in excluded or symbol not in current:
            continue
        row=(tickers or {}).get(symbol) or {}
        if _finite(row.get("quote_volume")) < FAST_RADAR_MIN_24H_QUOTE:
            continue
        picks.append(RadarPick(symbol,0.0,0.0,10_000.0,"near_candidate"))
        seen.add(symbol)
        if len(picks)>=near_budget:
            break

    movers=[]
    for symbol,(price,quote_volume) in current.items():
        if symbol in seen or symbol in excluded or quote_volume<FAST_RADAR_MIN_24H_QUOTE:
            continue
        old=previous.get(symbol)
        if not old:
            continue
        old_price,old_volume=old
        if old_price<=0:
            continue
        move_pct=(price/old_price-1.0)*100.0
        volume_delta=max(0.0,quote_volume-max(0.0,float(old_volume)))
        absolute_move=abs(move_pct)
        eligible=(absolute_move>=FAST_RADAR_MOVE_PCT or
                  (absolute_move>=FAST_RADAR_SOFT_MOVE_PCT and volume_delta>=FAST_RADAR_VOLUME_DELTA))
        if not eligible:
            continue
        # Ranking is discovery-only; the real Production gates decide the trade.
        score=absolute_move*10.0 + math.log1p(volume_delta/100_000.0)
        movers.append(RadarPick(symbol,move_pct,volume_delta,score,"ticker_impulse"))
    movers.sort(key=lambda x:(x.score,abs(x.move_pct),x.volume_delta),reverse=True)
    for pick in movers:
        if pick.symbol in seen:
            continue
        picks.append(pick); seen.add(pick.symbol)
        if len(picks)>=max(1,int(max_candidates)):
            break
    return picks,current


def heartbeat_text(
    diagnostics: Mapping | None,
    *,
    active_arms: int=0,
    fresh_setups: int=0,
    triggered: int=0,
    triggered_window: int=0,
    fast_radar_checked: int=0,
    fast_radar_armed: int=0,
    scan_error: str="",
    now: datetime | None=None,
) -> str:
    """One compact status message for the mandatory 10-minute AUTO heartbeat."""
    now=now or datetime.now(timezone.utc)
    stamp=now.strftime("%H:%M UTC")
    d=dict(diagnostics or {})
    liquid=int(d.get("liquid",0) or 0)
    prefiltered=int(d.get("prefiltered",0) or 0)
    deep=int(d.get("deep_checked",0) or 0)
    final=int(d.get("final",0) or 0)
    if scan_error:
        status="⚠️ <b>ДАННЫЕ/СКАН НЕ ЗАВЕРШЁН</b>"
        result="Новый вход не разрешён: обязательная проверка не завершилась."
    elif triggered>0:
        status=f"🚨 <b>ENTRY NOW ОТПРАВЛЕН: {int(triggered)}</b>"
        result="Сигнал отправлен сразу после подтверждения — без ожидания следующего 10-минутного цикла."
    elif triggered_window>0:
        status=f"✅ <b>ENTRY NOW ЗА ПОСЛЕДНИЕ 10М: {int(triggered_window)}</b>"
        result="На текущем полном скане нового входа нет, но сигнал уже был отправлен между циклами."
    else:
        status="⚪ <b>СИГНАЛОВ ДЛЯ ВХОДА НЕТ</b>"
        result="Бот продолжает наблюдение; слабые и неподтверждённые сделки не публикуются."
    reject_parts=[]
    reject_fields=(
        ("alpha_rejected","Alpha"),("metadata_rejected","MetaData"),
        ("execution_rejected","Execution"),("entry_rejected","EntryHist"),
        ("evidence_rejected","Evidence"),("meta_rejected","MetaOOS"),
        ("protection_rejected","Protection"),("indicator_rejected","Indicator"),
        ("adaptive_rejected","Adaptive"),
    )
    for key,label in reject_fields:
        value=int(d.get(key,0) or 0)
        if value>0:
            reject_parts.append(f"{label} −{value}")

    lines=[
        f"📡 <b>YK AUTO · 10M CHECK</b> · {stamp}",
        "━━━━━━━━━━━━━━━━━━━━",
        status,
        result,
        "",
        f"🔎 Рынок: <b>{liquid}</b> ликвидных → <b>{prefiltered}</b> кандидатов → <b>{deep}</b> deep-check → <b>{final}</b> финальных",
        f"🧪 Production rejects: <b>{escape(' · '.join(reject_parts) if reject_parts else 'нет')}</b>",
        f"👀 ENTRY watch: <b>{int(active_arms)}</b> · новых сетапов: <b>{int(fresh_setups)}</b>",
        f"⚡ Fast Radar: проверено <b>{int(fast_radar_checked)}</b> · поставлено на ENTRY-watch <b>{int(fast_radar_armed)}</b>",
        "⏱ Полный рынок: <b>каждые 10 минут</b> · ENTRY monitor: <b>30 сек</b> · Fast Radar: <b>60 сек</b>",
    ]
    if scan_error:
        safe=escape(" ".join(str(scan_error).split())[:180])
        lines.append(f"⚠️ Причина: <code>{safe}</code>")
    return "\n".join(lines)
