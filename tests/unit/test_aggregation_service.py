import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backyard_bird.aggregation.service import (
    aggregate_local_date,
    dates_due_for_aggregation,
    local_day_range_utc,
    today_local_date,
)
from backyard_bird.database.migrations import apply_migrations
from backyard_bird.database.repositories import (
    get_daily_species_summary,
    get_or_create_species,
    insert_audio_segment,
    insert_detection,
    reject_species_detections,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "test.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection, MIGRATIONS_DIR)
    yield connection
    connection.close()


def _insert_segment(conn: sqlite3.Connection, path: str = "incoming/a.wav") -> int:
    started = datetime(2026, 8, 17, 6, 0, 0, tzinfo=timezone.utc)
    with conn:
        return insert_audio_segment(
            conn,
            microphone_id="mic-01",
            file_path=path,
            recording_started_at_utc=started,
            recording_ended_at_utc=started,
            duration_seconds=3.0,
            sample_rate=48000,
            channels=1,
            file_size_bytes=1234,
            sha256="deadbeef",
        )


def _insert_detection(
    conn: sqlite3.Connection, species_id: int, detected_at_utc: datetime, confidence: float, segment_path: str
) -> int:
    with conn:
        return insert_detection(
            conn, _insert_segment(conn, segment_path), species_id, detected_at_utc,
            0.0, 3.0, confidence, 1.0, None, None, False, None,
        )


def test_aggregate_local_date_summarizes_only_that_local_day(conn: sqlite3.Connection) -> None:
    # "America/Los_Angeles" is UTC-7 in August (PDT), so 2026-08-17
    # local runs 07:00 UTC 2026-08-17 through 07:00 UTC 2026-08-18.
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    in_range = _insert_detection(
        conn, species_id, datetime(2026, 8, 17, 8, 0, 0, tzinfo=timezone.utc), 0.75, "incoming/a.wav"
    )
    _insert_detection(  # previous local day (before 07:00 UTC on the 17th)
        conn, species_id, datetime(2026, 8, 17, 6, 0, 0, tzinfo=timezone.utc), 0.99, "incoming/b.wav"
    )
    _insert_detection(  # next local day (at/after 07:00 UTC on the 18th)
        conn, species_id, datetime(2026, 8, 18, 7, 0, 0, tzinfo=timezone.utc), 0.99, "incoming/c.wav"
    )

    result = aggregate_local_date(conn, "2026-08-17", "America/Los_Angeles")

    assert result.local_date == "2026-08-17"
    assert result.species_count == 1
    rows = get_daily_species_summary(conn, "2026-08-17")
    assert len(rows) == 1
    assert rows[0]["detection_count"] == 1
    assert rows[0]["representative_detection_id"] == in_range


def test_aggregate_local_date_is_idempotent(conn: sqlite3.Connection) -> None:
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    _insert_detection(conn, species_id, datetime(2026, 8, 17, 8, 0, 0, tzinfo=timezone.utc), 0.75, "incoming/a.wav")

    first = aggregate_local_date(conn, "2026-08-17", "UTC")
    second = aggregate_local_date(conn, "2026-08-17", "UTC")

    assert first == second
    assert len(get_daily_species_summary(conn, "2026-08-17")) == 1


def test_aggregate_local_date_drops_species_whose_only_detection_is_later_rejected(
    conn: sqlite3.Connection,
) -> None:
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    _insert_detection(conn, species_id, datetime(2026, 8, 17, 8, 0, 0, tzinfo=timezone.utc), 0.75, "incoming/a.wav")

    aggregate_local_date(conn, "2026-08-17", "UTC")
    assert len(get_daily_species_summary(conn, "2026-08-17")) == 1

    with conn:
        reject_species_detections(conn, species_id)
    aggregate_local_date(conn, "2026-08-17", "UTC")

    assert get_daily_species_summary(conn, "2026-08-17") == []


def test_local_day_range_utc_handles_a_non_utc_timezone() -> None:
    start, end = local_day_range_utc("2026-08-17", "America/Los_Angeles")
    assert start == datetime(2026, 8, 17, 7, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 18, 7, 0, 0, tzinfo=timezone.utc)


def test_today_local_date_uses_the_configured_timezone() -> None:
    # 2026-08-17T01:00:00Z is still 2026-08-16 evening in Los Angeles.
    now_utc = datetime(2026, 8, 17, 1, 0, 0, tzinfo=timezone.utc)
    assert today_local_date("America/Los_Angeles", now_utc) == "2026-08-16"
    assert today_local_date("UTC", now_utc) == "2026-08-17"


def test_dates_due_for_aggregation_includes_only_today_outside_the_midnight_window() -> None:
    now_utc = datetime(2026, 8, 17, 15, 0, 0, tzinfo=timezone.utc)  # mid-afternoon UTC
    assert dates_due_for_aggregation("UTC", now_utc) == ["2026-08-17"]


def test_dates_due_for_aggregation_includes_yesterday_just_after_local_midnight() -> None:
    now_utc = datetime(2026, 8, 17, 0, 10, 0, tzinfo=timezone.utc)
    assert dates_due_for_aggregation("UTC", now_utc) == ["2026-08-17", "2026-08-16"]


def test_dates_due_for_aggregation_stops_including_yesterday_after_the_window() -> None:
    now_utc = datetime(2026, 8, 17, 0, 25, 0, tzinfo=timezone.utc)
    assert dates_due_for_aggregation("UTC", now_utc) == ["2026-08-17"]
