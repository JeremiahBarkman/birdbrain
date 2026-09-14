from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from backyard_bird.audio.levels import (
    STALE_AFTER_SECONDS,
    compute_level,
    read_mic_status,
    write_mic_status,
)


def test_compute_level_silence_is_zero() -> None:
    chunk = np.zeros(100, dtype=np.int16)
    level = compute_level(chunk)
    assert level.peak_percent == 0.0
    assert level.peak_dbfs == -60.0


def test_compute_level_full_scale_is_100_percent_and_0dbfs() -> None:
    chunk = np.array([32767, -32768], dtype=np.int16)
    level = compute_level(chunk)
    assert level.peak_percent == 100.0
    assert level.peak_dbfs == 0.0


def test_compute_level_half_scale() -> None:
    chunk = np.array([16384], dtype=np.int16)  # exactly half of 32768
    level = compute_level(chunk)
    assert level.peak_percent == 50.0
    assert -7.0 < level.peak_dbfs < -5.0  # ~-6dB


def test_compute_level_empty_chunk_is_silence() -> None:
    level = compute_level(np.array([], dtype=np.int16))
    assert level.peak_percent == 0.0
    assert level.peak_dbfs == -60.0


def test_write_and_read_mic_status_round_trip(tmp_path: Path) -> None:
    status_path = tmp_path / "run" / "mic_status.json"
    level = compute_level(np.array([16384], dtype=np.int16))

    write_mic_status(status_path, device_name="TONOR G11", status="capturing", level=level)
    payload = read_mic_status(status_path)

    assert payload is not None
    assert payload["device_name"] == "TONOR G11"
    assert payload["status"] == "capturing"
    assert payload["peak_percent"] == 50.0
    assert payload["error_message"] is None


def test_write_mic_status_creates_parent_directory(tmp_path: Path) -> None:
    status_path = tmp_path / "does" / "not" / "exist" / "mic_status.json"
    write_mic_status(status_path, device_name="Mic", status="stopped", level=None)
    assert status_path.exists()


def test_write_mic_status_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    status_path = tmp_path / "mic_status.json"
    write_mic_status(status_path, device_name="Mic", status="stopped", level=None)
    assert list(tmp_path.iterdir()) == [status_path]  # no leftover .tmp


def test_read_mic_status_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert read_mic_status(tmp_path / "nope.json") is None


def test_read_mic_status_returns_none_when_malformed(tmp_path: Path) -> None:
    status_path = tmp_path / "mic_status.json"
    status_path.write_text("not json")
    assert read_mic_status(status_path) is None


def test_read_mic_status_returns_none_when_stale(tmp_path: Path) -> None:
    # Simulates capture having crashed or been killed: the file is
    # real and well-formed, just old — the dashboard must not keep
    # showing a frozen "capturing" reading forever.
    status_path = tmp_path / "mic_status.json"
    write_mic_status(status_path, device_name="Mic", status="capturing", level=None)
    payload = read_mic_status(status_path)
    assert payload is not None

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    import json

    data = json.loads(status_path.read_text())
    data["updated_at_utc"] = stale_time.isoformat()
    status_path.write_text(json.dumps(data))

    assert read_mic_status(status_path) is None


def test_write_mic_status_includes_error_message(tmp_path: Path) -> None:
    status_path = tmp_path / "mic_status.json"
    write_mic_status(
        status_path, device_name="Mic", status="error", level=None, error_message="Microphone disappeared"
    )
    payload = read_mic_status(status_path)
    assert payload["error_message"] == "Microphone disappeared"
