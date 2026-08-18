"""Offline replay helpers for V11.11 market-tape bundles."""
from __future__ import annotations

def reconstruct_depth(bundle,book_factory):
    """Reconstruct the captured USD-M local book from the latest valid anchor."""
    book=book_factory(str(bundle.get("symbol") or "UNKNOWN"))
    events=list(bundle.get("events",[]) or [])
    snapshots=[(i,e) for i,e in enumerate(events) if e.get("kind")=="depth_snapshot"]
    if not snapshots: return {"ok":False,"reason":"no depth snapshot/anchor in tape"}
    snap_index,snap_event=snapshots[-1]
    snap=snap_event.get("payload") or {}; book.load_snapshot(snap)
    source=str(snap.get("source") or "rest")
    if source in {"local_anchor","decision_anchor"}:
        if hasattr(book,"bridge_pending"): book.bridge_pending=False
        if hasattr(book,"history") and snap.get("history"):
            try:
                book.history.clear()
                book.history.extend(tuple(row) for row in snap.get("history",[]))
            except Exception:
                pass
    # A local anchor already represents the fully applied book at that receive
    # point, so only later events may be replayed. A REST snapshot is fetched
    # while depth events are buffered; those pre-snapshot events are therefore
    # still eligible to provide the required first U/u bridge.
    stream=events[snap_index+1:] if source in {"local_anchor","decision_anchor"} else events
    first=(source!="local_anchor"); applied=0
    for event in stream:
        if event.get("kind")!="depth": continue
        data=event.get("payload") or {}
        if int(data.get("u",0) or 0)<int(book.last_update_id): continue
        state=book.apply_event(data,now=float(event.get("recv_ts",0) or 0),first_after_snapshot=first)
        if state=="APPLIED": applied+=1; first=False
        elif state=="GAP": return {"ok":False,"reason":book.last_error,"applied":applied,"anchor_source":source}
    bids,asks=book.top(20) if book.synced else ([],[])
    return {"ok":bool(book.synced and bids and asks),"applied":applied,"last_update_id":int(book.last_update_id),
            "best_bid":float(bids[0][0]) if bids else 0.0,"best_ask":float(asks[0][0]) if asks else 0.0,
            "anchor_source":source,"history_samples":len(getattr(book,"history",()) or ())}

def compare_assessment(bundle,evaluator,frame_builder):
    """Re-run the deterministic entry evaluator from stored candles/context."""
    f1=frame_builder(bundle.get("frame1") or []); f3=frame_builder(bundle.get("frame3") or [])
    arm=bundle.get("arm") or {}; quote=bundle.get("quote") or {}; flow=bundle.get("flow") or {}
    px=float(quote.get("price") or 0) or None
    assessment=evaluator(arm,f1,f3,px=px,bk=quote,flow_row=flow)
    old=bundle.get("assessment") or {}
    return {"old_state":old.get("state"),"new_state":getattr(assessment,"state",None),"old_score":old.get("score"),"new_score":getattr(assessment,"score",None),"same_state":old.get("state")==getattr(assessment,"state",None)}
