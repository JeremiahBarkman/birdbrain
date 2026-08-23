from datetime import datetime, timezone

from backyard_bird.analysis.deduplicator import find_duplicate


def test_finds_duplicate_within_window() -> None:
    candidate_time = datetime(2026, 8, 1, 6, 43, 0, tzinfo=timezone.utc)
    recent = [{"id": 1, "detected_at_utc": "2026-08-01T06:42:57+00:00"}]

    duplicate = find_duplicate(candidate_time, recent, window_seconds=5)

    assert duplicate is not None
    assert duplicate["id"] == 1


def test_no_duplicate_outside_window() -> None:
    candidate_time = datetime(2026, 8, 1, 6, 43, 0, tzinfo=timezone.utc)
    recent = [{"id": 1, "detected_at_utc": "2026-08-01T06:40:00+00:00"}]

    assert find_duplicate(candidate_time, recent, window_seconds=5) is None


def test_returns_earliest_match_when_multiple_candidates() -> None:
    candidate_time = datetime(2026, 8, 1, 6, 43, 0, tzinfo=timezone.utc)
    recent = [
        {"id": 1, "detected_at_utc": "2026-08-01T06:42:58+00:00"},
        {"id": 2, "detected_at_utc": "2026-08-01T06:42:59+00:00"},
    ]

    duplicate = find_duplicate(candidate_time, recent, window_seconds=5)

    assert duplicate["id"] == 1


def test_empty_recent_list_is_never_a_duplicate() -> None:
    candidate_time = datetime(2026, 8, 1, 6, 43, 0, tzinfo=timezone.utc)
    assert find_duplicate(candidate_time, [], window_seconds=5) is None
