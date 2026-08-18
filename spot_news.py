"""Asset-aware news overlay for Spot 3–10 day signals.

News can veto or modestly confirm a technical Spot setup. It cannot create a BUY
by itself. Positive catalysts require source diversity; one serious negative
asset-specific headline is enough to block a BUY until it ages out.
"""
from __future__ import annotations

import re
import time

ALIASES={
    "BTC":("bitcoin","btc"),"ETH":("ethereum","ether","eth"),
    "BNB":("bnb","binance coin"),"SOL":("solana","sol"),
    "XRP":("xrp","ripple"),"DOGE":("dogecoin","doge"),
    "ADA":("cardano","ada"),"AVAX":("avalanche","avax"),
    "LINK":("chainlink","link"),"DOT":("polkadot","dot"),
    "SUI":("sui",),"TON":("toncoin","ton"),"TRX":("tron","trx"),
    "LTC":("litecoin","ltc"),"BCH":("bitcoin cash","bch"),
    "NEAR":("near protocol","near"),"APT":("aptos","apt"),
    "ARB":("arbitrum","arb"),"OP":("optimism","op"),
    "ATOM":("cosmos","atom"),"UNI":("uniswap","uni"),
    "AAVE":("aave",),"FIL":("filecoin","fil"),"INJ":("injective","inj"),
    "SEI":("sei",),"TIA":("celestia","tia"),"RENDER":("render","render network"),
    "TAO":("bittensor","tao"),"WLD":("worldcoin","world"),
}
POSITIVE=(
    "approval","approved","partnership","partners","integration","integrates",
    "upgrade","mainnet","launch","launches","adoption","adopts","inflows",
    "record volume","institutional","staking","buyback","burn","expands",
)
NEGATIVE=(
    "hack","exploit","breach","stolen","delist","delisting","lawsuit","sues",
    "investigation","outflows","shutdown","bankruptcy","insolvency","fraud",
    "unlock","token unlock","security incident","halt","suspends withdrawals",
)
SEVERE=("hack","exploit","breach","stolen","delist","bankruptcy","insolvency","fraud","suspends withdrawals")


def _term(text,term):
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])",text.lower()) is not None


def _headline_signature(title):
    text=re.sub(r"[^a-z0-9 ]+"," ",str(title or "").lower())
    stop={"the","a","an","and","or","to","of","for","in","on","with","at","from","by"}
    words=[w for w in text.split() if w not in stop]
    return " ".join(words[:12])


def _asset_match(text,base):
    base=str(base).upper()
    terms=list(ALIASES.get(base,()))
    # Raw ticker matching is only safe-ish for >=4 chars; for shorter tickers
    # we require a known alias to avoid ordinary-word false positives.
    if len(base)>=4:
        terms.append(base.lower())
    return any(_term(text,t) for t in terms)


def assess(snapshot,base):
    now=time.time(); relevant=[]
    for item in snapshot.get("items",[]) or []:
        title=str(item.get("title") or "")
        tagged={str(x).upper() for x in (item.get("assets") or [])}
        if not title or (str(base).upper() not in tagged and not _asset_match(title,base)):
            continue
        age_raw=item.get("age_minutes")
        age=999999.0 if age_raw is None else max(0.0,float(age_raw))
        if age>72*60:
            continue
        source=str(item.get("source") or "unknown")
        pos=sum(_term(title,t) for t in POSITIVE)
        neg=sum(_term(title,t) for t in NEGATIVE)
        severe=any(_term(title,t) for t in SEVERE)
        direction=1 if pos>neg else (-1 if neg>pos else 0)
        relevant.append({"title":title,"source":source,"age_min":age,"direction":direction,"severe":severe})
    negative=[r for r in relevant if r["direction"]<0]
    positive=[r for r in relevant if r["direction"]>0]
    severe_recent=[r for r in negative if r["severe"] and r["age_min"]<=48*60]
    recent_negative=[r for r in negative if r["age_min"]<=72*60]
    recent_positive=[r for r in positive if r["age_min"]<=72*60]
    pos_sources={r["source"] for r in recent_positive}
    neg_sources={r["source"] for r in recent_negative}
    pos_events={_headline_signature(r["title"]) for r in recent_positive if _headline_signature(r["title"])}
    # Two feeds repeating the same press release are one event, not independent alpha.
    catalyst=len(pos_sources)>=2 and len(pos_events)>=2 and not recent_negative
    block=bool(severe_recent) or (len(neg_sources)>=2 and len(recent_negative)>=2)
    sources=int(snapshot.get("sources",0) or 0)
    degraded=sources<1
    adjustment=3 if catalyst else (-6 if block else (-2 if negative else (-3 if degraded else 0)))
    return {
        "block":block,"catalyst":catalyst,"adjustment":adjustment,
        "positive_sources":len(pos_sources),"positive_events":len(pos_events),
        "negative_sources":len(neg_sources),"negative_count":len(recent_negative),
        "recent_negative":bool(recent_negative),
        "headlines":[r["title"] for r in relevant[:4]],
        "relevant":relevant[:8],
        "global_event_risk":float(snapshot.get("event_risk",0) or 0),
        "global_breaking":bool(snapshot.get("breaking_events")),
        "sources":sources,"degraded":degraded,
    }
