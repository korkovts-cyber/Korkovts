"""Korkovts Signal AI V11.23.5 · signal delivery + clear SPOT/FUTURES UI."""
import bot_v11234  # installs the complete V11.23.4 stack
import bot_v11191 as runtime
from v11235_signal_delivery_ui import install as i235

i235()

if __name__ == "__main__":
    runtime.base.core.main()
