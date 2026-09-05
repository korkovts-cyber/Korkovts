V11.23.5 SIGNAL DELIVERY + CLEAR SPOT/FUTURES UI

FIXED
- Every Futures card starts with: FUTURES SIGNAL.
- Every Spot card starts with: SPOT SIGNAL · БЕЗ ПЛЕЧА.
- Removed the misleading AUTO pattern "best candidate 100/100 -> do not enter".
- AUTO heartbeat is now only a status message; a real signal is a separate card.
- Repaired brittle V11.23.4 legacy-family reconstruction: TREND can be verified
  from normalized EMA/market fields instead of depending only on reason wording.
- Strong AUTO eligibility is restored for valid 4/5 candidates with safe
  LOCATION + EXECUTION and independent directional confirmation.
- WAIT remains WAIT when price is outside the entry location.
- A genuine ENTER_NOW state is never overwritten by the final gate.
- Scores are consistent with the 5-family gate; rejected watch candidates are
  no longer displayed as 100/100.

IMPORTANT
This patch increases signal reachability by fixing a gating bug; it does not
force fabricated entries or bypass execution/location risk checks.
