"""Unit tests for the bird-display doctor checks (§25).

check_birdnet's happy path (real model load + analysis) is exercised
by tests/integration/test_birdnet_adapter.py instead — loading the
real model is slow and needs a sample WAV, same reasoning as that
file. Here we only test the parts that don't need the real model:
import/load failure handling, and the has-no-sample-wav path.
"""
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from backyard_bird.audio.devices import AudioDevice
from backyard_bird.doctor import (
    check_audio_devices,
    check_birdnet,
    check_disk_space,
    check_microphone_permission,
    check_platform,
    check_python_version,
    check_required_directories,
    run_all_checks,
)


def test_platform_check_fails_on_unsupported_os() -> None:
    with patch("platform.system", return_value="Windows"):
        result = check_platform()
    assert result.status == "fail"


def test_platform_check_warns_on_intel_mac() -> None:
    with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="x86_64"):
        result = check_platform()
    assert result.status == "warn"


def test_platform_check_passes_on_apple_silicon() -> None:
    with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="arm64"):
        result = check_platform()
    assert result.status == "pass"


def test_platform_check_passes_on_linux_aarch64() -> None:
    # The Raspberry Pi 4B/Ubuntu 24.04 host profile (requirements §7.1).
    with patch("platform.system", return_value="Linux"), patch("platform.machine", return_value="aarch64"):
        result = check_platform()
    assert result.status == "pass"


def test_platform_check_passes_on_linux_x86_64() -> None:
    with patch("platform.system", return_value="Linux"), patch("platform.machine", return_value="x86_64"):
        result = check_platform()
    assert result.status == "pass"


def test_platform_check_warns_on_unusual_linux_arch() -> None:
    with patch("platform.system", return_value="Linux"), patch("platform.machine", return_value="armv7l"):
        result = check_platform()
    assert result.status == "warn"


def test_python_version_passes_on_current_interpreter() -> None:
    # The test suite itself only runs on a supported interpreter (see
    # pyproject.toml requires-python), so this should always pass.
    result = check_python_version()
    assert result.status == "pass"


def test_birdnet_import_failure_reported_as_fail() -> None:
    with patch("birdnetlib.analyzer.Analyzer", side_effect=ImportError("no module")):
        result = check_birdnet()
    assert result.status == "fail"
    assert "birdnetlib" in result.message.lower() or "import" in result.message.lower()


def test_birdnet_model_load_failure_reported_as_fail() -> None:
    with patch("birdnetlib.analyzer.Analyzer", side_effect=RuntimeError("model file missing")):
        result = check_birdnet()
    assert result.status == "fail"
    assert "model file missing" in result.message


def test_birdnet_without_sample_wav_warns_but_does_not_fail() -> None:
    with patch("birdnetlib.analyzer.Analyzer") as mock_analyzer:
        mock_analyzer.return_value = object()
        result = check_birdnet(sample_wav=None)
    assert result.status == "warn"


def test_required_directories_creates_missing_subdirs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = check_required_directories(data_dir)
    assert result.status == "pass"
    assert (data_dir / "audio" / "incoming").is_dir()
    assert (data_dir / "database").is_dir()
    assert (data_dir / "logs").is_dir()


def test_disk_space_warns_when_below_threshold(tmp_path: Path) -> None:
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = type("Usage", (), {"total": 100, "used": 99, "free": 1 * 1024**3})()
        result = check_disk_space(tmp_path)
    assert result.status == "warn"


def test_disk_space_passes_with_plenty_free(tmp_path: Path) -> None:
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = type("Usage", (), {"total": 1000, "used": 10, "free": 500 * 1024**3})()
        result = check_disk_space(tmp_path)
    assert result.status == "pass"


def test_audio_devices_fails_when_none_found() -> None:
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=[]):
        result = check_audio_devices()
    assert result.status == "fail"


def test_audio_devices_warns_when_configured_device_not_found() -> None:
    devices = [AudioDevice(index=0, name="Built-in Microphone", max_input_channels=1, default_samplerate=48000.0, host_api="Core Audio")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        result = check_audio_devices(configured_device_name="USB Mic (nonexistent)")
    assert result.status == "warn"


def test_audio_devices_passes_via_alsa_hw_index_fallback() -> None:
    # Regression: doctor must agree with find_input_device's ALSA
    # (hw:N,M) fallback (devices.py), not just an exact-name membership
    # check - otherwise it warns about a device capture would resolve fine.
    devices = [AudioDevice(index=0, name="Mic (hw:3,0)", max_input_channels=1, default_samplerate=48000.0, host_api="ALSA")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        result = check_audio_devices(configured_device_name="Mic (hw:1,0)")
    assert result.status == "pass"


def test_audio_devices_passes_when_configured_device_found() -> None:
    devices = [AudioDevice(index=0, name="Built-in Microphone", max_input_channels=1, default_samplerate=48000.0, host_api="Core Audio")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        result = check_audio_devices(configured_device_name="Built-in Microphone")
    assert result.status == "pass"


def test_audio_devices_fail_message_is_linux_specific_on_linux() -> None:
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=[]), \
         patch("platform.system", return_value="Linux"):
        result = check_audio_devices()
    assert result.status == "fail"
    assert "audio" in result.message and "macOS" not in result.message


def test_microphone_permission_skips_when_no_devices() -> None:
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=[]):
        result = check_microphone_permission()
    assert result.status == "warn"


def test_microphone_permission_fails_when_stream_open_errors() -> None:
    # e.g. macOS denying the TCC microphone grant surfaces as a PortAudio error here.
    devices = [AudioDevice(index=0, name="Mic", max_input_channels=1, default_samplerate=48000.0, host_api="Core Audio")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices), \
         patch("sounddevice.rec", side_effect=RuntimeError("PaMacCore (AUHAL): Unanticipated host error")):
        result = check_microphone_permission()
    assert result.status == "fail"


def test_microphone_permission_fail_message_is_linux_specific_on_linux() -> None:
    devices = [AudioDevice(index=0, name="Mic", max_input_channels=1, default_samplerate=48000.0, host_api="ALSA")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices), \
         patch("sounddevice.rec", side_effect=RuntimeError("Device unavailable")), \
         patch("platform.system", return_value="Linux"):
        result = check_microphone_permission()
    assert result.status == "fail"
    assert "audio" in result.message.lower() and "TCC" not in result.message


def test_microphone_permission_warns_on_complete_silence() -> None:
    devices = [AudioDevice(index=0, name="Mic", max_input_channels=1, default_samplerate=48000.0, host_api="Core Audio")]
    silent = np.zeros((14400, 1), dtype=np.int16)
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices), \
         patch("sounddevice.rec", return_value=silent), patch("sounddevice.wait"):
        result = check_microphone_permission()
    assert result.status == "warn"


def test_microphone_permission_passes_with_real_signal() -> None:
    devices = [AudioDevice(index=0, name="Mic", max_input_channels=1, default_samplerate=48000.0, host_api="Core Audio")]
    signal = np.full((14400, 1), 500, dtype=np.int16)
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices), \
         patch("sounddevice.rec", return_value=signal), patch("sounddevice.wait"):
        result = check_microphone_permission()
    assert result.status == "pass"


def test_run_all_checks_skips_directory_and_disk_checks_without_data_directory() -> None:
    with patch("birdnetlib.analyzer.Analyzer") as mock_analyzer, \
         patch("backyard_bird.audio.devices.list_input_devices", return_value=[]):
        mock_analyzer.return_value = object()
        results = run_all_checks(data_directory=None)
    names = {r.name for r in results}
    assert "required_directories" not in names
    assert "disk_space" not in names
    assert "python_version" in names
    assert "birdnet" in names
    assert "audio_devices" in names
