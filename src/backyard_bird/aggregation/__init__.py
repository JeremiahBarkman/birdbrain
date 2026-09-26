"""Daily species aggregation (§14, §29 Phase 5).

service.py: aggregate_local_date() recomputes daily_species_summary
(§12.5) for one local calendar day from the detections table.
Idempotent and restart-safe (CLAUDE.md rules 9/10) — it's a full
recompute-and-upsert against detections (the authoritative record,
CLAUDE.md rule 21), not an incremental accumulator, so re-running it
for the same date any number of times converges on the same answer.

This package only produces the per-day species summary; the slideshow
builder that reads it lives in backyard_bird.slideshow (§29 Phase 5's
renderer/manifest work), which calls aggregate_local_date() directly
for the date it is building. Nothing schedules aggregation yet:
dates_due_for_aggregation() decides which dates are due (§14), but
nothing outside the tests calls it.
"""
