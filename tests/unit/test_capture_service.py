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
from backyard_bird.audio.capture_service import _STATUS_WRITE_INTERVAL_SECONDS, CaptureService
from backyard_bird.audio.levels import read_mic_status
from backyard_bird.config import AudioConfig


class _FakeInputStream:
    """Stands in for sounddevice.InputStream: feeds synthetic chunks
    into the real callback as soon as the `with` block is entered — or,
    if `ready_event` is given, once that event is set, so a test can
    hold delivery back until some other precondition (e.g. a live-
    monitor client actually connecting) is definitely satisfied first.
    """

    def __init__(self, chunks: list[np.ndarray], ready_event=None, **kwargs: object) -> None:
        self._chunks = chunks
        self._callback = kwargs["callback"]
        self._ready_event = ready_event

    def __enter__(self) -> "_FakeInputStream":
        if self._ready_event is not None:
            self._ready_event.wait(timeout=5)
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


# -- gain (user request: mic input runs quiet by default) -------------------


def _read_wav_samples(path: Path) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as wav_file:
        return np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype=np.int16)


def test_capture_applies_configured_gain_to_written_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)

    original = np.full((100, 1), 1000, dtype=np.int16)
    monkeypatch.setattr(
        capture_service_module.sd, "InputStream", lambda **kwargs: _FakeInputStream([original], **kwargs)
    )

    incoming_dir = tmp_path / "incoming"
    service = CaptureService(_audio_config(gain=2.5), incoming_dir)
    service.run(max_segments=1)

    wav_files = sorted(incoming_dir.glob("*.wav"))
    assert len(wav_files) == 1
    written_samples = _read_wav_samples(wav_files[0])
    # The whole segment's samples are boosted 2.5x, not just some of
    # them — gain is applied once, upstream of the segmenter.
    assert all(sample == 2500 for sample in written_samples)


def test_capture_gain_control_file_overrides_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backyard_bird.audio.gain import write_gain_control

    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)

    original = np.full((100, 1), 1000, dtype=np.int16)
    monkeypatch.setattr(
        capture_service_module.sd, "InputStream", lambda **kwargs: _FakeInputStream([original], **kwargs)
    )

    gain_control_path = tmp_path / "run" / "mic_gain.json"
    write_gain_control(gain_control_path, 4.0)  # dashboard already set a live value before capture starts

    incoming_dir = tmp_path / "incoming"
    service = CaptureService(_audio_config(gain=1.0), incoming_dir, gain_control_path=gain_control_path)
    service.run(max_segments=1)

    wav_files = sorted(incoming_dir.glob("*.wav"))
    written_samples = _read_wav_samples(wav_files[0])
    assert all(sample == 4000 for sample in written_samples)


def test_capture_gain_clips_rather_than_wrapping_around(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)

    original = np.full((100, 1), 30000, dtype=np.int16)
    monkeypatch.setattr(
        capture_service_module.sd, "InputStream", lambda **kwargs: _FakeInputStream([original], **kwargs)
    )

    incoming_dir = tmp_path / "incoming"
    service = CaptureService(_audio_config(gain=3.0), incoming_dir)
    service.run(max_segments=1)

    wav_files = sorted(incoming_dir.glob("*.wav"))
    written_samples = _read_wav_samples(wav_files[0])
    assert all(sample == 32767 for sample in written_samples)  # clipped, never a wrapped negative value


# -- device switching (user request: "in case there is more than one mic") --


def test_capture_switches_device_live_without_error_or_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backyard_bird.audio.device_control import write_device_control

    device_a = SimpleNamespace(index=0, name="Mic A")
    device_b = SimpleNamespace(index=1, name="Mic B")
    monkeypatch.setattr(
        capture_service_module, "find_input_device", lambda name: {"Mic A": device_a, "Mic B": device_b}.get(name)
    )

    # Guarantees the periodic device-presence/switch check fires on
    # essentially every loop iteration, without this test actually
    # waiting out the real 5-second interval.
    fake_clock = {"t": 0.0}

    def _fake_monotonic() -> float:
        fake_clock["t"] += 10.0
        return fake_clock["t"]

    monkeypatch.setattr(capture_service_module.time, "monotonic", _fake_monotonic)

    one_segment = np.arange(100, dtype=np.int16).reshape(-1, 1)

    def _input_stream(**kwargs: object) -> _FakeInputStream:
        # Device A gets no chunks at all — proves the switch is
        # noticed before anything from the old device is ever
        # processed, not just that new chunks land on the new one.
        chunks = [] if kwargs["device"] == device_a.index else [one_segment]
        return _FakeInputStream(chunks, **kwargs)

    monkeypatch.setattr(capture_service_module.sd, "InputStream", _input_stream)

    device_control_path = tmp_path / "run" / "mic_device.json"
    write_device_control(device_control_path, "Mic B")  # dashboard already requested this before capture starts

    incoming_dir = tmp_path / "incoming"
    status_path = tmp_path / "run" / "mic_status.json"
    service = CaptureService(
        _audio_config(device_name="Mic A"),
        incoming_dir,
        status_path=status_path,
        device_control_path=device_control_path,
    )

    written = service.run(max_segments=1)

    assert written == 1
    assert len(list(incoming_dir.glob("*.wav"))) == 1
    status = read_mic_status(status_path)
    # A deliberate switch must never look like a failure: no "error"
    # status, and (implicitly, since this test has no reconnect-wait
    # sleep budget) no backoff delay either.
    assert status["status"] != "error"
    assert status["device_name"] == "Mic B"


def test_capture_without_device_control_path_never_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Existing behavior, unchanged: a caller/test that doesn't pass
    # device_control_path (e.g. every other test in this file) keeps
    # using the configured device for the whole run, even if a stray
    # mic_device.json happens to exist on disk.
    from backyard_bird.audio.device_control import write_device_control

    fake_device = SimpleNamespace(index=0, name="Mic A")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)

    fake_clock = {"t": 0.0}
    monkeypatch.setattr(
        capture_service_module.time, "monotonic", lambda: fake_clock.update(t=fake_clock["t"] + 10.0) or fake_clock["t"]
    )

    one_segment = np.arange(100, dtype=np.int16).reshape(-1, 1)
    monkeypatch.setattr(
        capture_service_module.sd, "InputStream", lambda **kwargs: _FakeInputStream([one_segment], **kwargs)
    )

    write_device_control(tmp_path / "run" / "mic_device.json", "Mic B")  # present, but never consulted

    incoming_dir = tmp_path / "incoming"
    service = CaptureService(_audio_config(device_name="Mic A"), incoming_dir)  # no device_control_path

    written = service.run(max_segments=1)

    assert written == 1
    assert len(list(incoming_dir.glob("*.wav"))) == 1


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


def test_capture_without_status_path_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Existing behavior, unchanged: a caller (or older test) that
    # doesn't pass status_path gets no live-status reporting at all,
    # not an error.
    service = CaptureService(_audio_config(), tmp_path / "incoming")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: service.stop() or None)

    service.run()

    assert not (tmp_path / "run").exists()


def test_capture_writes_capturing_status_with_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)

    # >= 100 samples (segment_seconds=1.0 * sample_rate=100), so this
    # single chunk completes a segment on its own and max_segments=1
    # below is actually reachable — too few samples would leave run()
    # blocked forever waiting for a chunk that never arrives.
    loud_chunk = np.full((150, 1), 16384, dtype=np.int16)  # half-scale, well above silence
    monkeypatch.setattr(
        capture_service_module.sd, "InputStream", lambda **kwargs: _FakeInputStream([loud_chunk], **kwargs)
    )

    status_path = tmp_path / "run" / "mic_status.json"
    service = CaptureService(_audio_config(), tmp_path / "incoming", status_path=status_path)
    # time.monotonic() starts near 0 at process start on this platform,
    # and this whole fake-stream test runs in milliseconds — the real
    # "1 second since the last status write" condition would never
    # actually elapse. Force it directly rather than trying to fake
    # real elapsed time.
    service._last_status_write = -_STATUS_WRITE_INTERVAL_SECONDS

    # run()'s own final "stopped" write overwrites the "capturing" one
    # by the time run() returns (same reasoning as the error-status
    # test below) — a spy observes the intermediate call directly.
    calls: list[tuple] = []
    original_write_status = service._write_status
    monkeypatch.setattr(
        service,
        "_write_status",
        lambda status, level=None, error_message=None: (
            calls.append((status, level)),
            original_write_status(status, level=level, error_message=error_message),
        ),
    )

    service.run(max_segments=1)

    capturing_calls = [c for c in calls if c[0] == "capturing" and c[1] is not None]
    assert len(capturing_calls) == 1
    level = capturing_calls[0][1]
    assert level.peak_percent == pytest.approx(50.0, abs=1.0)

    payload = read_mic_status(status_path)
    assert payload["device_name"] == "Fake Mic"


def test_capture_writes_stopped_status_after_run_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)
    chunk = np.zeros((150, 1), dtype=np.int16)  # >= 100 samples — see comment in the test above
    monkeypatch.setattr(
        capture_service_module.sd, "InputStream", lambda **kwargs: _FakeInputStream([chunk], **kwargs)
    )

    status_path = tmp_path / "run" / "mic_status.json"
    service = CaptureService(_audio_config(), tmp_path / "incoming", status_path=status_path)
    service.run(max_segments=1)

    # run() returning writes a final "stopped" status even though the
    # stream itself never errored — a stale-but-"capturing" status
    # would otherwise sit there for up to STALE_AFTER_SECONDS after a
    # clean shutdown.
    payload = read_mic_status(status_path)
    assert payload["status"] == "stopped"


def test_capture_reports_error_status_on_missing_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status_path = tmp_path / "run" / "mic_status.json"
    service = CaptureService(_audio_config(), tmp_path / "incoming", status_path=status_path)

    # The final "stopped" write (run() always does this on the way out)
    # overwrites whatever "error" write happened moments before, since
    # the status file only ever holds the latest state — so the error
    # itself has to be observed via a spy, not the file's end state.
    calls: list[tuple] = []
    original_write_status = service._write_status

    def _spy(status: str, level=None, error_message: str | None = None) -> None:
        calls.append((status, error_message))
        original_write_status(status, level=level, error_message=error_message)

    monkeypatch.setattr(service, "_write_status", _spy)

    def _find_then_stop(name: str) -> None:
        service.stop()
        return None

    monkeypatch.setattr(capture_service_module, "find_input_device", _find_then_stop)
    service.run()

    error_calls = [c for c in calls if c[0] == "error"]
    assert len(error_calls) == 1
    assert "Configured microphone not found" in error_calls[0][1]


def test_capture_relays_real_audio_to_a_connected_live_monitor_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real socket, real CaptureService, only the audio hardware is
    # faked — this is the actual point of the live-monitor feature:
    # a client connected to the relay port receives the same bytes
    # capture is processing.
    #
    # ready_event holds the fake stream's chunk delivery back until
    # this test has actually connected a client and confirmed the
    # server sees it — without that, the (near-instant, synthetic)
    # capture loop could broadcast before a real client ever connects,
    # racing the very thing this test means to verify.
    import socket
    import threading
    import time as time_module

    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)

    loud_chunk = np.full((150, 1), 12345, dtype=np.int16)
    ready_event = threading.Event()
    monkeypatch.setattr(
        capture_service_module.sd,
        "InputStream",
        lambda **kwargs: _FakeInputStream([loud_chunk], ready_event=ready_event, **kwargs),
    )

    service = CaptureService(_audio_config(), tmp_path / "incoming", live_monitor_port=0)

    run_thread = threading.Thread(target=service.run, kwargs={"max_segments": 1})
    run_thread.start()
    try:
        for _ in range(200):
            if service._live_monitor_server is not None:
                break
            time_module.sleep(0.01)
        assert service._live_monitor_server is not None, "relay server never started"
        port = service._live_monitor_server.server_address[1]

        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            for _ in range(200):
                if service._live_monitor_server.has_clients():
                    break
                time_module.sleep(0.01)
            assert service._live_monitor_server.has_clients(), "server never registered the client"

            ready_event.set()  # now safe to let the fake stream deliver its chunk

            expected = loud_chunk.astype(np.int16).tobytes()
            client.settimeout(5)
            received = b""
            while len(received) < len(expected):
                more = client.recv(len(expected) - len(received))
                if not more:
                    break
                received += more
            assert received == expected
        finally:
            client.close()
    finally:
        ready_event.set()  # in case an assertion above failed before this ran
        run_thread.join(timeout=5)


def test_capture_without_live_monitor_port_does_not_start_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_device = SimpleNamespace(index=0, name="Fake Mic")
    monkeypatch.setattr(capture_service_module, "find_input_device", lambda name: fake_device)
    chunk = np.zeros((150, 1), dtype=np.int16)
    monkeypatch.setattr(
        capture_service_module.sd, "InputStream", lambda **kwargs: _FakeInputStream([chunk], **kwargs)
    )

    service = CaptureService(_audio_config(), tmp_path / "incoming")  # live_monitor_port not passed
    service.run(max_segments=1)

    assert service._live_monitor_server is None
