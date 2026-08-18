"""V11.12.1 control-audit source checks; no exchange calls."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
issues=[]
checks={
 "v11_live.py":["timestamp_source","quote_event_ts","recv_time_books","/public/stream?streams=","/market/stream?streams="],
 "bot_v11121.py":["APP_VERSION=\"11.12.1\"","V11.12.1 CONTROL AUDIT","YK CONTROL CENTER · V11.12.1","V11.12 LIVE ENTRY"],
 "test_v11121_core.py":["timestamp_less_bookticker_uses_receive_time","ui_version_is_current"],
}
for name,tokens in checks.items():
    p=ROOT/name
    if not p.exists(): issues.append(f"missing {name}"); continue
    src=p.read_text(encoding="utf-8")
    for token in tokens:
        if token not in src: issues.append(f"{name}: missing {token}")
rail=(ROOT/"railway.toml").read_text(encoding="utf-8")
for token in ("test_v11121_core.py","release_check_v11121.py"):
    if token not in rail: issues.append("railway missing "+token)
if not any(x in rail for x in ("preflight_v11121.py","preflight_v11122.py","preflight_v11130.py")):
    issues.append("railway missing V11.12.1+ preflight")
if not any(x in rail for x in ("bot_v11121.py","bot_v11122.py","bot_v11130.py")):
    issues.append("railway missing V11.12.1+ bot entrypoint")
if issues: raise SystemExit("V11.12.1 RELEASE CHECK FAILED:\n- "+"\n- ".join(issues))
print("V11.12.1 RELEASE CHECK: OK")
print("bookTicker receive-time fallback + current UI version: OK")
