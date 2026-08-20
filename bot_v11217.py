"""Korkovts Signal AI V11.21.7 · reliability + Spot AUTO production entrypoint."""
from __future__ import annotations

import bot_v11191 as runtime
from v11217_reliability import install

install()

if __name__ == "__main__":
    runtime.base.core.main()
