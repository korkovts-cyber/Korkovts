Korkovts Signal AI V11.23.9

Fixes the live funnel fault where the bot showed e.g. 187 -> 36 -> 0 -> 0 Production.
The 36-name cheap OI/premium screen is ranking-only and must not be allowed to
kill the scan when its endpoint data is missing/partial. V11.23.9 fills missing
ranking rows from the already-computed technical soft score and sends the top
candidates to the mandatory full derivatives snapshot.

Safety is NOT bypassed: full-deep, ADL, Production, Alpha, execution, final risk
and live entry revalidation remain mandatory before delivery.
