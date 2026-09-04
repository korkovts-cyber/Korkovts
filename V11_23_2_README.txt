V11.23.2 INTEGRATED FRESHNESS FIX

Observed live defect:
184 -> 36 -> 0 with top candidates rejected as:
"derivatives snapshot acquisition 15.xs is stale"

Root cause:
The V11.22.9 freshness repair was installed before V11.23.0 signal-core.
V11.23.0 later replaced the analyzer and could expose the inherited V11.18
download-duration gate again.

Fix:
V11.23.2 installs LAST and captures the active V11.23 signal analyzer at install
time. Complete quality derivatives snapshots are not rejected merely because
safe API pacing took 12-30 seconds.

Safety remains fail-closed:
- deep_data required
- quality >= 8
- ADL fresh
- max bounded acquisition: 22s for 15M, 30s for 1H
- all downstream strategy, execution, evidence and final-risk gates remain

Expected first validation:
The next Futures scan must NOT show 14-16s snapshot acquisition as the primary
final rejection reason. The funnel should progress beyond 36 -> 0 unless actual
strategy/risk conditions reject the candidates.
