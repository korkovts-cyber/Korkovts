"""Compatibility launcher for Railway and older installation commands.

The production implementation lives in :mod:`app.bot`. Keeping this tiny
launcher makes ``python bot.py`` safe while ``railway.toml`` pins the canonical
command to ``python -m app.bot``.
"""

from app.bot import main

if __name__ == "__main__":
    main()
