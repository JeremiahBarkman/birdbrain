"""Daily species aggregation (§14, §29 Phase 5).

service.py: aggregate_local_date() recomputes daily_species_summary
(§12.5) for one local calendar day from the detections table.
Idempotent and restart-safe (CLAUDE.md rules 9/10) — it's a full
recompute-and-upsert against detections (the authoritative record,
CLAUDE.md rule 21), not an incremental accumulator, so re-running it
for the same date any number of times converges on the same answer.

The slideshow builder itself (§29 Phase 5's renderer/manifest work)
doesn't exist yet — this package only produces the per-day species
summary it will read from.
"""
