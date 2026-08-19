from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parent
required={
    'bot_v11180.py','v11180_defense.py','v11110_futures_orderbook.py',
    'v11170_snapshot.py','test_v11180_core.py','release_check_v11180.py',
    'preflight_v11172.py','railway.toml','BUILD_MANIFEST_V11_18_0.json',
}
missing=sorted(x for x in required if not (ROOT/x).exists())
if missing:
    raise SystemExit('V11.18 PREFLIGHT FAILED: missing: '+', '.join(missing))

# Preserve every inherited fail-closed gate, including the pinned app/ contract.
runpy.run_path(str(ROOT/'preflight_v11172.py'),run_name='__v11172_preflight__')

bot=(ROOT/'bot_v11180.py').read_text()
defense=(ROOT/'v11180_defense.py').read_text()
l2=(ROOT/'v11110_futures_orderbook.py').read_text()
snapshot=(ROOT/'v11170_snapshot.py').read_text()

for token in (
    'APP_VERSION="11.18.0"',
    'assess_v11180_defense(',
    'defense=defense_decision',
    'signal_id=core.save_pending(fresh)',
    'except _EntryRevalidationWait as exc:',
    'return "WAIT",fresh,None,None',
    '[:V11_CANDIDATE_POOL]',
    'FINAL RISK GATE · V11.18.0',
    'YK CONTROL CENTER · V11.18.0',
):
    if token not in bot:
        raise SystemExit('V11.18 PREFLIGHT FAILED: bot contract missing '+token)

if bot.index('assess_v11180_defense(')>bot.index('signal_id=core.save_pending(fresh)'):
    raise SystemExit('V11.18 PREFLIGHT FAILED: defense after save_pending')

# A temporary final-risk veto must not destroy the ARMED setup. It remains
# fail-closed because save_pending is still reachable only after final_gate PASS.
block=bot[bot.index('if not final_gate.eligible:'):bot.index('priority_label=',bot.index('if not final_gate.eligible:'))]
if 'cancel_entry_arm(arm_id,reason)' in block or 'return "WAIT",fresh,None,None' not in block:
    raise SystemExit('V11.18 PREFLIGHT FAILED: final-risk WAIT lifecycle regressed')

for token in ('PERSISTENCE_CONFIRMATIONS','def required_confirmations','RANGE_LOW_VOL','EXTREME_VOL'):
    if token not in defense:
        raise SystemExit('V11.18 PREFLIGHT FAILED: persistence contract missing '+token)

for token in ('self.history.append((now,spread,b,a,imb,micro))','microprice_drift_bps_2s'):
    if token not in l2:
        raise SystemExit('V11.18 PREFLIGHT FAILED: Futures L2 microprice contract missing '+token)

for token in ('V11180_EVIDENCE_SCHEMA="11.18-market-snapshot-evidence-v1"','microprice_drift_bps_2s','adverse_long_share_5s','adverse_short_share_5s'):
    if token not in snapshot:
        raise SystemExit('V11.18 PREFLIGHT FAILED: snapshot evidence contract missing '+token)

print('V11.18 PREFLIGHT: OK')
