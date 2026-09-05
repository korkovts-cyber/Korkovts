"""Korkovts Signal AI V11.23.7 · Telegram button reliability hotfix."""
import bot_v11236  # installs complete V11.23.6 signal stack
import bot_v11191 as runtime
from v11237_button_reliability import install as i237

i237()

if __name__ == "__main__":
    runtime.base.core.main()
