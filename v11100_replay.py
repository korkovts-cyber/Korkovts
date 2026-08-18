"""Decision-layer replay utilities for V11.10.

This is intentionally a *decision replay*, not a claim that OHLC/L2 chronology
can be reconstructed from a single JSON snapshot.  Black-box payloads preserve
all decision features available at the original gate, allowing future ranking
layers to be tested against exactly the same candidate sets.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from typing import Callable

from app.config import DATABASE_PATH
from v1171_sqlite import db_session
from v11100_blackbox import init, DECISION_RELEASE
from v11100_edge import selection_key


def _signal_from_payload(payload):
    data=dict(payload or {})
    # feature/market snapshots stay nested while the scalar fields become attrs.
    return SimpleNamespace(**data)


def candidate_sets(limit_scans:int=50):
    init()
    with db_session(DATABASE_PATH,row_factory=sqlite3.Row) as c:
        scan_ids=[r[0] for r in c.execute("""
            SELECT scan_id FROM v1190_blackbox
            WHERE release_version=? AND stage='FINAL_DECISION'
            GROUP BY scan_id ORDER BY MAX(id) DESC LIMIT ?
        """,(DECISION_RELEASE,int(limit_scans))).fetchall()]
        output=[]
        for scan_id in scan_ids:
            rows=c.execute("""
                SELECT selected,payload_json FROM v1190_blackbox
                WHERE release_version=? AND scan_id=? AND stage='FINAL_DECISION'
                ORDER BY id
            """,(DECISION_RELEASE,scan_id)).fetchall()
            candidates=[]
            for row in rows:
                try:
                    payload=json.loads(row["payload_json"])
                    rank=int(((payload.get("blackbox_extra") or {}).get("selection_rank") or 0))
                    candidates.append((_signal_from_payload(payload),bool(row["selected"]),rank))
                except Exception:
                    continue
            if candidates:
                output.append((scan_id,candidates))
    return output


def replay(limit_scans:int=50,selector:Callable=selection_key):
    reports=[]
    for scan_id,candidates in candidate_sets(limit_scans):
        original=sorted(
            ((s,rank) for s,selected,rank in candidates if selected),
            key=lambda item:(item[1] if item[1]>0 else 9999),
        )
        ordered=sorted((s for s,_,_ in candidates),key=selector,reverse=True)
        original_top=(getattr(original[0][0],"symbol",None) if original else None)
        replay_top=(getattr(ordered[0],"symbol",None) if ordered else None)
        reports.append({
            "scan_id":scan_id,
            "candidates":len(candidates),
            "original_top":original_top,
            "replay_top":replay_top,
            "same_top":original_top==replay_top,
        })
    return reports
