from datetime import datetime, timezone

from backyard_bird.analysis.birdnet_adapter import RawDetection
from backyard_bird.analysis.result_parser import parse_detection


def test_parse_detection_anchors_offset_to_absolute_time() -> None:
    segment_started_at = datetime(2026, 8, 1, 6, 42, 30, tzinfo=timezone.utc)
    raw = RawDetection(
        scientific_name="Poecile atricapillus",
        common_name="Black-capped Chickadee",
        confidence=0.812345,
        start_time_seconds=9.0,
        end_time_seconds=12.0,
    )

    parsed = parse_detection(raw, segment_started_at)

    assert parsed.detected_at_utc == datetime(2026, 8, 1, 6, 42, 39, tzinfo=timezone.utc)
    assert parsed.segment_offset_start_seconds == 9.0
    assert parsed.segment_offset_end_seconds == 12.0
    assert parsed.confidence == 0.8123  # rounded, not truncated


def test_parse_detection_normalizes_whitespace_in_names() -> None:
    raw = RawDetection(
        scientific_name="  Poecile   atricapillus ",
        common_name=" Black-capped  Chickadee",
        confidence=0.9,
        start_time_seconds=0.0,
        end_time_seconds=3.0,
    )

    parsed = parse_detection(raw, datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert parsed.scientific_name == "Poecile atricapillus"
    assert parsed.common_name == "Black-capped Chickadee"
