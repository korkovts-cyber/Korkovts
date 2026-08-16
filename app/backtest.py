"""Technical-only historical baseline.

This module cannot reproduce the live v10 decision because Binance does not
provide all historical ADL, order-book, crowding and news snapshots here.  The
authoritative v10 evaluation is the forward cohort stored by ``app.tracker``.
"""

import asyncio

import pandas as pd

from .config import MIN_SIGNAL_SCORE, ROUND_TRIP_COST_PCT
from .market import get_klines
from .strategy import analyze


async def run(symbol="BTCUSDT",tf="1h"):
    frames={"15m":("5m","1h","15min"),"1h":("15m","4h","1h"),"4h":("1h","1d","4h")}
    if tf not in frames: raise ValueError("Поддерживаются таймфреймы: 15m, 1h, 4h")
    lower_tf,higher_tf,delta=frames[tf]
    df,lower,higher=await asyncio.gather(get_klines(symbol,tf,450),get_klines(symbol,lower_tf,1500),
                                         get_klines(symbol,higher_tf,1000))
    trades=wins=losses=0; returns=[]
    last_exit=-1
    for i in range(250,len(df)-25):
        if i<=last_exit: continue
        decision_time=df.iloc[i-1].open_time+pd.Timedelta(delta)
        lo=lower[lower.open_time+pd.Timedelta(lower_tf)<=decision_time]
        hi=higher[higher.open_time+pd.Timedelta(higher_tf)<=decision_time]
        s=analyze(symbol,tf.upper(),df.iloc[:i],hi,MIN_SIGNAL_SCORE,lo,None,None,None)
        if not s: continue
        outcome=None; reward=0; active=False
        entry=s.entry_high if s.side=="LONG" else s.entry_low
        risk=abs(entry-s.stop)
        cost_r=entry*(ROUND_TRIP_COST_PCT/100)/risk if risk else 0
        for j in range(i,min(i+24,len(df))):
            c=df.iloc[j]
            entry_hit=float(c.low)<=entry<=float(c.high)
            invalid_hit=(float(c.low)<=s.stop if s.side=="LONG" else float(c.high)>=s.stop)
            if not active:
                if invalid_hit and not entry_hit:
                    outcome="invalidated"; reward=0; break
                if not entry_hit:
                    continue
                active=True
            # If entry, stop and target occur in one candle, count the stop first.
            if s.side=="LONG":
                if float(c.low)<=s.stop: outcome="loss"; reward=-1; break
                if float(c.high)>=s.tp2: outcome="win"; reward=2; break
            else:
                if float(c.high)>=s.stop: outcome="loss"; reward=-1; break
                if float(c.low)<=s.tp2: outcome="win"; reward=2; break
        if outcome in ("win","loss"):
            trades+=1; last_exit=j; returns.append(reward-cost_r)
            if outcome=="win": wins+=1
            else: losses+=1
    gains=sum(x for x in returns if x>0); losses_r=-sum(x for x in returns if x<0)
    equity=peak=max_dd=0
    for value in returns:
        equity+=value; peak=max(peak,equity); max_dd=max(max_dd,peak-equity)
    return {"scope":"technical_baseline_not_live_v10",
            "trades":trades,"wins":wins,"losses":losses,"win_rate":wins/trades*100 if trades else 0,
            "net_r":sum(returns),"profit_factor":gains/losses_r if losses_r else (999 if gains else 0),
            "max_drawdown_r":max_dd,"cost_pct":ROUND_TRIP_COST_PCT}

if __name__=="__main__":
    print("WARNING: technical baseline only; this is not a v10 performance test")
    print(asyncio.run(run()))
