"""Korkovts Signal AI V11.21.8 · Futures reliability + Spot BUY-NOW persistence fix."""
from __future__ import annotations

import bot_v11191 as runtime
from v11217_reliability import install as install_v11217
from v11218_spot_entry_fix import install as install_v11218

install_v11217()
install_v11218()

if __name__ == "__main__":
    runtime.base.core.main()
