V11.23.3 GEOMETRY REPAIR

Live observation:
183 -> 36 -> 1 -> 0 Production
Closest high-score candidates were rejected for entry/stop geometry.

Root issue:
Legacy LONG geometry uses min(ATR stop, structural stop) and SHORT uses max(...),
which deliberately chooses the farther stop. The same strategy then rejects risk
above ~2 ATR. This creates an internal contradiction.

V11.23.3:
- installed last after data + signal core
- risk floor 0.90 ATR
- target risk 1.35 ATR
- max risk 1.85 ATR
- uses nearest valid structural/ATR stop
- never widens risk
- rebuilds TP1/TP2/TP3 from corrected risk
- impossible geometry remains NO TRADE
- all Production, Alpha, execution, ADL, news and derivatives gates remain
