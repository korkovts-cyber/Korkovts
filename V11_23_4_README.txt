V11.23.4 UNIFIED FINAL GATE

What changes:
- Legacy engine can discover candidates but cannot independently label them STRONG/PRIME.
- Every Futures candidate is re-evaluated by 5 independent families:
  TREND / MOMENTUM / FLOW / LOCATION / EXECUTION.
- PRIME = 5/5.
- STRONG ENTRY = at least 4/5 with TREND + LOCATION + EXECUTION mandatory.
- Good setup but bad location = WAIT ENTRY.
- Example: TAO at +2.14 ATR from EMA20 becomes WAIT, not STRONG ENTRY.
- User UI shows 4/5 or 5/5 and which family is missing, not opaque legacy 89/100.

Unchanged:
- derivatives freshness repair
- geometry repair
- ADL/news/spread/funding/crowding protections
- Spot engine
- modern menu
