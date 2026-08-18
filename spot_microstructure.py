"""Spot execution-quality and aggressive-flow features."""
from __future__ import annotations

import math
import time
import pandas as pd


def _impact_for_quote(asks,quote_amount,mid):
    remaining=float(quote_amount); spent=0.0; qty=0.0
    for price,size in asks:
        level_quote=price*size
        take=min(remaining,level_quote)
        if take<=0:
            continue
        qty+=take/price; spent+=take; remaining-=take
        if remaining<=1e-9:
            break
    if remaining>max(1.0,quote_amount*.001) or qty<=0 or mid<=0:
        return 999.0
    avg=spent/qty
    return max(0.0,(avg-mid)/mid*10000)


def _closed_taker_flow(frame,lookback):
    if frame is None or getattr(frame,"empty",True):
        return {"buy_share":.5,"bars":0,"notional":0.0}
    x=frame.tail(int(lookback)).copy()
    if x.empty or "volume" not in x or "taker_buy_base" not in x:
        return {"buy_share":.5,"bars":0,"notional":0.0}
    volume=pd.to_numeric(x["volume"],errors="coerce").fillna(0)
    taker=pd.to_numeric(x["taker_buy_base"],errors="coerce").fillna(0)
    quote=(
        pd.to_numeric(x["quote_volume"],errors="coerce").fillna(0)
        if "quote_volume" in x else pd.Series(0.0,index=x.index)
    )
    total=float(volume.sum())
    return {
        "buy_share":float(taker.sum()/total) if total>0 else .5,
        "bars":int((volume>0).sum()),
        "notional":float(quote.sum()),
    }


def analyze_book(book,trades,now_ms=None,minute_frame=None):
    """Spot execution + flow state.

    Immediate BUY confirmation deliberately combines two different clocks:
    recent raw aggTrades for the current pulse and closed 1m taker flow for a
    stable 5–15 minute backdrop. A one-second burst on BTC can no longer
    authorize a weekly Spot BUY by itself.
    """
    bids=list(book.get("bids") or []); asks=list(book.get("asks") or [])
    if not bids or not asks:
        return {
            "healthy":False,"excellent":False,"spread_bps":999.0,
            "impact_1k_bps":999.0,"impact_5k_bps":999.0,
            "flow_reliable":False,"live_flow_reliable":False,
            "closed_flow_reliable":False,
        }
    best_bid=float(bids[0][0]); best_ask=float(asks[0][0]); mid=(best_bid+best_ask)/2
    if mid<=0 or best_ask<best_bid:
        return {
            "healthy":False,"excellent":False,"spread_bps":999.0,
            "impact_1k_bps":999.0,"impact_5k_bps":999.0,
            "flow_reliable":False,"live_flow_reliable":False,
            "closed_flow_reliable":False,
        }
    spread=(best_ask-best_bid)/mid*10000
    depth={}
    for bps in (10,20,50):
        lo=mid*(1-bps/10000); hi=mid*(1+bps/10000)
        bid_quote=sum(p*q for p,q in bids if p>=lo)
        ask_quote=sum(p*q for p,q in asks if p<=hi)
        depth[bps]={"bid":bid_quote,"ask":ask_quote,"total":bid_quote+ask_quote}
    b20=depth[20]["bid"]; a20=depth[20]["ask"]
    imbalance=(b20-a20)/(b20+a20) if b20+a20 else 0.0
    impact1=_impact_for_quote(asks,1000,mid)
    impact5=_impact_for_quote(asks,5000,mid)

    now_ms=int(now_ms or time.time()*1000)
    cutoff=now_ms-15*60_000
    recent=[r for r in trades if int(r.get("time_ms",0) or 0)>=cutoff]
    buy=sum(float(r.get("notional",0)) for r in recent if r.get("buyer_taker"))
    sell=sum(float(r.get("notional",0)) for r in recent if not r.get("buyer_taker"))
    total=buy+sell
    live_share=buy/total if total else .5
    live_imbalance=(buy-sell)/total if total else 0.0

    coverage_sec=0.0; latest_trade_age_sec=999999.0
    times=[int(r.get("time_ms",0) or 0) for r in recent if r.get("time_ms")]
    if times:
        coverage_sec=max(0.0,(max(times)-min(times))/1000)
        latest_trade_age_sec=max(0.0,(now_ms-max(times))/1000)

    # A liquid pair should provide enough activity inside the last 15m.
    # We do not require long raw-trade coverage because on BTC 1000 aggTrades
    # may span only seconds; the 1m candle layer below supplies that context.
    live_reliable=(
        total>=10_000 and len(recent)>=10 and latest_trade_age_sec<=90.0
    )

    flow5=_closed_taker_flow(minute_frame,5)
    flow15=_closed_taker_flow(minute_frame,15)
    closed_reliable=(
        flow15["bars"]>=12 and flow5["bars"]>=4
        and flow15["notional"]>0 and flow5["notional"]>0
    )
    closed_ok=(
        flow15["buy_share"]>=.50 and flow5["buy_share"]>=.50
    )

    # General execution health may survive temporarily missing flow context, but
    # a real BUY requires both live_reliable and closed_reliable below.
    healthy=(
        spread<=8.0 and impact5<=20.0 and imbalance>=-.45
        and (not live_reliable or live_share>=.45)
    )
    flow_reliable=bool(live_reliable and closed_reliable)
    excellent=(
        spread<=3.0 and impact5<=8.0 and imbalance>=-.20
        and flow_reliable and live_share>=.54
        and flow15["buy_share"]>=.52 and flow5["buy_share"]>=.52
    )
    return {
        "healthy":bool(healthy),"excellent":bool(excellent),
        "bid":best_bid,"ask":best_ask,"mid":mid,"spread_bps":spread,
        "impact_1k_bps":impact1,"impact_5k_bps":impact5,
        "depth_10bps":depth[10]["total"],"depth_20bps":depth[20]["total"],
        "depth_50bps":depth[50]["total"],"book_imbalance_20bps":imbalance,
        "buy_share":float(live_share),"flow_imbalance":float(live_imbalance),
        "flow_notional":float(total),"flow_trades":int(len(recent)),
        "flow_reliable":flow_reliable,
        "live_flow_reliable":bool(live_reliable),
        "closed_flow_reliable":bool(closed_reliable),
        "closed_buy_share_5m":float(flow5["buy_share"]),
        "closed_buy_share_15m":float(flow15["buy_share"]),
        "closed_flow_ok":bool(closed_ok),
        "trade_coverage_sec":coverage_sec,
        "latest_trade_age_sec":latest_trade_age_sec,
    }
