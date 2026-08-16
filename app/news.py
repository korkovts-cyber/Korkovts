import asyncio
import calendar
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from html import unescape as html_unescape

import feedparser
import httpx

from .config import (
    NEWS_ALERT_MAX_AGE_MIN,
    NEWS_CACHE_SECONDS,
    X_BEARER_TOKEN,
    X_NEWS_QUERY,
)

log=logging.getLogger(__name__)

FEEDS=(
    {"name":"CoinDesk","url":"https://www.coindesk.com/arc/outboundfeeds/rss/",
     "weight":1.0,"official":False,"market_only":True},
    {"name":"Cointelegraph","url":"https://cointelegraph.com/rss",
     "weight":0.85,"official":False,"market_only":True},
    {"name":"Decrypt","url":"https://decrypt.co/feed",
     "weight":0.90,"official":False,"market_only":True},
    {"name":"SEC","url":"https://www.sec.gov/news/pressreleases.rss",
     "weight":1.15,"official":True,"market_only":True},
    {"name":"Federal Reserve","url":"https://www.federalreserve.gov/feeds/press_all.xml",
     "weight":1.15,"official":True,"market_only":True},
)
WHITE_HOUSE={"name":"White House","url":"https://www.whitehouse.gov/news/",
             "index_url":"https://www.whitehouse.gov/sitemap_index.xml",
             "weight":1.20,"official":True,"market_only":True}
BASE_SOURCE_TOTAL=len(FEEDS)+1

POSITIVE=("approval","approved","approves","adoption","partnership","launch","upgrade","inflows",
          "record high","surge","rally","bullish","breakthrough","wins","growth","buy",
          "rate cut","cuts rates","strategic reserve","legalizes","settlement")
NEGATIVE=("hack","exploit","lawsuit","ban","bans","outflows","liquidation","crash","bearish",
          "fraud","stolen","breach","shutdown","reject","rejected","sell-off",
          "investigation","rate hike","raises rates","tariff","sanction","war","seized",
          "rejects","delist","default","bankruptcy")
SHOCK_TERMS=("etf approval","etf rejection","rate cut","rate hike","raises rates",
             "cuts rates","executive order","tariff","sanction","strategic reserve",
             "bankruptcy","hack","exploit","liquidation","approved","rejected","lawsuit",
             "investigation","breach","seized","delist","default","ban","bans")
INFLUENTIAL_ACTORS=("trump","president","musk","sec","federal reserve","fed","fomc",
                    "treasury","coinbase","binance","tether","circle","blackrock",
                    "microstrategy")
MARKET_ACTIONS=("says","said","posts","posted","announces","announced","launches",
                "backs","buys","sells","warns","orders","plans","files","approves",
                "rejects","halts","resumes","accepts","adopts","partners")
MARKET_RELEVANT=("bitcoin","btc","ethereum","ether","crypto","digital asset","blockchain",
                 "stablecoin","tokenization","token","altcoin","defi","nft","web3","wallet",
                 "solana","xrp","ripple","dogecoin","coinbase","binance","tether","circle",
                 "blackrock","microstrategy","spot etf","crypto etf","fomc","monetary policy",
                 "interest rate","federal funds","inflation","cpi","financial stability",
                 "bank liquidity","tariff","sanction","strategic reserve","digital dollar")
ASSETS={"BTC":("bitcoin","btc"),"ETH":("ethereum","ether","eth"),
        "BNB":("bnb","binance coin"),"SOL":("solana","sol"),
        "XRP":("xrp","ripple"),"DOGE":("dogecoin","doge"),
        "ADA":("cardano","ada"),"AVAX":("avalanche","avax"),
        "LINK":("chainlink","link"),"DOT":("polkadot","dot"),
        "SUI":("sui",),"TON":("toncoin","ton"),"TRX":("tron","trx")}
USER_AGENT="KorkovtsSignalAI/10R.3 (github.com/korkovts-cyber/Korkovts)"
_cache={"at":0,"data":{"global":0.0,"assets":{},"headlines":[],"items":[],
                         "breaking_events":[],"sources":0,"source_total":BASE_SOURCE_TOTAL,
                         "event_risk":0.0,"high_impact_count":0,"failed_sources":BASE_SOURCE_TOTAL,
                         "x_configured":bool(X_BEARER_TOKEN),"x_connected":False}}
_white_house_cache={"initialized":False,"signature":None,"items":[]}


def _contains_term(text,term):
    return re.search(rf"(?<!\w){re.escape(term.strip())}(?!\w)",text.lower()) is not None


def _contains_any(text,terms):
    return any(_contains_term(text,term) for term in terms)


def _headline_score(text):
    pos=sum(1 for term in POSITIVE if _contains_term(text,term))
    neg=sum(1 for term in NEGATIVE if _contains_term(text,term))
    return max(-1,min(1,(pos-neg)/2))


def _entry_epoch(entry):
    stamp=entry.get("published_parsed") or entry.get("updated_parsed")
    return float(calendar.timegm(stamp)) if stamp else None


def _freshness(age_minutes):
    if age_minutes is None: return .40
    if age_minutes<=360: return 1.0
    if age_minutes<=1440: return .75
    if age_minutes<=4320: return .40
    return .15


def _clean(value):
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",str(value or ""))).strip()


def _asset_mentions(text):
    return [asset for asset,terms in ASSETS.items() if _contains_any(text,terms)]


def _event_id(source,url,title):
    raw=f"{source}|{url}|{title}".encode("utf-8",errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:32]


def _make_item(source,title,summary,url,published_epoch):
    title=_clean(title)
    if not title:
        return None
    text=f"{title} {_clean(summary)[:500]}"
    if source.get("market_only") and not _contains_any(text,MARKET_RELEVANT):
        return None
    now=time.time()
    age_minutes=max(0,(now-published_epoch)/60) if published_epoch else None
    score=_headline_score(text)
    relevant=_contains_any(text,MARKET_RELEVANT)
    high_impact=relevant and (
        bool(source.get("official")) or bool(source.get("social"))
        or _contains_any(text,SHOCK_TERMS)
        or (_contains_any(text,INFLUENTIAL_ACTORS) and _contains_any(text,MARKET_ACTIONS)))
    direction="POSITIVE" if score>=.25 else ("NEGATIVE" if score<=-.25 else "NEUTRAL")
    published_at=(datetime.fromtimestamp(published_epoch,timezone.utc).isoformat()
                  if published_epoch else None)
    return {
        "id":_event_id(source["name"],url,title),"source":source["name"],
        "title":title,"url":str(url or ""),"published_at":published_at,
        "published_epoch":published_epoch or 0,"age_minutes":age_minutes,
        "score":score,"direction":direction,"high_impact":high_impact,
        "impact":"HIGH" if high_impact else "MEDIUM","official":bool(source.get("official")),
        "social":bool(source.get("social")),"assets":_asset_mentions(text),
        "weight":float(source.get("weight",1))*_freshness(age_minutes)*(1.35 if high_impact else 1),
    }


async def _fetch_feed(client,source):
    try:
        response=await client.get(source["url"])
        response.raise_for_status()
        entries=feedparser.parse(response.content).entries[:40]
        items=[]
        for entry in entries:
            item=_make_item(source,entry.get("title"),entry.get("summary"),
                            entry.get("link"),_entry_epoch(entry))
            if item:
                items.append(item)
        return {"ok":bool(entries),"name":source["name"],"items":items}
    except Exception as exc:  # noqa: BLE001 - one failed source must not cancel the radar.
        log.warning("news feed unavailable: %s (%s)",source["name"],exc)
        return {"ok":False,"name":source["name"],"items":[]}


def _parse_white_house(content):
    source=WHITE_HOUSE
    pattern=re.compile(
        r'<h2[^>]*wp-block-post-title[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a></h2>'
        r'[\s\S]{0,1200}?<time datetime="([^"]+)"',re.IGNORECASE|re.DOTALL)
    items=[]
    for url,title,published_text in pattern.findall(content):
        try:
            published=datetime.fromisoformat(published_text.replace("Z","+00:00")).timestamp()
        except ValueError:
            published=None
        item=_make_item(source,html_unescape(title),"",url,published)
        if item:
            items.append(item)
    return items[:30]


async def _fetch_white_house(client):
    try:
        index=await client.get(WHITE_HOUSE["index_url"])
        index.raise_for_status()
        stamps=re.findall(
            r'<loc>https://www\.whitehouse\.gov/post-sitemap[^<]*</loc>\s*<lastmod>([^<]+)</lastmod>',
            index.text,re.IGNORECASE)
        signature="|".join(stamps)
        if (_white_house_cache["initialized"]
                and signature==_white_house_cache["signature"]):
            return {"ok":True,"name":WHITE_HOUSE["name"],
                    "items":list(_white_house_cache["items"])}
        response=await client.get(WHITE_HOUSE["url"])
        response.raise_for_status()
        items=_parse_white_house(response.text)
        _white_house_cache.update(initialized=True,signature=signature,items=items)
        return {"ok":True,"name":WHITE_HOUSE["name"],"items":items}
    except Exception as exc:  # noqa: BLE001 - official source can fail independently.
        log.warning("news feed unavailable: White House (%s)",exc)
        return {"ok":False,"name":WHITE_HOUSE["name"],"items":[]}


async def _fetch_x(client):
    if not X_BEARER_TOKEN:
        return {"ok":False,"name":"X","items":[]}
    try:
        response=await client.get(
            "https://api.x.com/2/tweets/search/recent",
            headers={"Authorization":f"Bearer {X_BEARER_TOKEN}"},
            params={"query":X_NEWS_QUERY,"max_results":25,
                    "tweet.fields":"created_at,author_id","expansions":"author_id",
                    "user.fields":"username,name"})
        response.raise_for_status()
        payload=response.json()
        authors={row["id"]:row for row in payload.get("includes",{}).get("users",[])}
        items=[]
        for post in payload.get("data",[]):
            created=str(post.get("created_at","")).replace("Z","+00:00")
            published=datetime.fromisoformat(created).timestamp() if created else None
            author=authors.get(post.get("author_id"),{})
            username=author.get("username","unknown")
            source={"name":f"X @{username}","weight":1.20,"official":True,
                    "market_only":False,"social":True}
            item=_make_item(source,post.get("text"),"",
                            f"https://x.com/{username}/status/{post.get('id','')}",published)
            if item:
                items.append(item)
        return {"ok":True,"name":"X","items":items}
    except Exception as exc:  # noqa: BLE001 - X is optional; RSS sources must keep working.
        log.warning("X news source unavailable: %s",exc)
        return {"ok":False,"name":"X","items":[]}


def _deduplicate(items):
    unique=[]; seen=set()
    for item in sorted(items,key=lambda row:row["published_epoch"],reverse=True):
        key=re.sub(r"[^a-z0-9а-я]+"," ",item["title"].lower()).strip()[:180]
        if key and key not in seen:
            seen.add(key); unique.append(item)
    return unique


async def get_news_sentiment(force=False):
    if not force and time.time()-_cache["at"]<NEWS_CACHE_SECONDS:
        return _cache["data"]
    async with httpx.AsyncClient(timeout=15,follow_redirects=True,
                                 headers={"User-Agent":USER_AGENT,"Accept":"*/*"}) as client:
        batches=await asyncio.gather(
            *(_fetch_feed(client,source) for source in FEEDS),_fetch_white_house(client))
        if X_BEARER_TOKEN:
            batches=(*batches,await _fetch_x(client))
    source_total=BASE_SOURCE_TOTAL+(1 if X_BEARER_TOKEN else 0)
    source_count=sum(1 for batch in batches if batch["ok"])
    items=_deduplicate([item for batch in batches for item in batch["items"]])
    scored=[item for item in items[:40] if item["score"]]
    total_weight=sum(item["weight"] for item in scored)
    global_score=(sum(item["score"]*item["weight"] for item in scored)/max(2.5,total_weight)
                  if scored else 0)
    assets={}
    for asset in ASSETS:
        rows=[item for item in items if asset in item["assets"] and item["score"]]
        if rows:
            weight=sum(item["weight"] for item in rows)
            assets[asset]=sum(item["score"]*item["weight"] for item in rows)/max(.5,weight)
    high_impact=[item for item in items if item["high_impact"]
                 and item["age_minutes"] is not None and item["age_minutes"]<=1440]
    breaking=[item for item in high_impact if item["age_minutes"]<=NEWS_ALERT_MAX_AGE_MIN]
    event_risk=min(1.0,len(high_impact)/3)
    data={
        "global":max(-1,min(1,global_score)),"assets":assets,
        "headlines":[item["title"] for item in items[:8]],"items":items[:15],
        "breaking_events":breaking,"sources":source_count,"source_total":source_total,
        "source_names":[batch["name"] for batch in batches if batch["ok"]],
        "failed_sources":source_total-source_count,"event_risk":event_risk,
        "high_impact_count":len(high_impact),
        "high_impact_headlines":[item["title"] for item in high_impact[:8]],
        "x_configured":bool(X_BEARER_TOKEN),
        "x_connected":any(batch["name"]=="X" and batch["ok"] for batch in batches),
        "fetched_at":datetime.now(timezone.utc).isoformat(),
    }
    _cache.update(at=time.time(),data=data)
    return data


def for_symbol(snapshot,symbol):
    base=symbol.removesuffix("USDT")
    asset=float(snapshot.get("assets",{}).get(base,0))
    global_score=float(snapshot.get("global",0))
    global_weight=.60 if int(snapshot.get("high_impact_count",0)) else .35
    score=max(-1,min(1,asset*.75+global_score*.25)) if asset else global_score*global_weight
    relevant=[item["title"] for item in snapshot.get("items",[])
              if base in item.get("assets",[]) or item.get("high_impact")][:3]
    return {"score":score,"headlines":relevant or snapshot.get("headlines",[])[:3],
            "sources":int(snapshot.get("sources",0)),
            "event_risk":float(snapshot.get("event_risk",0)),
            "high_impact_count":int(snapshot.get("high_impact_count",0)),
            "breaking":bool(snapshot.get("breaking_events"))}
