"""Worker tests fake out analyze_file so they never load the real
BirdNET model — fast, and independent of any model/hardware. Live
BirdNET behavior is covered separately by
tests/integration/test_birdnet_adapter.py.
"""
from __future__ import annotations

import os
import sqlite3
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backyard_bird.analysis import worker as worker_module
from backyard_bird.analysis.birdnet_adapter import AnalysisResult, RawDetection
from backyard_bird.analysis.worker import (
    QueueDirs,
    _maybe_sweep_processed_and_failed,
    process_one_file,
    recover_stuck_segments,
)
from backyard_bird.config import AudioConfig, BirdNETConfig, DetectionsConfig, LocationConfig
from backyard_bird.database.migrations import apply_migrations
from backyard_bird.database.repositories import (
    find_audio_segment_by_file_path,
    get_best_recordings_by_scientific_name,
    insert_audio_segment,
    mark_audio_segment_completed,
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


@pytest.fixture()
def dirs(tmp_path: Path) -> QueueDirs:
    d = QueueDirs.under(tmp_path / "data")
    d.ensure()
    return d


def _write_wav(path: Path, seconds: float = 3.0, sample_rate: int = 48000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * int(seconds * sample_rate))


def _config() -> tuple[BirdNETConfig, LocationConfig, DetectionsConfig]:
    return BirdNETConfig(), LocationConfig(latitude=45.0, longitude=-123.0), DetectionsConfig()


# -- process_one_file --------------------------------------------------------


def test_process_one_file_writes_detections_and_files_segment(
    conn: sqlite3.Connection, dirs: QueueDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav"
    _write_wav(path)
    fake_result = AnalysisResult(
        detections=[RawDetection("Poecile atricapillus", "Black-capped Chickadee", 0.81, 0.0, 3.0)],
        birdnet_version="2.4",
        analysis_duration_seconds=0.1,
    )
    monkeypatch.setattr(worker_module, "analyze_file", lambda *a, **k: fake_result)

    birdnet_config, location, detections_config = _config()
    process_one_file(conn, path, dirs, birdnet_config, location, detections_config)

    assert not path.exists()
    assert len(list(dirs.processed.glob("*.wav"))) == 1
    assert not list(dirs.processing.glob("*.wav"))

    segment = find_audio_segment_by_file_path(conn, str(path))
    assert segment["processing_status"] == "completed"
    detection = conn.execute("SELECT * FROM detections").fetchone()
    assert detection["confidence"] == 0.81
    assert detection["is_duplicate"] == 0

    # A single detection is automatically the species' best recording.
    best = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert best["confidence"] == 0.81
    assert best["is_approved"] == 0
    assert Path(best["clip_path"]).exists()


def test_process_one_file_routes_analysis_failure_to_failed_dir(
    conn: sqlite3.Connection, dirs: QueueDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav"
    _write_wav(path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(worker_module, "analyze_file", _boom)

    birdnet_config, location, detections_config = _config()
    process_one_file(conn, path, dirs, birdnet_config, location, detections_config)

    assert len(list(dirs.failed.glob("*.wav"))) == 1
    segment = find_audio_segment_by_file_path(conn, str(path))
    assert segment["processing_status"] == "failed"
    assert "model exploded" in segment["error_message"]


def test_process_one_file_routes_unreadable_audio_to_failed_without_db_row(
    conn: sqlite3.Connection, dirs: QueueDirs
) -> None:
    path = dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav"
    path.write_bytes(b"not actually a wav file")

    birdnet_config, location, detections_config = _config()
    process_one_file(conn, path, dirs, birdnet_config, location, detections_config)

    assert len(list(dirs.failed.glob("*.wav"))) == 1
    assert find_audio_segment_by_file_path(conn, str(path)) is None


def test_duplicate_detection_across_overlapping_segments_is_flagged(
    conn: sqlite3.Connection, dirs: QueueDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two segments 27s apart (30s/3s overlap default) with the same
    # call caught near the shared boundary in both -> the second must
    # be flagged a duplicate, not counted as a second real sighting.
    first_path = dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav"
    _write_wav(first_path)
    monkeypatch.setattr(
        worker_module,
        "analyze_file",
        lambda *a, **k: AnalysisResult(
            detections=[RawDetection("Poecile atricapillus", "Black-capped Chickadee", 0.81, 28.0, 30.0)],
            birdnet_version="2.4",
            analysis_duration_seconds=0.1,
        ),
    )
    birdnet_config, location, detections_config = _config()
    process_one_file(conn, first_path, dirs, birdnet_config, location, detections_config)

    second_path = dirs.incoming / "2026-08-01T06-00-27_mic-01_000002.wav"
    _write_wav(second_path)
    monkeypatch.setattr(
        worker_module,
        "analyze_file",
        lambda *a, **k: AnalysisResult(
            detections=[RawDetection("Poecile atricapillus", "Black-capped Chickadee", 0.79, 1.0, 3.0)],
            birdnet_version="2.4",
            analysis_duration_seconds=0.1,
        ),
    )
    process_one_file(conn, second_path, dirs, birdnet_config, location, detections_config)

    rows = conn.execute("SELECT confidence, is_duplicate FROM detections ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["is_duplicate"] == 0
    assert rows[1]["is_duplicate"] == 1

    # The duplicate (lower-confidence anyway) must not touch the best
    # recording, which stays the first (real) detection's.
    best = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert best["confidence"] == 0.81


# -- best_recordings ----------------------------------------------------------


def _process_with_detection(
    conn: sqlite3.Connection,
    dirs: QueueDirs,
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    confidence: float,
    detections_config: DetectionsConfig,
) -> None:
    _write_wav(path)
    monkeypatch.setattr(
        worker_module,
        "analyze_file",
        lambda *a, **k: AnalysisResult(
            detections=[RawDetection("Poecile atricapillus", "Black-capped Chickadee", confidence, 0.0, 3.0)],
            birdnet_version="2.4",
            analysis_duration_seconds=0.1,
        ),
    )
    birdnet_config, location, _ = _config()
    process_one_file(conn, path, dirs, birdnet_config, location, detections_config)


def test_higher_confidence_detection_replaces_best_recording(
    conn: sqlite3.Connection, dirs: QueueDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    detections_config = DetectionsConfig()
    _process_with_detection(
        conn, dirs, monkeypatch, dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav", 0.60, detections_config
    )
    first_clip = Path(get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["clip_path"])
    assert first_clip.exists()

    _process_with_detection(
        conn, dirs, monkeypatch, dirs.incoming / "2026-08-01T06-05-00_mic-01_000002.wav", 0.90, detections_config
    )

    best = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert best["confidence"] == 0.90
    # Same fixed per-species path, overwritten in place — not a second file.
    assert Path(best["clip_path"]) == first_clip


def test_lower_confidence_detection_does_not_replace_best_recording(
    conn: sqlite3.Connection, dirs: QueueDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    detections_config = DetectionsConfig()
    _process_with_detection(
        conn, dirs, monkeypatch, dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav", 0.90, detections_config
    )
    _process_with_detection(
        conn, dirs, monkeypatch, dirs.incoming / "2026-08-01T06-05-00_mic-01_000002.wav", 0.60, detections_config
    )

    best = get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]
    assert best["confidence"] == 0.90


def test_replacing_best_recording_resets_approval(
    conn: sqlite3.Connection, dirs: QueueDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backyard_bird.database.repositories import get_species_id_by_scientific_name, set_best_recording_approved

    detections_config = DetectionsConfig()
    _process_with_detection(
        conn, dirs, monkeypatch, dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav", 0.60, detections_config
    )
    species_id = get_species_id_by_scientific_name(conn, "Poecile atricapillus")
    with conn:
        assert set_best_recording_approved(conn, species_id, True)
    assert get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["is_approved"] == 1

    _process_with_detection(
        conn, dirs, monkeypatch, dirs.incoming / "2026-08-01T06-05-00_mic-01_000002.wav", 0.90, detections_config
    )

    assert get_best_recordings_by_scientific_name(conn)["Poecile atricapillus"]["is_approved"] == 0


def test_extract_detection_clips_disabled_skips_best_recording_entirely(
    conn: sqlite3.Connection, dirs: QueueDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    detections_config = DetectionsConfig(extract_detection_clips=False)
    _process_with_detection(
        conn, dirs, monkeypatch, dirs.incoming / "2026-08-01T06-00-00_mic-01_000001.wav", 0.90, detections_config
    )

    assert get_best_recordings_by_scientific_name(conn) == {}


# -- recover_stuck_segments ---------------------------------------------------


def test_recover_requeues_orphan_with_no_db_row(conn: sqlite3.Connection, dirs: QueueDirs) -> None:
    orphan = dirs.processing / "2026-08-01T06-00-00_mic-01_000001.wav"
    _write_wav(orphan)

    recovered = recover_stuck_segments(conn, dirs)

    assert recovered == 1
    assert (dirs.incoming / orphan.name).exists()
    assert not orphan.exists()


def test_recover_finishes_move_for_already_completed_segment(
    conn: sqlite3.Connection, dirs: QueueDirs
) -> None:
    filename = "2026-08-01T06-00-00_mic-01_000001.wav"
    stuck = dirs.processing / filename
    _write_wav(stuck)

    with conn:
        segment_id = insert_audio_segment(
            conn,
            "mic-01",
            str(dirs.incoming / filename),
            datetime(2026, 8, 1, 6, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 1, 6, 0, 3, tzinfo=timezone.utc),
            3.0,
            48000,
            1,
            100,
            "abc",
        )
        mark_audio_segment_completed(conn, segment_id, "2.4")

    recovered = recover_stuck_segments(conn, dirs)

    assert recovered == 1
    assert (dirs.processed / filename).exists()
    assert not stuck.exists()
    assert not (dirs.incoming / filename).exists()  # not reprocessed — no duplicate detections


def test_recover_resets_interrupted_segment_to_pending(conn: sqlite3.Connection, dirs: QueueDirs) -> None:
    filename = "2026-08-01T06-00-00_mic-01_000001.wav"
    stuck = dirs.processing / filename
    _write_wav(stuck)

    with conn:
        insert_audio_segment(
            conn,
            "mic-01",
            str(dirs.incoming / filename),
            datetime(2026, 8, 1, 6, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 1, 6, 0, 3, tzinfo=timezone.utc),
            3.0,
            48000,
            1,
            100,
            "abc",
        )  # left at status='processing' by insert_audio_segment's default

    recover_stuck_segments(conn, dirs)

    assert (dirs.incoming / filename).exists()
    row = find_audio_segment_by_file_path(conn, str(dirs.incoming / filename))
    assert row["processing_status"] == "pending"


# -- _maybe_sweep_processed_and_failed -----------------------------------------


def _audio_config(**overrides: object) -> AudioConfig:
    return AudioConfig(device_name="test-mic", microphone_id="mic-01", **overrides)


def _age_file(path: Path, days: float) -> None:
    old_time = time.time() - days * 86400
    os.utime(path, (old_time, old_time))


def test_sweep_deletes_old_processed_and_failed_files_once_interval_elapses(dirs: QueueDirs) -> None:
    old_processed = dirs.processed / "old.wav"
    old_failed = dirs.failed / "old.wav"
    old_processed.write_bytes(b"x")
    old_failed.write_bytes(b"x")
    _age_file(old_processed, days=10)
    _age_file(old_failed, days=10)

    audio_config = _audio_config(processed_audio_retention_days=7, failed_audio_retention_days=7)
    new_last_swept = _maybe_sweep_processed_and_failed(dirs, audio_config, last_swept_at=0.0, now=10_000.0)

    assert new_last_swept == 10_000.0
    assert not old_processed.exists()
    assert not old_failed.exists()


def test_sweep_is_noop_before_interval_elapses(dirs: QueueDirs) -> None:
    old_processed = dirs.processed / "old.wav"
    old_processed.write_bytes(b"x")
    _age_file(old_processed, days=10)

    audio_config = _audio_config(processed_audio_retention_days=7)
    new_last_swept = _maybe_sweep_processed_and_failed(dirs, audio_config, last_swept_at=100.0, now=200.0)

    assert new_last_swept == 100.0  # unchanged: interval hasn't elapsed
    assert old_processed.exists()


def test_sweep_is_noop_when_audio_config_omitted(dirs: QueueDirs) -> None:
    old_processed = dirs.processed / "old.wav"
    old_processed.write_bytes(b"x")
    _age_file(old_processed, days=10)

    new_last_swept = _maybe_sweep_processed_and_failed(dirs, None, last_swept_at=0.0, now=10_000.0)

    assert new_last_swept == 0.0
    assert old_processed.exists()
