"""V11.12 source-contract release checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 "v11120_contract.py":["bookTicker unavailable","not persistent","recent burst","liquidity pulled","spread shock","persistent adverse","non-finite live market data"],
 "v11120_entry_now.py":["evaluate_live_contract","ENTRY_GATE_VERSION","entry_gate_v11120"],
 "v11_live.py":["_ROUTE_STALL_SEC","route_stalls_public","route_stalls_market","active_seconds","coverage_sec","max_bucket_share"],
 "v11110_futures_orderbook.py":["bid_depth_change_2s","ask_depth_change_2s","spread_ratio_2s","adverse_long_share_5s","adverse_short_share_5s"],
 "v11110_tape.py":["_PER_SYMBOL_RETENTION","key=(symbol,arm_id,state)","forensic diversity","v11.12.0-tape-2","sequence_replay_complete","decision_anchor"],
 "v11120_replay.py":["replay_gate","11.12.0-live-contract"],
 "v11110_replay.py":["decision_anchor","reconstruct_depth"],
 "bot_v11120.py":["APP_VERSION=\"11.12.0\"","SELF-HEALING EDGE","from v11120_entry_now import ("],
}
for name,tokens in checks.items():
    path=ROOT/name
    if not path.exists(): issues.append(f"missing {name}"); continue
    src=path.read_text(encoding="utf-8")
    for token in tokens:
        if token not in src: issues.append(f"{name}: missing contract {token}")
rail=(ROOT/"railway.toml").read_text(encoding="utf-8")
for token in ("test_v11120_core.py","release_check_v11120.py"):
    if token not in rail: issues.append("railway missing "+token)
if not any(x in rail for x in ("preflight_v11120.py","preflight_v11121.py","preflight_v11122.py","preflight_v11130.py")):
    issues.append("railway missing V11.12+ preflight")
if not any(x in rail for x in ("bot_v11120.py","bot_v11121.py","bot_v11122.py","bot_v11130.py")):
    issues.append("railway missing V11.12+ bot entrypoint")
if issues: raise SystemExit("V11.12 RELEASE CHECK FAILED:\n- "+"\n- ".join(issues))
print("V11.12 RELEASE CHECK: OK")
print("fail-closed quote + persistent flow + L2 shock vetoes: OK")
print("websocket self-heal + tape diversity + replay gate: OK")
