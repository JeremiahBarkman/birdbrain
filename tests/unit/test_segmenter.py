from datetime import datetime, timezone

import numpy as np
import pytest

from backyard_bird.audio.segmenter import (
    AudioSegmenter,
    format_segment_filename,
    parse_segment_filename,
)


def _make_segmenter(**overrides: object) -> AudioSegmenter:
    defaults = dict(
        sample_rate=100,
        channels=1,
        segment_seconds=1.0,
        overlap_seconds=0.2,
        stream_started_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return AudioSegmenter(**defaults)  # type: ignore[arg-type]


def test_produces_correctly_sized_overlapping_segments() -> None:
    segmenter = _make_segmenter()
    # Sequential sample values make the overlapping region easy to
    # verify by content, not just by length.
    all_samples = np.arange(500, dtype=np.int16).reshape(-1, 1)

    segments = []
    for i in range(0, len(all_samples), 17):  # deliberately uneven chunk size
        segments.extend(segmenter.push(all_samples[i : i + 17]))

    # segment_samples=100, stride_samples=80 -> floor((500-100)/80)+1 = 6
    assert len(segments) == 6
    for seg in segments:
        assert len(seg.samples) == 100

    for a, b in zip(segments, segments[1:]):
        assert b.sequence == a.sequence + 1
        assert np.array_equal(a.samples[-20:], b.samples[:20])  # overlap_samples=20


def test_segment_start_times_advance_by_stride() -> None:
    segmenter = _make_segmenter()
    all_samples = np.zeros((500, 1), dtype=np.int16)
    segments = segmenter.push(all_samples)

    stride_seconds = 0.8  # (100 - 20) / 100
    for a, b in zip(segments, segments[1:]):
        delta = (b.started_at_utc - a.started_at_utc).total_seconds()
        assert abs(delta - stride_seconds) < 1e-9


def test_incomplete_trailing_audio_is_never_emitted() -> None:
    segmenter = _make_segmenter()
    segments = segmenter.push(np.zeros((150, 1), dtype=np.int16))
    assert len(segments) == 1  # only one full 100-sample segment fits in 150


def test_start_sequence_offset_continues_numbering() -> None:
    segmenter = _make_segmenter(start_sequence=5)
    segments = segmenter.push(np.zeros((100, 1), dtype=np.int16))
    assert segments[0].sequence == 5


def test_rejects_overlap_not_smaller_than_segment() -> None:
    with pytest.raises(ValueError):
        _make_segmenter(overlap_seconds=1.0)


def test_filename_format_and_parse_round_trip() -> None:
    started_at = datetime(2026, 8, 1, 6, 42, 30, tzinfo=timezone.utc)
    filename = format_segment_filename(started_at, "mic-01", 184)

    assert filename == "2026-08-01T06-42-30_mic-01_000184.wav"

    info = parse_segment_filename(filename)
    assert info.recording_started_at_utc == started_at
    assert info.microphone_id == "mic-01"
    assert info.sequence == 184


def test_parse_rejects_unrecognized_filename() -> None:
    with pytest.raises(ValueError):
        parse_segment_filename("not-a-segment.wav")
