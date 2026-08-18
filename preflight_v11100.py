"""Pure-stdlib pre-import gate for Korkovts V11.10.

Runs before python-telegram-bot/pandas imports so a stale core repository fails
with an explicit compatibility message rather than an unrelated AttributeError.
"""
from pathlib import Path
import sys

from v11100_base_contract import failures

ROOT=Path(__file__).resolve().parent

required={
    "bot_v11100.py","release_check_v11100.py","test_v11100.py","test_v11100_core.py",
    "v11100_data.py","v11100_protections.py","v11100_edge.py","v11100_blackbox.py",
    "v11100_replay.py","v11100_stability.py","v11100_policy.py","v11100_base_contract.py",
    "railway.toml","requirements.txt",
}
missing=sorted(name for name in required if not (ROOT/name).exists())
if missing:
    raise SystemExit("V11.10 PREFLIGHT FAILED: missing overlay files: "+", ".join(missing))

bad=failures(ROOT)
if bad:
    lines=["V11.10 PREFLIGHT FAILED: repository app/ does not match the audited base."]
    for row in bad:
        lines.append(
            f"- {row['path']}: {row['reason']} | actual={row['actual']} expected={row['expected']}"
        )
    lines.append("Do not deploy by mixing V11.10 with an older app/ package.")
    raise SystemExit("\n".join(lines))

expected_requirements={
    "python-telegram-bot[job-queue]==22.8",
    "httpx==0.28.1",
    "pandas==2.3.3",
    "numpy==2.5.2",
    "python-dotenv==1.2.2",
    "feedparser==6.0.14",
    "websockets==15.0.1",
}
actual={line.strip() for line in (ROOT/"requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")}
if actual!=expected_requirements:
    raise SystemExit(
        "V11.10 PREFLIGHT FAILED: requirements.txt differs from the audited dependency lock.\n"
        f"actual={sorted(actual)}\nexpected={sorted(expected_requirements)}"
    )

print("V11.10 PREFLIGHT: OK")
print("base app fingerprints: OK")
print("dependency lock: OK")
