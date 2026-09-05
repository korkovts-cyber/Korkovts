Korkovts Signal AI V11.23.7 — BUTTON RELIABILITY HOTFIX

Purpose:
- Fix Telegram inline buttons that appear to do nothing after V11.23.6.
- Preserve all V11.23.6 signal activation changes.
- Do NOT alter trading thresholds or risk logic.

What changed:
1) Immediate Telegram callback ACK for primary buttons.
2) Direct live dispatch for scan, short scan, news, movers, prices,
   AUTO on/off, history, memory, system, lab and clear-chat.
3) YK CONTROL CENTER recovery button is handled directly.
4) Legacy/namespaced panels still use the complete existing callback router.
5) Button failures now return an error card instead of failing silently.

Production entrypoint: bot_v11237.py
