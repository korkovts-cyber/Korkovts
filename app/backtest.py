import asyncio
from .market import get_klines
from .strategy import analyze

async def run(symbol="BTCUSDT",tf="1h"):
    df=await get_klines(symbol,tf,1500)
    trades=wins=losses=0
    for i in range(250,len(df)-3):
        s=analyze(symbol,tf,df.iloc[:i],None,75)
        if not s: continue
        trades+=1; c=df.iloc[i+1]
        if s.side=="LONG":
            if float(c.low)<=s.stop: losses+=1
            elif float(c.high)>=s.tp2: wins+=1
        else:
            if float(c.high)>=s.stop: losses+=1
            elif float(c.low)<=s.tp2: wins+=1
    return trades,wins,losses,(wins/trades*100 if trades else 0)

if __name__=="__main__":
    print(asyncio.run(run()))
