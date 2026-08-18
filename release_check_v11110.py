"""V11.11 source-contract release checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 "v11110_futures_orderbook.py":["pu!=self.last_update_id","snapshot bridge","@depth@100ms","sequence_synced","local_anchor","_TAPE_ANCHOR_SEC"],
 "v11110_contract.py":["bookTicker stale","bookTicker diverged","tolerance","quote=None"],
 "v11110_entry_now.py":["evaluate_entry_contract","capture_decision","ENTRY_GATE_VERSION",'state="WAIT"',"quote=quote"],
 "bot_v11110.py":["APP_VERSION=\"11.11.0\"","v11110-singleton-lease","v11110-futures-local-orderbook"],
 "v11110_tape.py":["<redacted>","gzip.compress","v11110_tape_bundle","source","event_count"],
 "v11110_lease.py":["BEGIN IMMEDIATE","expires_at","heartbeat","acquire_with_wait","time.monotonic"],
 "v11110_replay.py":["local_anchor","anchor_source","bridge_pending"],
 "v11_live.py":["/public/stream?streams=","/market/stream?streams=","@bookTicker","@aggTrade","_connected_public","_connected_market"],
}
for name,tokens in checks.items():
    path=ROOT/name
    if not path.exists(): issues.append(f"missing {name}"); continue
    src=path.read_text(encoding="utf-8")
    for token in tokens:
        if token not in src: issues.append(f"{name}: missing contract {token}")
rail=(ROOT/"railway.toml").read_text(encoding="utf-8")
for token in ("test_v11110_core.py","release_check_v11100.py","test_v11100.py"):
    if token not in rail: issues.append("railway missing "+token)
if not any(x in rail for x in ("preflight_v11110.py","preflight_v11120.py","preflight_v11121.py","preflight_v11122.py","preflight_v11130.py")):
    issues.append("railway missing V11.11+ preflight")
if not any(x in rail for x in ("bot_v11110.py","bot_v11120.py","bot_v11121.py","bot_v11122.py","bot_v11130.py")):
    issues.append("railway missing V11.11+ bot entrypoint")
if issues: raise SystemExit("V11.11 RELEASE CHECK FAILED:\n- "+"\n- ".join(issues))
print("V11.11 RELEASE CHECK: OK")
print("Futures L2 continuity + fail-closed entry contract: OK")
print("market tape + singleton lease contracts: OK")
