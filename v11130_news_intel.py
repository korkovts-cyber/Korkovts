"""V11.13 Global Event Intelligence.

Adds trusted macro/regulatory and broad-world shock feeds to the existing crypto
radar.  Headlines can trigger a re-scan and a directional hypothesis, but they
never authorize ENTRY NOW without the existing market/execution gates.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import math
import re
import time
from datetime import datetime, timezone
from html import escape


EXTRA_FEEDS=(
    {"name":"BLS","url":"https://www.bls.gov/feed/bls_latest.rss","trust":0.98,"official":True,"scope":"macro"},
    {"name":"ECB","url":"https://www.ecb.europa.eu/rss/press.html","trust":0.98,"official":True,"scope":"macro"},
    {"name":"ECB Statistics","url":"https://www.ecb.europa.eu/rss/statpress.html","trust":0.98,"official":True,"scope":"macro"},
    {"name":"CFTC","url":"https://www.cftc.gov/RSS/RSSGP/rssgp.xml","trust":0.98,"official":True,"scope":"regulation"},
    {"name":"CFTC Enforcement","url":"https://www.cftc.gov/RSS/RSSENF/rssenf.xml","trust":0.98,"official":True,"scope":"regulation"},
    {"name":"BBC World","url":"https://feeds.bbci.co.uk/news/world/rss.xml","trust":0.80,"official":False,"scope":"world"},
    {"name":"BBC Business","url":"https://feeds.bbci.co.uk/news/business/rss.xml","trust":0.82,"official":False,"scope":"world"},
)
EXTRA_CACHE_SEC=45
BREAKING_MAX_AGE_MIN=45
PRICE_DISCOVERY_BLOCK_SEC=180
USER_AGENT="KorkovtsSignalAI/11.13 event-intelligence"
_PROCESS_STARTED=time.time()
ONBOARDING_WINDOW_SEC=300
ONBOARDING_FRESH_SEC=120
_EXTRA_NAMES={x["name"] for x in EXTRA_FEEDS}
_cache={"at":0.0,"items":[],"ok":0,"total":len(EXTRA_FEEDS),"failed":[]}
_latest_symbols=()

ASSETS={
    "BTC":("bitcoin","btc"),"ETH":("ethereum","ether","eth"),"BNB":("bnb","binance coin"),
    "SOL":("solana","$sol","sol token"),"XRP":("xrp","ripple"),"DOGE":("dogecoin","doge"),
    "ADA":("cardano","$ada"),"AVAX":("avalanche","$avax"),"LINK":("chainlink","$link"),
    "DOT":("polkadot","$dot"),"SUI":("sui network","$sui","sui token"),"TON":("toncoin","$ton"),"TRX":("tron","$trx"),
}
GLOBAL_RELEVANT=(
    "federal reserve","fed ","fomc","interest rate","rate cut","rate hike","inflation","cpi","ppi",
    "payroll","employment","unemployment","jobs report","treasury","bond yield","dollar","liquidity",
    "ecb","central bank","recession","bank failure","bank crisis","default","debt ceiling","credit crisis",
    "tariff","sanction","trade war","war ","invasion","missile","nuclear","ceasefire","oil price","crude oil",
    "cyberattack","cyber attack","market crash","stock market","wall street","emergency","capital control",
    "bitcoin","ethereum","crypto","digital asset","stablecoin","exchange","binance","coinbase","tether","circle","hack","exploit","breach",
)
CATEGORIES=(
    ("HACK_EXPLOIT",("hack","exploit","breach","stolen","drain","cyberattack","cyber attack")),
    ("LISTING_DELISTING",("delist","delisting","listing","lists ","listed on")),
    ("STABLECOIN",("stablecoin","tether","usdt","usdc","circle","depeg","peg")),
    ("ETF_REGULATION",("etf","sec ","securities and exchange commission","cftc","regulation","lawsuit","enforcement")),
    ("MACRO_INFLATION",("inflation","consumer price","cpi","producer price","ppi")),
    ("MACRO_JOBS",("payroll","employment situation","unemployment","job openings","jobs report","nonfarm")),
    ("MACRO_RATES",("federal reserve","fomc","rate cut","rate hike","interest rate","ecb","monetary policy")),
    ("BANKING_LIQUIDITY",("bank failure","bank crisis","liquidity","credit crisis","deposit run","default","debt ceiling")),
    ("TRADE_SANCTIONS",("tariff","sanction","trade war","export control")),
    ("GEO_CONFLICT",("war ","invasion","missile","nuclear","ceasefire","military strike","attack on")),
    ("ENERGY_SHOCK",("oil price","crude oil","oil supply","opec","energy shock")),
    ("MARKET_STRESS",("market crash","stock market crash","stocks plunge","equities plunge","wall street plunges","sell-off","selloff","circuit breaker","volatility halt")),
    ("EXCHANGE",("binance","coinbase","kraken","exchange outage","withdrawals halted","withdrawal halt")),
)
POSITIVE=("approved","approval","rate cut","cuts rates","ceasefire","inflows","adoption","legalized","settlement")
NEGATIVE=("rejected","rate hike","raises rates","hack","exploit","breach","stolen","delist","default","bank failure",
          "sanction","tariff","invasion","missile","war ","lawsuit","enforcement action","depeg","outflows","halt withdrawals")


def _clean(x): return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",str(x or ""))).strip()
def _norm(x): return re.sub(r"[^a-z0-9]+"," ",str(x or "").lower()).strip()
def _contains(text,term): return re.search(rf"(?<!\w){re.escape(term.strip())}(?!\w)",text.lower()) is not None

def _entry_epoch(entry):
    stamp=entry.get("published_parsed") or entry.get("updated_parsed")
    if stamp: return float(calendar.timegm(stamp))
    raw=str(entry.get("published") or entry.get("updated") or "")
    try: return datetime.fromisoformat(raw.replace("Z","+00:00")).timestamp()
    except Exception: return 0.0

def _assets(text): return [a for a,terms in ASSETS.items() if any(_contains(text,t) for t in terms)]
def _category(text):
    low=" "+text.lower()+" "
    for name,terms in CATEGORIES:
        if any(t in low for t in terms): return name
    return "GENERAL_SHOCK"

def _market_relevant(text):
    low=" "+text.lower()+" "
    return any(t in low for t in GLOBAL_RELEVANT)

def _bias(category,text,assets):
    low=" "+text.lower()+" "
    pos=sum(1 for t in POSITIVE if t in low); neg=sum(1 for t in NEGATIVE if t in low)
    if category=="HACK_EXPLOIT": return "ASSET_NEGATIVE" if assets else "RISK_OFF"
    if category=="ETF_REGULATION":
        if ("etf" in low and any(t in low for t in ("approved","approval","approves"))) or any(t in low for t in ("dismisses lawsuit","drops lawsuit","closes investigation")):
            return "ASSET_POSITIVE" if assets else "RISK_ON"
        if ("etf" in low and any(t in low for t in ("rejected","rejection","rejects"))) or any(t in low for t in ("charges "," charged ","sues ","lawsuit against","enforcement action","ban ","bans ")):
            return "ASSET_NEGATIVE" if assets else "RISK_OFF"
        return "AMBIGUOUS"
    if category=="STABLECOIN":
        if any(t in low for t in ("depeg","loses peg","below peg","redemption halt","halts redemptions")):
            return "ASSET_NEGATIVE" if assets else "RISK_OFF"
        if any(t in low for t in ("peg restored","restores peg","resumes redemptions")):
            return "ASSET_POSITIVE" if assets else "RISK_ON"
        return "AMBIGUOUS"
    if category=="EXCHANGE":
        if any(t in low for t in ("outage","withdrawals halted","withdrawal halt","halts withdrawals","insolvency")):
            return "ASSET_NEGATIVE" if assets else "RISK_OFF"
        if any(t in low for t in ("resumes withdrawals","service restored","trading restored")):
            return "ASSET_POSITIVE" if assets else "RISK_ON"
        return "AMBIGUOUS"
    if category=="LISTING_DELISTING":
        if "delist" in low: return "ASSET_NEGATIVE"
        if "listing" in low or "listed on" in low: return "ASSET_POSITIVE"
    if category=="GEO_CONFLICT":
        if any(t in low for t in ("ceasefire broken","ceasefire collapses","ceasefire violated","truce broken","peace talks fail")):
            return "RISK_OFF"
        if any(t in low for t in ("ceasefire","peace agreement","de-escalation","deescalation","truce")):
            return "RISK_ON"
        return "RISK_OFF"
    if category=="TRADE_SANCTIONS":
        if any(t in low for t in ("lifts sanctions","sanctions lifted","removes tariff","tariff removed","cuts tariff")):
            return "RISK_ON"
        return "RISK_OFF"
    if category=="ENERGY_SHOCK":
        if any(t in low for t in ("oil prices fall","oil price falls","supply restored","output restored")):
            return "RISK_ON"
        return "RISK_OFF"
    if category=="MARKET_STRESS":
        return "RISK_OFF"
    if category=="BANKING_LIQUIDITY":
        if any(t in low for t in ("bank failure","bank collapse","default","deposit run","credit crisis")):
            return "RISK_OFF"
        if any(t in low for t in ("emergency liquidity","liquidity facility","deposit guarantee","backstop")):
            return "AMBIGUOUS"
        return "RISK_OFF" if neg else "AMBIGUOUS"
    if category=="MACRO_RATES":
        if "rate cut" in low or "cuts rates" in low: return "RISK_ON"
        if "rate hike" in low or "raises rates" in low: return "RISK_OFF"
        return "AMBIGUOUS"
    if category in {"MACRO_INFLATION","MACRO_JOBS"}:
        # Never infer surprise from the absolute data alone. Only explicit comparative language.
        hot=("above forecast" in low or "above expectations" in low or "hotter than expected" in low or "stronger than expected" in low)
        cool=("below forecast" in low or "below expectations" in low or "cooler than expected" in low or "weaker than expected" in low)
        if hot: return "RISK_OFF"
        if cool: return "RISK_ON"
        return "AMBIGUOUS"
    if pos>neg: return "ASSET_POSITIVE" if assets else "RISK_ON"
    if neg>pos: return "ASSET_NEGATIVE" if assets else "RISK_OFF"
    return "AMBIGUOUS"

def _score_from_bias(bias):
    return {"RISK_ON":0.45,"ASSET_POSITIVE":0.60,"RISK_OFF":-0.45,"ASSET_NEGATIVE":-0.65}.get(bias,0.0)

def _event_id(source,title,url):
    return hashlib.sha256(f"{source}|{title}|{url}".encode("utf-8",errors="ignore")).hexdigest()[:32]

def _item(source,title,summary,url,published):
    title=_clean(title); summary=_clean(summary)[:600]
    if not title: return None
    text=f"{title} {summary}"
    scope=str(source.get("scope") or "")
    low=text.lower()
    if scope=="world" and not _market_relevant(text): return None
    if scope=="macro" and not any(t in low for t in ("inflation","cpi","consumer price","ppi","producer price","employment","unemployment","payroll","job openings","interest rate","monetary policy","fomc","federal reserve","ecb","recession","wage","productivity","liquidity")):
        return None
    if scope=="regulation" and not any(t in low for t in ("crypto","digital asset","bitcoin","ether","ethereum","stablecoin","virtual currency","binance","coinbase","ftx","celsius","token")):
        return None
    cat=_category(text); assets=_assets(text)
    if cat=="LISTING_DELISTING" and not assets and not any(t in low for t in ("crypto","token","binance","coinbase")):
        cat="GENERAL_SHOCK"
    bias=_bias(cat,text,assets)
    now=time.time(); age=max(0.0,(now-published)/60.0) if published else None
    high=(cat!="GENERAL_SHOCK" or bool(assets)) and (age is None or age<=1440)
    trust=float(source.get("trust",.7)); confidence=trust
    # Ambiguous macro headlines still trigger a recheck, but carry no directional score.
    score=_score_from_bias(bias)
    return {
        "id":_event_id(source["name"],title,url),"source":source["name"],"title":title,"url":str(url or ""),
        "published_epoch":float(published or 0),
        "published_at":datetime.fromtimestamp(published,timezone.utc).isoformat() if published else None,
        "age_minutes":age,"official":bool(source.get("official")),"social":False,"assets":assets,
        "category":cat,"trade_bias":bias,"confidence":confidence,"source_trust":trust,
        "direction":"POSITIVE" if score>.15 else ("NEGATIVE" if score<-.15 else "NEUTRAL"),
        "score":score,"high_impact":high,"impact":"HIGH" if high else "MEDIUM",
        "weight":trust*(1.25 if high else 1.0),
        "rationale":_rationale(cat,bias),
    }

def _rationale(category,bias):
    names={
        "MACRO_RATES":"ставки и глобальная ликвидность","MACRO_INFLATION":"инфляция и ожидания по ставкам",
        "MACRO_JOBS":"рынок труда и ожидания по ставкам","ETF_REGULATION":"регуляторный/ETF катализатор",
        "HACK_EXPLOIT":"операционный/контрагентский риск","EXCHANGE":"риск инфраструктуры бирж",
        "STABLECOIN":"риск стейблкоинов/ликвидности","GEO_CONFLICT":"геополитический risk-off",
        "TRADE_SANCTIONS":"торговые ограничения и risk-off","BANKING_LIQUIDITY":"банковская/долларовая ликвидность",
        "ENERGY_SHOCK":"энергетический инфляционный шок","MARKET_STRESS":"резкий global risk-off / стресс фондового рынка","LISTING_DELISTING":"биржевой катализатор",
    }
    return f"{names.get(category,'глобальный рыночный шок')}; гипотеза {bias}"

async def _fetch_one(client,source,feedparser_module):
    try:
        r=await client.get(source["url"]); r.raise_for_status()
        entries=feedparser_module.parse(r.content).entries[:35]
        items=[]
        for e in entries:
            row=_item(source,e.get("title"),e.get("summary"),e.get("link"),_entry_epoch(e))
            if row: items.append(row)
        return bool(entries),source["name"],items
    except Exception:
        return False,source["name"],[]

async def _extra_items(force=False):
    if not force and time.time()-float(_cache["at"])<EXTRA_CACHE_SEC:
        return list(_cache["items"]),int(_cache["ok"]),list(_cache["failed"])
    import feedparser
    import httpx
    async with httpx.AsyncClient(timeout=12,follow_redirects=True,headers={"User-Agent":USER_AGENT,"Accept":"*/*"}) as client:
        rows=await asyncio.gather(*(_fetch_one(client,s,feedparser) for s in EXTRA_FEEDS))
    items=[i for ok,name,batch in rows for i in batch]
    ok=sum(1 for yes,_,__ in rows if yes); failed=[name for yes,name,__ in rows if not yes]
    _cache.update(at=time.time(),items=items,ok=ok,failed=failed)
    return list(items),ok,failed

def _enrich_existing(item):
    row=dict(item or {})
    text=f"{row.get('title','')}"
    cat=row.get("category") or _category(text); assets=list(row.get("assets") or _assets(text))
    bias=row.get("trade_bias") or _bias(cat,text,assets)
    source=str(row.get("source") or "")
    social=bool(row.get("social"))
    # An official account on a social network is still a social source, not a
    # primary publication channel. It must be independently corroborated.
    official=bool(row.get("official")) and not social
    default_trust=0.58 if social else (0.98 if official else 0.76)
    explicit=row.get("source_trust")
    trust=min(0.58,float(explicit)) if social and explicit is not None else (default_trust if explicit is None else float(explicit))
    row.update(category=cat,assets=assets,trade_bias=bias,official=official,social=social,source_trust=trust,
               confidence=trust if social else float(row.get("confidence") or trust),
               rationale=row.get("rationale") or _rationale(cat,bias))
    return row

def _dedupe(items):
    out=[]; seen=set()
    for row in sorted(items,key=lambda x:float((x or {}).get("published_epoch") or 0),reverse=True):
        key=_norm((row or {}).get("title"))[:180]
        if key and key not in seen: seen.add(key); out.append(row)
    return out


_STOP={"the","a","an","and","or","of","to","in","on","for","with","as","after","amid","says","said","new","latest"}
def _title_tokens(title):
    return {t for t in _norm(title).split() if len(t)>=3 and t not in _STOP}

def _cluster_breaking(items):
    """Collapse near-duplicate headlines while retaining corroboration evidence."""
    chosen=[]
    for row in sorted(items,key=lambda x:(float(x.get("confidence") or 0),float(x.get("published_epoch") or 0)),reverse=True):
        toks=_title_tokens(row.get("title")); merged=False
        for keep in chosen:
            if str(row.get("category"))!=str(keep.get("category")): continue
            a=set(row.get("assets") or []); b=set(keep.get("assets") or [])
            if a and b and not (a & b): continue
            kt=_title_tokens(keep.get("title")); union=toks|kt
            sim=(len(toks & kt)/len(union)) if union else 0.0
            if sim>=.45:
                keep["corroboration_count"]=max(int(keep.get("corroboration_count",0) or 0),int(row.get("corroboration_count",0) or 0)+1)
                keep["confidence"]=max(float(keep.get("confidence") or 0),float(row.get("confidence") or 0))
                merged=True; break
        if not merged: chosen.append(dict(row))
    return chosen

def _corroborate(items):
    for row in items:
        raw_age=row.get("age_minutes")
        age=float(raw_age) if raw_age is not None else 99999.0
        count=0
        for other in items:
            if other is row or str(other.get("source"))==str(row.get("source")): continue
            raw_oage=other.get("age_minutes")
            oage=float(raw_oage) if raw_oage is not None else 99999.0
            if abs(age-oage)>120: continue
            same_cat=row.get("category")==other.get("category") and row.get("category")!="GENERAL_SHOCK"
            aset=set(row.get("assets") or []); bset=set(other.get("assets") or [])
            if not same_cat or (aset and bset and not (aset & bset)):
                continue
            rt=_title_tokens(row.get("title")); ot=_title_tokens(other.get("title")); union=rt|ot
            overlap=len(rt & ot); similarity=(overlap/len(union)) if union else 0.0
            if overlap>=2 or similarity>=.25:
                count+=1
        row["corroboration_count"]=count
        base=float(row.get("source_trust") or .6)
        row["confidence"]=min(1.0,base+min(.18,.06*count))
        # Social is alert-worthy only when corroborated; official sources need no corroboration.
        row["trade_usable"]=bool(row.get("official") or row["confidence"]>=.78 or count>=1)
    return items

async def get_news_sentiment(base_fetcher, *args, **kwargs):
    """Merge current app.news with macro/world feeds; preserve base failover semantics."""
    force=bool(kwargs.get("force",False))
    base_error=""
    base_result,extra_result=await asyncio.gather(
        base_fetcher(*args,**kwargs),_extra_items(force=force),return_exceptions=True
    )
    if isinstance(base_result,Exception):
        base_error=f"{type(base_result).__name__}: {base_result}"
        base={"global":0.0,"assets":{},"items":[],"breaking_events":[],"sources":0,"source_total":0,
              "event_risk":0.0,"high_impact_count":0,"headlines":[],"base_news_error":base_error}
    else:
        base=dict(base_result or {})
    if isinstance(extra_result,Exception):
        extras,ok,failed=[],0,[f"GLOBAL_FEEDS:{type(extra_result).__name__}"]
    else:
        extras,ok,failed=extra_result
    base_items=[_enrich_existing(x) for x in (base.get("items") or [])]
    items=_corroborate(_dedupe(base_items+extras))
    high=[x for x in items if x.get("high_impact") and x.get("age_minutes") is not None and float(x["age_minutes"])<=1440]
    breaking=[]
    uptime=time.time()-_PROCESS_STARTED
    for x in high:
        age=x.get("age_minutes")
        if age is None or float(age)>BREAKING_MAX_AGE_MIN: continue
        if not (bool(x.get("official")) or float(x.get("confidence") or 0)>=.72): continue
        if str(x.get("source") or "") in _EXTRA_NAMES and uptime<ONBOARDING_WINDOW_SEC:
            published=float(x.get("published_epoch") or 0)
            if not published or published<_PROCESS_STARTED-ONBOARDING_FRESH_SEC:
                continue
        breaking.append(x)
    # Only high-confidence, non-ambiguous events can nudge sentiment. This is small by design;
    # ENTRY NOW still requires technical/derivatives/L2/taker confirmation.
    directional=[x for x in high[:20] if x.get("trade_usable") and x.get("trade_bias")!="AMBIGUOUS"]
    extra_global=0.0
    if directional:
        denom=sum(max(.2,float(x.get("confidence") or 0))*max(.5,float(x.get("weight") or 1)) for x in directional)
        extra_global=sum(float(x.get("score") or 0)*max(.2,float(x.get("confidence") or 0))*max(.5,float(x.get("weight") or 1)) for x in directional)/max(1.0,denom)
    base_global=float(base.get("global") or 0)
    base["global"]=max(-1.0,min(1.0,base_global*.75+extra_global*.25))
    assets=dict(base.get("assets") or {})
    for asset in ASSETS:
        rows=[x for x in directional if asset in (x.get("assets") or [])]
        if rows:
            val=sum(float(x.get("score") or 0)*float(x.get("confidence") or 0) for x in rows)/max(.5,sum(float(x.get("confidence") or 0) for x in rows))
            assets[asset]=max(-1.0,min(1.0,float(assets.get(asset,0))*.65+val*.35))
    base["assets"]=assets
    base["items"]=items[:30]
    base_breaking=[_enrich_existing(x) for x in (base.get("breaking_events") or [])]
    combined=_corroborate(_dedupe(base_breaking+breaking))
    combined=[x for x in combined if (bool(x.get("official")) or float(x.get("confidence") or 0)>=.72
              or (bool(x.get("social")) and int(x.get("corroboration_count",0) or 0)>=1))]
    base["breaking_events"]=_cluster_breaking(combined)
    base["high_impact_count"]=len(high)
    base["event_risk"]=min(1.0,max(float(base.get("event_risk") or 0),len(high)/4.0))
    base["source_total"]=int(base.get("source_total") or 0)+len(EXTRA_FEEDS)
    base["sources"]=int(base.get("sources") or 0)+ok
    global _latest_symbols
    syms=[]
    for event in base["breaking_events"]:
        assets=list(event.get("assets") or [])
        if assets:
            syms.extend(f"{a}USDT" for a in assets)
        else:
            syms.extend(("BTCUSDT","ETHUSDT"))
    _latest_symbols=tuple(dict.fromkeys(syms))[:8]

    base["global_intel"]={
        "extra_sources_ok":ok,"extra_sources_total":len(EXTRA_FEEDS),"failed_sources":failed,
        "high_impact":len(high),"breaking":len(base["breaking_events"]),
        "categories":sorted({str(x.get("category")) for x in high}),
        "base_error":base_error,
        "fetched_at":datetime.now(timezone.utc).isoformat(),
    }
    return base

def opinion_text(event):
    e=dict(event or {}); bias=str(e.get("trade_bias") or "AMBIGUOUS")
    bias_ru={"RISK_ON":"🟢 risk-on","RISK_OFF":"🔴 risk-off","ASSET_POSITIVE":"🟢 позитивно для актива",
             "ASSET_NEGATIVE":"🔴 негативно для актива","AMBIGUOUS":"⚪ направление неоднозначно"}.get(bias,bias)
    assets=", ".join(e.get("assets") or []) or "рынок в целом"
    conf=float(e.get("confidence") or 0)
    return (f"\n🧠 <b>NEWS INTELLIGENCE</b>\n"
            f"Категория: <b>{e.get('category','GENERAL_SHOCK')}</b>\n"
            f"Гипотеза: <b>{bias_ru}</b> · активы: <b>{assets}</b>\n"
            f"Доверие к событию: <b>{conf*100:.0f}%</b> · подтверждений: <b>{int(e.get('corroboration_count',0) or 0)}</b>\n"
            f"Причина: {e.get('rationale','рыночный шок')}\n"
            "⚠️ <b>По одному заголовку не входить.</b> Бот проверит цену, OI, taker-flow, L2 и ENTRY NOW.")

def for_symbol(base_for_symbol, snapshot, symbol):
    """Event-aware per-symbol news contract.

    Directional, high-confidence shocks become a catalyst/conflict score and are
    allowed to proceed to market confirmation. Ambiguous shocks stay fail-closed
    and block both directions until price discovery becomes clearer.
    """
    out=dict(base_for_symbol(snapshot,symbol) or {})
    asset=str(symbol).upper().removesuffix("USDT")
    relevant=[]
    for raw in snapshot.get("breaking_events") or []:
        e=_enrich_existing(raw)
        age=e.get("age_minutes")
        if age is not None and float(age)>BREAKING_MAX_AGE_MIN: continue
        aset=set(e.get("assets") or [])
        if aset and asset not in aset: continue
        trusted=(bool(e.get("official")) or float(e.get("confidence") or 0)>=.72
                 or (bool(e.get("social")) and int(e.get("corroboration_count",0) or 0)>=1))
        if not trusted: continue
        relevant.append(e)
    if not relevant:
        out["global_breaking"]=False
        return out
    # The newest/highest-confidence relevant event owns the immediate hypothesis.
    relevant.sort(key=lambda e:(float(e.get("confidence") or 0),float(e.get("published_epoch") or 0)),reverse=True)
    event=relevant[0]; bias=str(event.get("trade_bias") or "AMBIGUOUS")
    out.update({
        "event_category":event.get("category"),"event_confidence":float(event.get("confidence") or 0),
        "trade_bias":bias,"event_source":event.get("source"),"event_title":event.get("title"),
    })
    if bias=="AMBIGUOUS":
        age_min=event.get("age_minutes")
        age_sec=(float(age_min)*60.0) if age_min is not None else 0.0
        if age_min is None or age_sec<=PRICE_DISCOVERY_BLOCK_SEC:
            out["breaking"]=True; out["global_breaking"]=True
            out["event_risk"]=max(.80,float(out.get("event_risk") or 0))
            out["block"]=True
            out["price_discovery"]=True
        else:
            # No headline-direction guess after initial price discovery. Let the
            # full technical/derivatives/L2 stack decide the market reaction.
            out["breaking"]=False; out["global_breaking"]=False; out["block"]=False
            out["event_risk"]=min(.55,max(.25,float(out.get("event_risk") or 0)))
            out["price_discovery"]=False
            out["score"]=0.0
        return out
    directional=_score_from_bias(bias)
    if abs(directional)>=.4:
        # Direction is explicit enough to test against live price discovery.
        # Never marks the trade as ready: all technical/evidence/L2 gates remain.
        out["score"]=directional
        out["breaking"]=False; out["global_breaking"]=False; out["block"]=False
        out["event_risk"]=min(.55,max(.25,float(out.get("event_risk") or 0)))
        out["catalyst"]=True
    return out


def alert_text(event):
    event=dict(event or {})
    title=escape(str(event.get("title") or "Важная новость"))
    source=escape(str(event.get("source") or "?"))
    url=str(event.get("url") or "")
    source_line=(f'Источник: <a href="{escape(url,quote=True)}">{source}</a>'
                 if url.startswith(("https://","http://")) else f"Источник: <b>{source}</b>")
    return ("🚨 <b>GLOBAL / CRYPTO SHOCK</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<b>{title}</b>\n"+source_line+opinion_text(event)+
            "\n\n🔎 Запущена внеплановая перепроверка 15M/1H. "
            "Если рынок подтвердит гипотезу, 🚨 ENTRY NOW придёт отдельным сообщением.")


def latest_breaking_symbols():
    return tuple(_latest_symbols)
