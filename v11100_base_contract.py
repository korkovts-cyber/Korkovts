"""V11.10 pinned base-package compatibility contract.

The V11.x release archives are overlays by design.  Silent execution on an
older ``app/`` package is more dangerous than a loud deployment failure, so
V11.10 fingerprints the exact core package it was audited against.

Hashes below are Git blob SHA-1 values from ``korkovts-cyber/Korkovts`` main at
the time this release was built.  They do not contain or expose secrets.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

EXPECTED_GIT_BLOBS={
    "app/__init__.py":"e2bd1550b11947a1bcbec6a139dc428f93a47d33",
    "app/bot.py":"f9291351f7a4e38d6bcf8306edb33de3af0d36e6",
    "app/config.py":"51ecbfc4a4f3cab68e15536acfd53ef403b22007",
    "app/db.py":"0b6f9a5367fe3cc7e5858299df4ae2eb4292f339",
    "app/indicators.py":"61c46e1a5c51bff6a3c7ce486275f0df6f183203",
    "app/liquidations.py":"77ad0775ee4292fb01ab31993b5db0005c70396d",
    "app/market.py":"10a6907584d5ca86ee54f71662ff186816037672",
    "app/news.py":"ad55c140ce59c7ae35ceeec28d849f8c21c3d9ba",
    "app/research.py":"0ff962f45e18f27971d686e41870ebbb477940d1",
    "app/risk.py":"1ab5856868e2ff7d0f042ed895dae52f900b2cb5",
    "app/scanner.py":"53d1f0414c780912968124209bb616dfb679f45f",
    "app/strategy.py":"2d4b9df5fcb46b60399af97ae7573984f35caf8e",
    "app/tracker.py":"61926c05a09e756d3b1366c752ae496ed24bdd2f",
}


def git_blob_sha(data:bytes)->str:
    header=f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header+data).hexdigest()


def audit(root:Path|str="."):
    root=Path(root)
    rows=[]
    for rel,expected in EXPECTED_GIT_BLOBS.items():
        path=root/rel
        if not path.exists():
            rows.append({"path":rel,"ok":False,"expected":expected,"actual":None,"reason":"missing"})
            continue
        actual=git_blob_sha(path.read_bytes())
        rows.append({
            "path":rel,"ok":actual==expected,"expected":expected,"actual":actual,
            "reason":"ok" if actual==expected else "base version mismatch",
        })
    return rows


def failures(root:Path|str="."):
    return [row for row in audit(root) if not row["ok"]]


def assert_compatible(root:Path|str="."):
    bad=failures(root)
    if bad:
        text="; ".join(f"{x['path']}: {x['reason']} ({x['actual']} != {x['expected']})" for x in bad)
        raise RuntimeError("V11.10 BASE CONTRACT FAILED: "+text)
    return True
