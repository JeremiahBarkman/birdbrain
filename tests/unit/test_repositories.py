import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backyard_bird.database.migrations import apply_migrations
from backyard_bird.database.repositories import (
    delete_species_detections,
    find_audio_segment_by_file_path,
    get_best_recording_confidence,
    get_best_recordings_by_scientific_name,
    get_or_create_species,
    get_recent_detections_for_species,
    get_species_id_by_scientific_name,
    insert_audio_segment,
    insert_bird_image,
    insert_detection,
    list_detections,
    list_species_needing_image_search,
    list_species_summary,
    mark_audio_segment_completed,
    mark_audio_segment_failed,
    reject_species_detections,
    reset_audio_segment_to_pending,
    set_best_recording_approved,
    set_best_recording_highpass,
    upsert_best_recording,
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
    started = datetime(2026, 8, 1, 6, 0, 0, tzinfo=timezone.utc)
    with conn:
        return insert_audio_segment(
            conn,
            microphone_id="mic-01",
            file_path=path,
            recording_started_at_utc=started,
            recording_ended_at_utc=started + timedelta(seconds=30),
            duration_seconds=30.0,
            sample_rate=48000,
            channels=1,
            file_size_bytes=1234,
            sha256="deadbeef",
        )


def test_get_or_create_species_is_idempotent_and_updates_common_name(conn: sqlite3.Connection) -> None:
    id1 = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    id2 = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    assert id1 == id2

    id3 = get_or_create_species(conn, "Poecile atricapillus", "Black-Capped Chickadee")
    assert id3 == id1
    row = conn.execute("SELECT common_name FROM species WHERE id = ?", (id1,)).fetchone()
    assert row["common_name"] == "Black-Capped Chickadee"


def test_audio_segment_lifecycle(conn: sqlite3.Connection) -> None:
    segment_id = _insert_segment(conn)
    row = find_audio_segment_by_file_path(conn, "incoming/a.wav")
    assert row["id"] == segment_id
    assert row["processing_status"] == "processing"

    with conn:
        mark_audio_segment_completed(conn, segment_id, "2.4")
    assert find_audio_segment_by_file_path(conn, "incoming/a.wav")["processing_status"] == "completed"

    with conn:
        mark_audio_segment_failed(conn, segment_id, "boom")
    row = find_audio_segment_by_file_path(conn, "incoming/a.wav")
    assert row["processing_status"] == "failed"
    assert row["error_message"] == "boom"

    with conn:
        reset_audio_segment_to_pending(conn, segment_id)
    assert find_audio_segment_by_file_path(conn, "incoming/a.wav")["processing_status"] == "pending"


def test_insert_detection_and_list_detections(conn: sqlite3.Connection) -> None:
    segment_id = _insert_segment(conn)
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    detected_at = datetime(2026, 8, 1, 6, 0, 5, tzinfo=timezone.utc)

    with conn:
        insert_detection(
            conn, segment_id, species_id, detected_at, 5.0, 8.0, 0.81, 1.0, 45.28, -123.19, False, None
        )

    rows = list_detections(conn, limit=10)
    assert len(rows) == 1
    assert rows[0].common_name == "Black-capped Chickadee"
    assert rows[0].confidence == 0.81
    assert rows[0].is_duplicate is False


def test_list_detections_excludes_duplicates_by_default(conn: sqlite3.Connection) -> None:
    segment_id = _insert_segment(conn)
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    detected_at = datetime(2026, 8, 1, 6, 0, 5, tzinfo=timezone.utc)

    with conn:
        original_id = insert_detection(
            conn, segment_id, species_id, detected_at, 5.0, 8.0, 0.81, 1.0, None, None, False, None
        )
        insert_detection(
            conn, segment_id, species_id, detected_at, 5.0, 8.0, 0.75, 1.0, None, None, True, original_id
        )

    assert len(list_detections(conn)) == 1
    assert len(list_detections(conn, include_duplicates=True)) == 2


def test_get_recent_detections_for_species_filters_by_mic_and_time(conn: sqlite3.Connection) -> None:
    segment_id = _insert_segment(conn, path="incoming/a.wav")
    other_segment_id = _insert_segment(conn, path="incoming/b.wav")
    with conn:
        conn.execute("UPDATE audio_segments SET microphone_id = 'mic-02' WHERE id = ?", (other_segment_id,))
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    detected_at = datetime(2026, 8, 1, 6, 0, 5, tzinfo=timezone.utc)

    with conn:
        insert_detection(
            conn, segment_id, species_id, detected_at, 5.0, 8.0, 0.81, 1.0, None, None, False, None
        )
        insert_detection(
            conn, other_segment_id, species_id, detected_at, 5.0, 8.0, 0.81, 1.0, None, None, False, None
        )

    recent = get_recent_detections_for_species(
        conn, species_id, "mic-01", since_utc=detected_at - timedelta(seconds=10)
    )
    assert len(recent) == 1


def test_list_species_summary(conn: sqlite3.Connection) -> None:
    segment_id = _insert_segment(conn)
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    with conn:
        insert_detection(
            conn,
            segment_id,
            species_id,
            datetime(2026, 8, 1, 6, 0, 5, tzinfo=timezone.utc),
            5.0,
            8.0,
            0.81,
            1.0,
            None,
            None,
            False,
            None,
        )

    summary = list_species_summary(conn)
    assert len(summary) == 1
    assert summary[0].common_name == "Black-capped Chickadee"
    assert summary[0].detection_count == 1
    assert summary[0].highest_confidence == 0.81


def test_list_species_summary_filters_by_confidence_and_date(conn: sqlite3.Connection) -> None:
    segment_id = _insert_segment(conn)
    chickadee_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    finch_id = get_or_create_species(conn, "Haemorhous mexicanus", "House Finch")
    with conn:
        # Chickadee: one low-confidence detection yesterday, one high-confidence today.
        insert_detection(
            conn,
            segment_id,
            chickadee_id,
            datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc),
            5.0,
            8.0,
            0.50,
            1.0,
            None,
            None,
            False,
            None,
        )
        insert_detection(
            conn,
            segment_id,
            chickadee_id,
            datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc),
            5.0,
            8.0,
            0.90,
            1.0,
            None,
            None,
            False,
            None,
        )
        # A second, lower-confidence chickadee detection today too — so
        # the "today" grouping below has two surviving detections, not
        # one, and can actually distinguish "highest" from "average"
        # rather than the two being coincidentally identical.
        insert_detection(
            conn,
            segment_id,
            chickadee_id,
            datetime(2026, 8, 18, 9, 30, 0, tzinfo=timezone.utc),
            5.0,
            8.0,
            0.60,
            1.0,
            None,
            None,
            False,
            None,
        )
        # Finch: only a low-confidence detection today.
        insert_detection(
            conn,
            segment_id,
            finch_id,
            datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc),
            5.0,
            8.0,
            0.55,
            1.0,
            None,
            None,
            False,
            None,
        )

    # min_confidence excludes species whose every detection falls below it.
    high_confidence_only = list_species_summary(conn, min_confidence=0.75)
    assert [r.common_name for r in high_confidence_only] == ["Black-capped Chickadee"]
    assert high_confidence_only[0].detection_count == 1  # only the 0.90 detection counted
    assert high_confidence_only[0].highest_confidence == 0.90

    # A date range narrows both which species appear and their aggregates.
    today_only = list_species_summary(
        conn,
        since_utc=datetime(2026, 8, 18, 0, 0, 0, tzinfo=timezone.utc),
        until_utc=datetime(2026, 8, 19, 0, 0, 0, tzinfo=timezone.utc),
    )
    assert {r.common_name for r in today_only} == {"Black-capped Chickadee", "House Finch"}
    chickadee_today = next(r for r in today_only if r.common_name == "Black-capped Chickadee")
    # Two detections survive today (0.90 and 0.60, yesterday's 0.50 is
    # excluded) — highest_confidence must reflect the higher of the
    # two (0.90), not their average (0.75). This is the exact
    # real-world discrepancy a user reported live: a table row showing
    # a lower number than the species' just-arrived detection.
    assert chickadee_today.detection_count == 2
    assert chickadee_today.highest_confidence == 0.90


def _backdate_last_verified(conn: sqlite3.Connection, species_id: int, days_ago: int) -> None:
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "UPDATE bird_images SET last_verified_at_utc = ?, downloaded_at_utc = ? WHERE species_id = ?",
        (when, when, species_id),
    )


def test_species_with_no_image_row_needs_search(conn: sqlite3.Connection) -> None:
    species_id = get_or_create_species(conn, "Megascops kennicottii", "Western Screech-Owl")
    needing = {r["id"] for r in list_species_needing_image_search(conn, refresh_after_days=90)}
    assert species_id in needing


def test_species_with_fresh_approved_image_does_not_need_search(conn: sqlite3.Connection) -> None:
    species_id = get_or_create_species(conn, "Megascops kennicottii", "Western Screech-Owl")
    with conn:
        insert_bird_image(conn, species_id, "wikimedia_commons", "https://example.com/owl.jpg", "approved")

    needing = {r["id"] for r in list_species_needing_image_search(conn, refresh_after_days=90)}
    assert species_id not in needing


def test_species_with_recent_unavailable_verdict_does_not_need_search(conn: sqlite3.Connection) -> None:
    species_id = get_or_create_species(conn, "Megascops kennicottii", "Western Screech-Owl")
    with conn:
        insert_bird_image(conn, species_id, "none", "", "unavailable")

    needing = {r["id"] for r in list_species_needing_image_search(conn, refresh_after_days=90)}
    assert species_id not in needing


def test_stale_unavailable_verdict_becomes_eligible_again(conn: sqlite3.Connection) -> None:
    # An 'unavailable' recorded 100 days ago (e.g. during a provider
    # outage that has since been fixed, like the real Wikimedia
    # throttle this project hit) shouldn't stay stuck forever.
    species_id = get_or_create_species(conn, "Megascops kennicottii", "Western Screech-Owl")
    with conn:
        insert_bird_image(conn, species_id, "none", "", "unavailable")
        _backdate_last_verified(conn, species_id, days_ago=100)

    needing = {r["id"] for r in list_species_needing_image_search(conn, refresh_after_days=90)}
    assert species_id in needing


def test_stale_approved_image_becomes_eligible_for_rotation(conn: sqlite3.Connection) -> None:
    species_id = get_or_create_species(conn, "Megascops kennicottii", "Western Screech-Owl")
    with conn:
        insert_bird_image(conn, species_id, "wikimedia_commons", "https://example.com/owl.jpg", "approved")
        _backdate_last_verified(conn, species_id, days_ago=100)

    needing = {r["id"] for r in list_species_needing_image_search(conn, refresh_after_days=90)}
    assert species_id in needing


# -- best_recordings -----------------------------------------------------------


def _insert_species_and_detection(
    conn: sqlite3.Connection, confidence: float, segment_path: str = "incoming/a.wav"
) -> tuple[int, int]:
    segment_id = _insert_segment(conn, segment_path)
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    with conn:
        detection_id = insert_detection(
            conn,
            segment_id,
            species_id,
            datetime(2026, 8, 17, 6, 0, 0, tzinfo=timezone.utc),
            5.0,
            8.0,
            confidence,
            1.0,
            None,
            None,
            False,
            None,
        )
    return species_id, detection_id


def test_upsert_best_recording_first_time_always_wins(conn: sqlite3.Connection) -> None:
    species_id, detection_id = _insert_species_and_detection(conn, 0.60)
    assert get_best_recording_confidence(conn, species_id) is None

    with conn:
        upsert_best_recording(conn, species_id, detection_id, 0.60, "clip.wav")

    assert get_best_recording_confidence(conn, species_id) == 0.60
    row = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert row["detection_id"] == detection_id
    assert row["clip_path"] == "clip.wav"
    assert row["is_approved"] == 0


def test_upsert_best_recording_replaces_on_higher_confidence(conn: sqlite3.Connection) -> None:
    species_id, first_detection_id = _insert_species_and_detection(conn, 0.60, "incoming/a.wav")
    with conn:
        upsert_best_recording(conn, species_id, first_detection_id, 0.60, "clip.wav")

    _, second_detection_id = _insert_species_and_detection(conn, 0.90, "incoming/b.wav")
    with conn:
        upsert_best_recording(conn, species_id, second_detection_id, 0.90, "clip.wav")

    row = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert row["confidence"] == 0.90
    assert row["detection_id"] == second_detection_id


def test_upsert_best_recording_resets_approval_on_replacement(conn: sqlite3.Connection) -> None:
    species_id, first_detection_id = _insert_species_and_detection(conn, 0.60, "incoming/a.wav")
    with conn:
        upsert_best_recording(conn, species_id, first_detection_id, 0.60, "clip.wav")
        assert set_best_recording_approved(conn, species_id, True)
    assert get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["is_approved"] == 1

    _, second_detection_id = _insert_species_and_detection(conn, 0.90, "incoming/b.wav")
    with conn:
        upsert_best_recording(conn, species_id, second_detection_id, 0.90, "clip.wav")

    row = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert row["is_approved"] == 0
    assert row["approved_at_utc"] is None


def test_set_best_recording_approved_returns_false_when_nothing_to_approve(conn: sqlite3.Connection) -> None:
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    with conn:
        assert set_best_recording_approved(conn, species_id, True) is False


def test_set_best_recording_approved_can_unstar(conn: sqlite3.Connection) -> None:
    species_id, detection_id = _insert_species_and_detection(conn, 0.60)
    with conn:
        upsert_best_recording(conn, species_id, detection_id, 0.60, "clip.wav")
        set_best_recording_approved(conn, species_id, True)
        set_best_recording_approved(conn, species_id, False)

    row = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert row["is_approved"] == 0
    assert row["approved_at_utc"] is None


def test_upsert_best_recording_defaults_highpass_to_off(conn: sqlite3.Connection) -> None:
    species_id, detection_id = _insert_species_and_detection(conn, 0.60)
    with conn:
        upsert_best_recording(conn, species_id, detection_id, 0.60, "clip.wav")

    assert get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["highpass_hz"] == 0


def test_set_best_recording_highpass_persists(conn: sqlite3.Connection) -> None:
    species_id, detection_id = _insert_species_and_detection(conn, 0.60)
    with conn:
        upsert_best_recording(conn, species_id, detection_id, 0.60, "clip.wav")
        assert set_best_recording_highpass(conn, species_id, 1000.0) is True

    assert get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["highpass_hz"] == 1000.0


def test_set_best_recording_highpass_returns_false_when_nothing_to_set_it_on(
    conn: sqlite3.Connection,
) -> None:
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    with conn:
        assert set_best_recording_highpass(conn, species_id, 500.0) is False


def test_upsert_best_recording_resets_highpass_on_replacement(conn: sqlite3.Connection) -> None:
    species_id, first_detection_id = _insert_species_and_detection(conn, 0.60, "incoming/a.wav")
    with conn:
        upsert_best_recording(conn, species_id, first_detection_id, 0.60, "clip.wav")
        set_best_recording_highpass(conn, species_id, 1500.0)
    assert get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["highpass_hz"] == 1500.0

    _, second_detection_id = _insert_species_and_detection(conn, 0.90, "incoming/b.wav")
    with conn:
        upsert_best_recording(conn, species_id, second_detection_id, 0.90, "clip.wav")

    assert get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["highpass_hz"] == 0


def test_get_species_id_by_scientific_name(conn: sqlite3.Connection) -> None:
    species_id = get_or_create_species(conn, "Poecile atricapillus", "Black-capped Chickadee")
    assert get_species_id_by_scientific_name(conn, "Poecile atricapillus") == species_id
    assert get_species_id_by_scientific_name(conn, "Nonexistent species") is None


def test_reject_species_detections_marks_all_and_hides_from_summary(conn: sqlite3.Connection) -> None:
    species_id, _ = _insert_species_and_detection(conn, 0.60)
    with conn:
        second_detection_id = insert_detection(
            conn, _insert_segment(conn, "incoming/b.wav"), species_id,
            datetime(2026, 8, 17, 7, 0, 0, tzinfo=timezone.utc), 5.0, 8.0, 0.70, 1.0, None, None, False, None,
        )
    assert second_detection_id  # sanity: two real detections for this species now

    with conn:
        affected = reject_species_detections(conn, species_id)
    assert affected == 2

    rows = conn.execute(
        "SELECT is_reviewed, review_status FROM detections WHERE species_id = ?", (species_id,)
    ).fetchall()
    assert all(r["is_reviewed"] == 1 and r["review_status"] == "rejected" for r in rows)
    assert list_species_summary(conn) == []  # dropped out, not shown as a zero row
    assert list_detections(conn) == []


def test_reject_species_detections_returns_zero_for_unknown_species(conn: sqlite3.Connection) -> None:
    with conn:
        assert reject_species_detections(conn, 999) == 0


def test_delete_species_detections_removes_detections_and_best_recording(conn: sqlite3.Connection) -> None:
    species_id, detection_id = _insert_species_and_detection(conn, 0.60)
    with conn:
        upsert_best_recording(conn, species_id, detection_id, 0.60, "clip.wav")

    with conn:
        result = delete_species_detections(conn, species_id)

    assert result == {"detections_deleted": 1, "clip_path": "clip.wav"}
    remaining_detections = conn.execute("SELECT COUNT(*) AS c FROM detections").fetchone()["c"]
    remaining_best_recordings = conn.execute("SELECT COUNT(*) AS c FROM best_recordings").fetchone()["c"]
    assert remaining_detections == 0
    assert remaining_best_recordings == 0
    # species catalog row itself is deliberately untouched
    assert get_species_id_by_scientific_name(conn, "Poecile atricapillus") == species_id


def test_delete_species_detections_without_a_best_recording(conn: sqlite3.Connection) -> None:
    species_id, _ = _insert_species_and_detection(conn, 0.60)

    with conn:
        result = delete_species_detections(conn, species_id)

    assert result == {"detections_deleted": 1, "clip_path": None}


def test_delete_species_detections_for_unknown_species_is_a_no_op(conn: sqlite3.Connection) -> None:
    with conn:
        result = delete_species_detections(conn, 999)
    assert result == {"detections_deleted": 0, "clip_path": None}
