"""Unit tests for CaptureService. sounddevice and device discovery are
both faked out — real hardware / microphone permission is never
touched, so these run fine over SSH despite the TCC constraint that
blocks live capture (see README).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backyard_bird.audio import capture_service as capture_service_module
from backyard_bird.audio.capture_service import CaptureService
from backyard_bird.config import AudioConfig


class _FakeInputStream:
    """Stands in for sounddevice.InputStream: feeds synthetic chunks
    into the real callback as soon as the `with` block is entered."""

    def __init__(self, chunks: list[np.ndarray], **kwargs: object) -> None:
        self._chunks = chunks
        self._callback = kwargs["callback"]

    def __enter__(self) -> "_FakeInputStream":
        for chunk in self._chunks:
            self._callback(chunk, len(chunk), None, None)
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _audio_config(**overrides: object) -> AudioConfig:
    defaults = dict(
        device_name="Fake Mic",
        microphone_id="test-mic",
        sample_rate=100,
        channels=1,
        segment_seconds=1.0,
        overlap_seconds=0.2,
        raw_audio_retention_days=7,
        failed_audio_retention_days=30,
    )
    defaults.update(overrides)
    return AudioConfig(**defaults)  # type: ignore[arg-type]


def test_capture_run_writes_expected_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)

    # 500 samples in uneven 17-sample chunks -> 6 complete segments
    # (same math as test_segmenter.py: segment=100, overlap=20).
    all_samples = np.arange(500, dtype=np.int16).reshape(-1, 1)
    chunks = [all_samples[i : i + 17] for i in range(0, 500, 17)]
    monkeypatch.setattr(
        capture_service_module.sd,
        "InputStream",
        lambda **kwargs: _FakeInputStream(chunks, **kwargs),
    )

    incoming_dir = tmp_path / "incoming"
    service = CaptureService(_audio_config(), incoming_dir)

    written = service.run(max_segments=6)

    assert written == 6
    wav_files = sorted(incoming_dir.glob("*.wav"))
    assert len(wav_files) == 6
    assert not list(incoming_dir.glob("*.partial"))  # nothing left mid-write


def test_missing_device_is_reported_and_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CaptureService(_audio_config(), tmp_path / "incoming")

    def _find_then_stop(name: str) -> None:
        service.stop()  # simulate an operator stopping the service after the failure is logged
        return None

    monkeypatch.setattr(capture_service_module, "find_input_device", _find_then_stop)

    written = service.run()

    assert written == 0
