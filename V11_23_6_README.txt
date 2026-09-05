KORKOVTS SIGNAL AI V11.23.6 · SIGNAL ACTIVATION

WHAT WAS WRONG
- The independent-family engine still inherited a narrow set of micro-thresholds,
  so many otherwise coherent 4/5 setups were rejected.
- The final gate could be applied later in the presentation pipeline instead of
  being guaranteed on every scanner output consumed by AUTO/manual signal paths.
- AUTO could keep repeating no-entry text while the user could not clearly see
  whether a real actionable signal existed.

WHAT CHANGED
- Futures family profile widened without removing hard safety:
  * data quality >= 8/9 remains mandatory
  * ADL fresh + low/medium remains mandatory
  * deep derivatives remain mandatory
  * Trend + Execution remain mandatory
  * >= 4/5 independent families remain mandatory
  * spread cap 5 -> 7 bps
  * basis cap 20 -> 30 bps
  * flow taker 1.02/0.98 -> 1.01/0.99
  * OI tolerance -0.25% -> -0.50%
  * location band 1.55 ATR -> 1.85 ATR with 0.20 ATR VWAP tolerance
  * directional family gap 10 -> 6
  * BTC regime is a score penalty, no longer a whole-coin hard veto.
- Every Futures scan result is final-gated before AUTO/manual consumers receive it.
- All scan aliases are rebound to the same V11.23.6 path.
- SPOT and FUTURES cards remain explicitly separated.
- No-signal heartbeat is compact and no longer pretends that a watch score is a signal.

IMPORTANT
The bot still cannot truthfully guarantee a trade on every scan. V11.23.6 is
intended to remove accidental starvation while preserving hard market-data and
risk protections.
