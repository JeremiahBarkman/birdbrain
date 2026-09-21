"""The daily aggregation job (§14): recomputes daily_species_summary
from detections for one local calendar day at a time.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backyard_bird.database.repositories import (
    delete_daily_species_summary_species_not_in,
    list_daily_detection_aggregates,
    upsert_daily_species_summary,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AggregationResult:
    local_date: str
    species_count: int


def local_day_range_utc(local_date: str, tz_name: str) -> tuple[datetime, datetime]:
    """Local midnight-to-midnight UTC range for one YYYY-MM-DD date in
    tz_name — same convention as cli.py's `detections list --date` and
    web/routes.py's `_day_range_utc`, kept as its own small copy here
    rather than shared across those unrelated call sites.
    """
    tz = ZoneInfo(tz_name)
    day_start_local = datetime.strptime(local_date, "%Y-%m-%d").replace(tzinfo=tz)
    day_end_local = day_start_local + timedelta(days=1)
    return day_start_local.astimezone(timezone.utc), day_end_local.astimezone(timezone.utc)


def today_local_date(tz_name: str, now_utc: datetime | None = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def aggregate_local_date(conn: sqlite3.Connection, local_date: str, tz_name: str) -> AggregationResult:
    """Recomputes daily_species_summary (§12.5) for one local calendar
    day from scratch.

    Idempotent and restart-safe (CLAUDE.md rules 9/10): this is a pure
    recompute from detections — the authoritative record (CLAUDE.md
    rule 21) — not an incremental accumulator, so calling it any number
    of times for the same date, from any process, converges on the
    same answer. The prune-then-upsert happens in one transaction, so
    a crash mid-run leaves either the old summary or the new one, never
    a partial mix.
    """
    start_utc, end_utc = local_day_range_utc(local_date, tz_name)
    aggregates = list_daily_detection_aggregates(conn, start_utc, end_utc)

    with conn:
        delete_daily_species_summary_species_not_in(conn, local_date, [a.species_id for a in aggregates])
        for aggregate in aggregates:
            upsert_daily_species_summary(
                conn,
                local_date,
                aggregate.species_id,
                aggregate.first_detected_at_utc,
                aggregate.last_detected_at_utc,
                aggregate.detection_count,
                aggregate.highest_confidence,
                aggregate.representative_detection_id,
            )

    logger.info(
        "daily_aggregation_completed",
        extra={
            "event": "daily_aggregation_completed",
            "local_date": local_date,
            "species_count": len(aggregates),
        },
    )
    return AggregationResult(local_date=local_date, species_count=len(aggregates))


def dates_due_for_aggregation(tz_name: str, now_utc: datetime | None = None) -> list[str]:
    """§14's recommended schedule, expressed as "which local date(s)
    need a pass right now": always today, so a periodic caller (e.g.
    every 15 minutes) keeps the current day's summary fresh, plus
    yesterday during the first 20 minutes after local midnight — wide
    enough to cover a 15-minute poll interval landing a few minutes
    late — so a detection recorded in the last moments before the day
    rolled over still gets folded into yesterday's now-final summary
    (the "finalize the previous day's summary" half of §14's
    schedule). Pure decision function — no I/O, no sleeping; a caller
    owns the actual loop, same split as images/service.py's
    run_watch_loop versus the CLI command that drives it.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(tz_name))
    dates = [now_local.strftime("%Y-%m-%d")]
    if now_local.hour == 0 and now_local.minute < 20:
        dates.append((now_local - timedelta(days=1)).strftime("%Y-%m-%d"))
    return dates
