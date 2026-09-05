Korkovts Signal AI V11.23.8

1) Removes repetitive "SPOT MANAGER · ... · RISK REVIEW" cards from Telegram.
   Actual Spot trade lifecycle messages (BUY/TP/SL/invalidation) are not filtered.
2) Increases Futures candidate reachability:
   - 5/5 remains PRIME;
   - 4/5 remains STRONG;
   - 3/5 is allowed only with TREND + LOCATION + EXECUTION all present.
   - 3/5 is ARMED, never fabricated as ENTER NOW; live micro confirmation remains required.
3) Keeps V11.23.7 button reliability patch.

This is a controlled threshold relaxation, not a guarantee that a live entry will exist on every scan.
