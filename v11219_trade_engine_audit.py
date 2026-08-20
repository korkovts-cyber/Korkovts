"""V11.21.9 trading-engine audit."""
from __future__ import annotations
import asyncio, math
import bot_v11191 as runtime
import v11191_spot_engine as spot_engine
import v11200_data_architecture as data_arch
import v11196_api_resilience as api_resilience
import spot_strategy, spot_scanner
base=runtime.base
VERSION="11.21.9"

def _f(v,d=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else float(d)
    except Exception: return float(d)

# BUY READY downstream-threshold contradiction fix.
_old_analyze=spot_engine.analyze
def spot_analyze_v11219(*a,**k):
    r=_old_analyze(*a,**k)
    if r is None: return None
    snap=dict(getattr(r,"feature_snapshot",{}) or {})
    rel=dict(snap.get("spot_v11210") or {})
    if str(getattr(r,"status","")).upper()!="BUY" or not rel.get("buy_ready_relief"): return r
    regime=str((snap.get("market") or {}).get("regime") or getattr(r,"market_regime","NEUTRAL")).upper()
    old_s=_f(snap.get("required_score"),86); old_rp=_f(snap.get("required_relative_percentile"),85)
    s_floor=78.0 if regime=="BULL" else 80.0
    rp_floor=70.0 if regime=="BULL" else 75.0
    snap["original_required_score"]=old_s
    snap["original_required_relative_percentile"]=old_rp
    snap["required_score"]=min(old_s,s_floor)
    snap["required_relative_percentile"]=min(old_rp,rp_floor)
    snap.setdefault("spot_v11219",{}).update({
      "buy_ready_threshold_alignment":True,
      "effective_required_score":snap["required_score"],
      "effective_required_relative_percentile":snap["required_relative_percentile"],
      "regime":regime})
    r.feature_snapshot=snap
    return r
spot_engine.analyze=spot_analyze_v11219
spot_engine.legacy.analyze=spot_analyze_v11219
spot_strategy.analyze=spot_analyze_v11219
spot_scanner.analyze=spot_analyze_v11219

# READY 1/2 priority: score-only top10 previously could starve an almost-ready trade.
def _watch_priority(row):
    state=str(row.get("candidate_state") or "WATCH").upper(); streak=int(row.get("confirm_streak") or 0)
    return (streak>=1,state=="READY_PENDING",streak,_f(row.get("ready_score"),-1),_f(row.get("score")),str(row.get("updated_at") or ""))

def prioritized_spot_rows(limit=10):
    rows=list(base.active_spot_watches(40)); rows.sort(key=_watch_priority,reverse=True)
    return rows[:max(1,int(limit))]

def spot_orderbook_symbols_v11219(limit=10):
    out=[]; seen=set()
    try:
        for row in base.spot_reserved_signals(10):
            s=str(row.get("symbol") or "").upper()
            if s and s not in seen: out.append(s); seen.add(s)
            if len(out)>=int(limit): return tuple(out[:int(limit)])
    except Exception: pass
    for row in prioritized_spot_rows(40):
        s=str(row.get("symbol") or "").upper()
        if s and s not in seen: out.append(s); seen.add(s)
        if len(out)>=int(limit): break
    return tuple(out[:int(limit)])
base._spot_orderbook_symbols=spot_orderbook_symbols_v11219

try: import v11218_spot_entry_fix as s218
except Exception: s218=None

async def spot_watch_core_v11219(context):
    chats=list(base.core.subscribers())
    if not chats: return
    promoted=0
    async with base._spot_candidate_lock:
        rows=prioritized_spot_rows(10)
        if not rows: return
        clusters=set(base.spot_active_clusters()); positions=base.spot_reserved_signals(10)
        portfolio=[str(x.get("symbol") or "").upper() for x in positions]
        active=base.spot_reserved_count()
        if active>=2: return
        for row in rows:
            symbol=str(row.get("symbol") or "").upper()
            if base.spot_was_sent_recently(symbol,72):
                base.close_spot_watch(symbol,"COOLDOWN","recent delivered/pending BUY already exists"); continue
            book=base.spot_local_book(symbol,3.0,50); h=base.spot_book_stability(symbol,3.0)
            if book is None or not h.get("healthy"):
                base.record_spot_watch_check(symbol,None,f"local depth not ready: {h.get('reason','unsynchronised')}"); continue
            ask=float(book["asks"][0][0])
            if ask<=float(row.get("invalidation") or 0):
                base.close_spot_watch(symbol,"CANCELLED","price invalidated before BUY"); continue
            near=getattr(s218,"_near_original_zone",None) if s218 else None
            if near and not near(row,ask):
                base.reset_spot_ready(symbol,"waiting near original BUY zone",ask); continue
            cluster=str(row.get("portfolio_cluster") or symbol).upper()
            if cluster in clusters: continue
            corr=await base.spot_active_correlation_risk(symbol,portfolio)
            if corr.get("degraded") or corr.get("blocked"):
                base.record_spot_watch_check(symbol,ask,"correlation check blocks/waits"); continue
            signal,error=await base.spot_recheck_watch(row)
            if signal is None:
                base.reset_spot_ready(symbol,error or "fresh revalidation rejected",ask); continue
            if str(getattr(signal,"status","")).upper()!="BUY":
                base.reset_spot_ready(symbol,"fresh full revalidation still WATCH",ask); continue
            base.upsert_spot_watch(signal)
            streak=base.record_spot_ready(symbol,float(signal.score),ask,60)
            if streak<2:
                base._decorate_spot_entry(signal,"READY_PENDING",streak)
                base.record_spot_watch_check(symbol,ask,f"fresh BUY confirmation {streak}/2; priority monitoring active"); continue
            base._decorate_spot_entry(signal,"BUY_NOW",streak)
            sid=base.save_spot_signal(signal,delivered=False); base.record_v1180_decision("SPOT",sid,signal)
            payload=base.spot_card(signal,True)
            for chat in chats: base.enqueue_spot_delivery(sid,chat,payload)
            base.close_spot_watch(symbol,"PENDING_DELIVERY","WATCH -> BUY 2/2 passed; Telegram live revalidation pending",sid)
            clusters.add(base._spot_cluster_key(signal)); portfolio.append(symbol); promoted+=1; active+=1
            if active>=2: break
    if promoted: await base._deliver_spot_pending(context.bot)

async def spot_watch_job_v11219(context):
    if not list(base.core.subscribers()): return
    got=False
    try:
        await asyncio.wait_for(runtime._v11205_research_gate.acquire(),timeout=55.0); got=True
        health=await base.health_check(force=False); base._last_health=health
        if bool(getattr(health,"hard_pause",False)) or str(getattr(health,"status","")).upper()=="PAUSE": return
        if base.core._scan_lock.locked(): return
        await spot_watch_core_v11219(context)
    except asyncio.TimeoutError: base.core.log.info("V11.21.9 Spot WATCH deferred: research busy")
    except Exception: base.core.log.exception("V11.21.9 Spot WATCH failed")
    finally:
        if got and runtime._v11205_research_gate.locked(): runtime._v11205_research_gate.release()
base.spot_watch_job=spot_watch_job_v11219

# API headroom from live 429 evidence; no strategy threshold is weakened here.
data_arch.REQUESTS_PER_SEC=min(float(getattr(data_arch,"REQUESTS_PER_SEC",4.0) or 4.0),3.2)
data_arch.MIN_START_GAP=1.0/max(.5,data_arch.REQUESTS_PER_SEC)
api_resilience.SOFT_WEIGHT_CEILING=min(int(getattr(api_resilience,"SOFT_WEIGHT_CEILING",1050) or 1050),900)
try:
    import v11217_reliability as rel217
    rel217.DEEP_START_WEIGHT_CEILING=min(int(getattr(rel217,"DEEP_START_WEIGHT_CEILING",820) or 820),700)
except Exception: pass

_old_hb=base.heartbeat_text
def heartbeat_text_v11219(d,**k):
    t=_old_hb(d,**k)
    try:
        ready=[r for r in prioritized_spot_rows(10) if int(r.get("confirm_streak") or 0)>=1]
        if ready:
            r=ready[0]; t+=f"\\n🚦 Spot priority: <b>{base.escape(str(r.get('symbol','?')))}</b> · READY <b>{int(r.get('confirm_streak') or 0)}/2</b>"
        t+=f"\\n🧯 API research: <b>{data_arch.REQUESTS_PER_SEC:.1f} req/s</b> · soft weight <b>{api_resilience.SOFT_WEIGHT_CEILING}</b>"
    except Exception: pass
    return t
base.heartbeat_text=heartbeat_text_v11219

_old_health=base.health_text
def health_text_v11219(h):
    return _old_health(h).replace("V11.21.8","V11.21.9").replace("V11.21.7","V11.21.9").replace("V11.21.6","V11.21.9")
base.health_text=health_text_v11219

def install():
    base.APP_VERSION=VERSION; base.config.APP_VERSION=VERSION; base.core.APP_VERSION=VERSION; return True
