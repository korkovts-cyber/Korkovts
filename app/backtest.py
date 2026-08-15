import asyncio
from .market import get_klines
from .strategy import analyze

async def run(symbol="BTCUSDT",tf="1h"):
    df=await get_klines(symbol,tf,1500)
    trades=wins=losses=0
    last_exit=-1
    for i in range(250,len(df)-25):
        if i<=last_exit: continue
        s=analyze(symbol,tf,df.iloc[:i],None,60)
        if not s: continue
        outcome=None
        for j in range(i,min(i+24,len(df))):
            c=df.iloc[j]
            # If both levels occur in one candle, count the stop first (conservative).
            if s.side=="LONG":
                if float(c.low)<=s.stop: outcome="loss"; break
                if float(c.high)>=s.tp2: outcome="win"; break
            else:
                if float(c.high)>=s.stop: outcome="loss"; break
                if float(c.low)<=s.tp2: outcome="win"; break
        if outcome:
            trades+=1; last_exit=j
            if outcome=="win": wins+=1
            else: losses+=1
    return trades,wins,losses,(wins/trades*100 if trades else 0)

if __name__=="__main__":
    print(asyncio.run(run()))
