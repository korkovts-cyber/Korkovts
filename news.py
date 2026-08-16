import asyncio
import calendar
import re
import time
import httpx
import feedparser

FEEDS=(
    ("https://www.coindesk.com/arc/outboundfeeds/rss/",1.0),
    ("https://cointelegraph.com/rss",0.85),
    ("https://decrypt.co/feed",0.90),
)

POSITIVE=("approval","approved","adoption","partnership","launch","upgrade","inflows",
          "record high","surge","rally","bullish","breakthrough","wins","growth","buy")
NEGATIVE=("hack","exploit","lawsuit","ban","outflows","liquidation","crash","bearish",
          "fraud","stolen","breach","shutdown","reject","rejected","sell-off","investigation")
HIGH_IMPACT=("sec","etf","federal reserve","fed ","rate cut","interest rate","regulation",
             "bankruptcy","hack","exploit","liquidation","approval","approved","rejected")
ASSETS={"BTC":("bitcoin","btc"),"ETH":("ethereum","ether","eth"),"BNB":("bnb","binance coin"),
        "SOL":("solana","sol"),"XRP":("xrp","ripple"),"DOGE":("dogecoin","doge"),
        "ADA":("cardano","ada"),"AVAX":("avalanche","avax"),"LINK":("chainlink","link"),
        "DOT":("polkadot","dot"),"SUI":("sui",),"TON":("toncoin","ton")}
_cache={"at":0,"data":{"global":0.0,"assets":{},"headlines":[]}}

def _headline_score(text):
    t=text.lower()
    present=lambda term: re.search(rf"(?<!\w){re.escape(term)}(?!\w)",t) is not None
    pos=sum(1 for x in POSITIVE if present(x)); neg=sum(1 for x in NEGATIVE if present(x))
    return max(-1,min(1,(pos-neg)/2))

def _freshness(entry):
    stamp=entry.get("published_parsed") or entry.get("updated_parsed")
    if not stamp: return .65
    age_hours=max(0,(time.time()-calendar.timegm(stamp))/3600)
    if age_hours<=6: return 1.0
    if age_hours<=24: return .75
    if age_hours<=72: return .40
    return .15

async def get_news_sentiment():
    if time.time()-_cache["at"]<300: return _cache["data"]
    async def fetch(url,source_weight):
        try:
            async with httpx.AsyncClient(timeout=12,follow_redirects=True) as c:
                r=await c.get(url,headers={"User-Agent":"CryptoSignalBot/1.0"}); r.raise_for_status()
                return [(e,source_weight) for e in feedparser.parse(r.content).entries[:30]]
        except Exception: return []
    batches=await asyncio.gather(*(fetch(url,weight) for url,weight in FEEDS))
    seen=set(); items=[]
    for batch in batches:
        for e,source_weight in batch:
            title=re.sub(r"\s+"," ",e.get("title","")).strip()
            if title and title.lower() not in seen:
                seen.add(title.lower())
                raw=_headline_score(title+" "+re.sub(r"<[^>]+>"," ",e.get("summary",""))[:400])
                impact=1.35 if any(x in title.lower() for x in HIGH_IMPACT) else 1.0
                items.append((title,raw,source_weight*_freshness(e)*impact))
    scored=[x for x in items if x[1]]
    weight=sum(w for _,_,w in scored[:30])
    global_score=sum(s*w for _,s,w in scored[:30])/max(2.5,weight) if scored else 0
    assets={}
    for asset,terms in ASSETS.items():
        vals=[(s,w) for title,s,w in items if any(re.search(rf"\b{re.escape(term)}\b",title.lower()) for term in terms)]
        if vals: assets[asset]=sum(s*w for s,w in vals)/max(.5,sum(w for _,w in vals))
    data={"global":max(-1,min(1,global_score)),"assets":assets,
          "headlines":[t for t,s,w in scored[:5]]}
    _cache.update(at=time.time(),data=data)
    return data

def for_symbol(snapshot,symbol):
    base=symbol.removesuffix("USDT")
    asset=float(snapshot.get("assets",{}).get(base,0))
    global_score=float(snapshot.get("global",0))
    score=max(-1,min(1,asset*.75+global_score*.25)) if asset else global_score*.35
    return {"score":score,"headlines":snapshot.get("headlines",[])}
