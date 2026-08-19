"""V11.17 deterministic post-trade failure attribution from replay + outcome."""
from __future__ import annotations
import json,sqlite3
SCHEMA="11.17-failure-attribution-v1"

def _db_path():
    from app.config import DATABASE_PATH
    return DATABASE_PATH

def classify(payload,pnl_r=None,result=None):
    p=payload or {}
    execution=dict(p.get("execution_reality") or p.get("execution_reality_v11170") or {})
    snapshot=dict(p.get("market_snapshot") or p.get("market_snapshot_v11170") or {})
    edge=dict(p.get("indicator_edge") or {})
    final=dict(p.get("final_risk") or {})
    res=str(result or "").upper()
    if res in {"TP2","TP3"} or (pnl_r is not None and float(pnl_r)>=1.0): return "SUCCESS"
    if execution.get("freshness") is not None and float(execution.get("freshness") or 0)<.65: return "ENTRY_TIMING"
    if execution.get("stability_score") is not None and float(execution.get("stability_score") or 0)<65: return "LIQUIDITY"
    if snapshot.get("coherence_ok") is False: return "DATA_QUALITY"
    text=" ".join(str(x) for x in (edge.get("blockers",[]) or []))
    if "flow" in text.lower() or "cvd" in text.lower(): return "FLOW_REVERSAL"
    if res in {"SL","STOP","STOPPED"} and execution.get("impact_1000_bps") is not None and float(execution.get("impact_1000_bps") or 0)>4: return "EXECUTION_COST"
    if pnl_r is not None and float(pnl_r)<0: return "ALPHA_OR_REGIME"
    if final.get("eligible") is False: return "PRE_ENTRY_BLOCKED"
    return "UNRESOLVED"

def report_text(limit=50):
    try:
        con=sqlite3.connect(_db_path(),timeout=10); con.row_factory=sqlite3.Row
        try:
            rows=con.execute("""
                SELECT r.payload_json,s.pnl_r,s.result
                FROM v11160_entry_replay r
                LEFT JOIN signals s ON s.id=r.signal_id
                ORDER BY r.id DESC LIMIT ?
            """,(max(1,min(int(limit),200)),)).fetchall()
            counts={}
            for r in rows:
                try: label=classify(json.loads(r['payload_json'] or '{}'),r['pnl_r'],r['result'])
                except Exception: label='UNRESOLVED'
                counts[label]=counts.get(label,0)+1
        finally: con.close()
        lines=["🧭 <b>FAILURE ATTRIBUTION · V11.17</b>","━━━━━━━━━━━━━━━━━━"]
        if not counts:return "\n".join(lines+["Пока недостаточно ENTRY replay данных."])
        total=sum(counts.values())
        for k,n in sorted(counts.items(),key=lambda x:(-x[1],x[0])):
            lines.append(f"• {k}: <b>{n}</b> · {n/max(total,1):.0%}")
        lines+=["","<i>Диагностика причин; она не меняет Production-веса автоматически.</i>"]
        return "\n".join(lines)
    except Exception:return "⚠️ Attribution report временно недоступен."
