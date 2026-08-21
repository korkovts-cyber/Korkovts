"""V11.22.2 · Verification hardening.

This layer does not change signal thresholds. It fixes two verification/UI
problems found during a second audit of V11.22.1:

- heartbeat newline normalization matched two backslashes instead of the actual
  literal "\n" sequence shown in Telegram;
- "API final path: BOUND" was printed unconditionally. V11.22.2 reports the
  actual runtime identity binding and fails startup if app.market._get is not
  the final governed request path after installation.
"""
from __future__ import annotations

import bot_v11191 as runtime
import v1141_governor as governor
import v11221_production_reachability as p221

base = runtime.base
VERSION = "11.22.2"


def _binding_ok():
    return governor.market._get is p221.governed_get_v11221


# Patch V11.22.1 heartbeat result rather than duplicating its trading logic.
_old_hb = base.heartbeat_text
def heartbeat_text_v11222(diagnostics, **kwargs):
    text = _old_hb(diagnostics, **kwargs)
    # Actual Telegram artifact is one backslash + n.
    text = text.replace("\\n", "\n")
    state = "BOUND" if _binding_ok() else "ERROR"
    # Replace the prior optimistic label if present.
    text = text.replace("API final path: <b>BOUND</b>", f"API final path: <b>{state}</b>")
    return text
base.heartbeat_text = heartbeat_text_v11222

_old_health = base.health_text
def health_text_v11222(h):
    text = _old_health(h)
    for old in ("V11.22.1","V11.22.0","V11.21.9","V11.21.8","V11.21.7","V11.21.6"):
        text = text.replace(old, VERSION)
    state = "ACTIVE" if _binding_ok() else "ERROR"
    text = text.replace("Final API binding: <b>ACTIVE</b>", f"Final API binding: <b>{state}</b>")
    return text
base.health_text = health_text_v11222


def install():
    # Rebind once more at the last possible overlay stage.
    p221._rebind_request_aliases()
    if not _binding_ok():
        raise RuntimeError("V11.22.2 startup blocked: final Binance governor is not bound to app.market._get")
    base.APP_VERSION = VERSION
    base.config.APP_VERSION = VERSION
    base.core.APP_VERSION = VERSION
    return True
