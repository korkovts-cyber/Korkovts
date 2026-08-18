"""V11.10 append-only decision black box.

The black box records why an already-generated candidate was accepted or
removed at each Production layer.  It is intentionally local SQLite data: no
exchange keys, Telegram token or environment secrets are copied into payloads.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.config import DATABASE_PATH
from v1171_sqlite import db_session
from v11100_policy import contract as decision_policy_contract

DECISION_RELEASE="11.10.0-competitive-edge"


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def new_scan_id(kind:str="scan"):
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{str(kind).lower()}-{uuid.uuid4().hex[:8]}"


def init():
    with db_session(DATABASE_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS v1190_blackbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                release_version TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                stage TEXT NOT NULL,
                symbol TEXT,
                timeframe TEXT,
                side TEXT,
                selected INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                pro_rank REAL,
                decision_priority REAL,
                expected_net_r REAL,
                expected_net_r_lcb REAL,
                sample_n INTEGER,
                payload_json TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_v1190_scan ON v1190_blackbox(scan_id,id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_v1190_symbol ON v1190_blackbox(symbol,created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_v1190_stage ON v1190_blackbox(stage,created_at)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS v11100_policy_contract(
                fingerprint TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                schema TEXT NOT NULL,
                policy_json TEXT NOT NULL
            )
        """)
        contract=decision_policy_contract()
        c.execute(
            "INSERT OR IGNORE INTO v11100_policy_contract(fingerprint,created_at,schema,policy_json) VALUES(?,?,?,?)",
            (str(contract["fingerprint"]),_utcnow(),str(contract["schema"]),
             json.dumps(_safe(contract.get("policy") or {}),ensure_ascii=False,sort_keys=True,separators=(",",":"))),
        )


def _safe(value:Any,depth:int=0):
    if depth>12:
        return "<max-depth>"
    if value is None or isinstance(value,(str,bool,int)):
        return value
    if isinstance(value,float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value,Decimal):
        return float(value)
    if isinstance(value,(datetime,)):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return _safe(dataclasses.asdict(value),depth+1)
    if isinstance(value,dict):
        result={}
        for k,v in value.items():
            key=str(k)
            # Never persist obvious secret-bearing keys if a caller accidentally
            # places environment/config material inside a snapshot.
            lowered=key.lower()
            if any(x in lowered for x in ("token","secret","password","api_key","apikey")):
                result[key]="<redacted>"
            else:
                result[key]=_safe(v,depth+1)
        return result
    if isinstance(value,(list,tuple,set)):
        return [_safe(v,depth+1) for v in value]
    # pandas/numpy scalars generally expose item().
    try:
        item=value.item()
        if item is not value:
            return _safe(item,depth+1)
    except Exception:
        pass
    return str(value)


def signal_payload(signal:Any,extra:dict|None=None):
    fields=(
        "symbol","timeframe","side","setup_type","score","professional_rank",
        "decision_priority","expected_net_r","expected_net_r_lcb","edge_sample_n",
        "entry_low","entry_high","stop","tp1","tp2","tp3","rr",
        "estimated_cost_r","cluster_id","cluster_rank","cluster_correlation",
        "production_regime","professional_grade","data_quality","data_quality_total",
    )
    payload={name:_safe(getattr(signal,name,None)) for name in fields}
    payload["market_context"]=_safe(getattr(signal,"market_context",{}) or {})
    payload["feature_snapshot"]=_safe(getattr(signal,"feature_snapshot",{}) or {})
    contract=decision_policy_contract()
    payload["decision_contract"]={
        "schema":str(contract.get("schema") or ""),
        "fingerprint":str(contract.get("fingerprint") or ""),
    }
    payload["reasons"]=_safe(getattr(signal,"reasons",[]) or [])
    if extra:
        payload["blackbox_extra"]=_safe(extra)
    return payload


def _fingerprint(scan_id,stage,signal,reason,selected,payload_json):
    raw="|".join([
        DECISION_RELEASE,str(scan_id),str(stage),
        str(getattr(signal,"symbol","") or ""),
        str(getattr(signal,"timeframe","") or ""),
        str(getattr(signal,"side","") or ""),
        "1" if selected else "0",str(reason or ""),payload_json,
    ])
    return hashlib.sha256(raw.encode("utf-8","replace")).hexdigest()


def _row_values(signal:Any,stage:str,reason:str,selected:bool,scan_id:str,pipeline:str,extra):
    payload=signal_payload(signal,extra)
    payload_json=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    fp=_fingerprint(scan_id,stage,signal,reason,selected,payload_json)
    return (
        _utcnow(),DECISION_RELEASE,scan_id,str(pipeline),str(stage),
        str(getattr(signal,"symbol","") or ""),
        str(getattr(signal,"timeframe","") or ""),
        str(getattr(signal,"side","") or ""),1 if selected else 0,str(reason or ""),
        float(getattr(signal,"professional_rank",0) or 0),
        float(getattr(signal,"decision_priority",getattr(signal,"professional_rank",0)) or 0),
        (None if getattr(signal,"expected_net_r",None) is None else float(signal.expected_net_r)),
        (None if getattr(signal,"expected_net_r_lcb",None) is None else float(signal.expected_net_r_lcb)),
        int(getattr(signal,"edge_sample_n",0) or 0),payload_json,fp,
    )


_INSERT_SQL="""
    INSERT OR IGNORE INTO v1190_blackbox(
        created_at,release_version,scan_id,pipeline,stage,symbol,timeframe,side,
        selected,reason,pro_rank,decision_priority,expected_net_r,
        expected_net_r_lcb,sample_n,payload_json,fingerprint
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def record(signal:Any,stage:str,reason:str="",selected:bool=False,
           scan_id:str|None=None,pipeline:str="futures",extra:dict|None=None):
    try:
        init()
        scan_id=str(scan_id or (getattr(signal,"feature_snapshot",{}) or {}).get("v11100_scan_id") or new_scan_id(pipeline))
        values=_row_values(signal,stage,reason,selected,scan_id,pipeline,extra)
        with db_session(DATABASE_PATH) as c:
            c.execute(_INSERT_SQL,values)
        return values[-1]
    except Exception:
        # Diagnostics must never be able to manufacture or block a trade.
        return None


def record_many(rows,stage:str,reason:str="",selected_ids:set[int]|None=None,
                scan_id:str|None=None,pipeline:str="futures",extra:dict|None=None,
                selected_order:list[int]|None=None):
    signals=list(rows or [])
    if not signals:
        return 0
    selected_ids=selected_ids or set()
    rank_map={int(ident):rank for rank,ident in enumerate(selected_order or [],1)}
    try:
        init()
        sid=str(scan_id or new_scan_id(pipeline))
        values=[]
        for signal in signals:
            row_extra=dict(extra or {})
            if id(signal) in rank_map:
                row_extra["selection_rank"]=rank_map[id(signal)]
            values.append(_row_values(
                signal,stage,reason,id(signal) in selected_ids,sid,pipeline,row_extra
            ))
        with db_session(DATABASE_PATH) as c:
            c.executemany(_INSERT_SQL,values)
        return len(values)
    except Exception:
        return 0

def recent(limit:int=100):
    init()
    with db_session(DATABASE_PATH,row_factory=sqlite3.Row) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM v1190_blackbox ORDER BY id DESC LIMIT ?",(int(limit),)
        ).fetchall()]


def stats():
    init()
    with db_session(DATABASE_PATH,row_factory=sqlite3.Row) as c:
        rows=c.execute("""
            SELECT stage,COUNT(*) n,SUM(selected) selected
            FROM v1190_blackbox
            WHERE release_version=?
            GROUP BY stage ORDER BY n DESC,stage
        """,(DECISION_RELEASE,)).fetchall()
    return [dict(r) for r in rows]
