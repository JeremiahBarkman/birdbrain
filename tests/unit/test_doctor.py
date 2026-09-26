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
    check_database_access,
    check_disk_space,
    check_frame_configuration,
    check_image_providers,
    check_microphone_permission,
    check_network_access,
    check_platform,
    check_python_version,
    check_required_directories,
    check_service_autostart,
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
    # Explicitly pinned to Darwin: on real Linux hardware with capture
    # actually running (confirmed live on the Pi - this exact test
    # failed there once auto-start was genuinely installed), the
    # unpinned version of this test silently depended on the real host
    # OS/process state rather than testing a deterministic scenario.
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=[]), \
         patch("platform.system", return_value="Darwin"):
        result = check_audio_devices()
    assert result.status == "fail"


def test_audio_devices_warns_not_fails_when_capture_already_running_on_linux() -> None:
    # Real scenario found live: once auto-start is enabled (§29 Phase
    # 8), capture_run holds the mic open permanently, and ALSA's raw
    # hw:N,M nodes make that look identical to "no mic" to any other
    # process. This must never be a FAIL - doctor would then always
    # fail in the normal, healthy, auto-start-enabled state.
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=[]), \
         patch("platform.system", return_value="Linux"), \
         patch("backyard_bird.doctor._capture_process_is_running", return_value=True):
        result = check_audio_devices()
    assert result.status == "warn"
    assert "capture run" in result.message


def test_audio_devices_still_fails_on_linux_when_capture_not_running() -> None:
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=[]), \
         patch("platform.system", return_value="Linux"), \
         patch("backyard_bird.doctor._capture_process_is_running", return_value=False):
        result = check_audio_devices()
    assert result.status == "fail"


def test_capture_process_is_running_reflects_pgrep_exit_code() -> None:
    from backyard_bird.doctor import _capture_process_is_running

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        assert _capture_process_is_running() is True

        mock_run.return_value.returncode = 1
        assert _capture_process_is_running() is False


def test_capture_process_is_running_defaults_false_if_pgrep_missing() -> None:
    from backyard_bird.doctor import _capture_process_is_running

    with patch("subprocess.run", side_effect=FileNotFoundError("pgrep not found")):
        assert _capture_process_is_running() is False


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
    # _capture_process_is_running is explicitly pinned False - without
    # it, this test's result silently depends on whether a real
    # `bird-display capture run` process happens to be running on
    # whatever machine runs the suite (confirmed live: this failed on
    # the Pi once auto-start made that genuinely, correctly true).
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=[]), \
         patch("platform.system", return_value="Linux"), \
         patch("backyard_bird.doctor._capture_process_is_running", return_value=False):
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
        # check_network=False: run_all_checks would otherwise make two
        # real outbound HTTPS requests from a unit test.
        results = run_all_checks(data_directory=None, check_network=False)
    names = {r.name for r in results}
    assert "required_directories" not in names
    assert "disk_space" not in names
    assert "database_access" not in names
    assert "python_version" in names
    assert "birdnet" in names
    assert "audio_devices" in names
    assert "service_autostart" in names


def test_service_autostart_warns_when_nothing_installed(tmp_path: Path) -> None:
    with patch("platform.system", return_value="Darwin"), patch("pathlib.Path.home", return_value=tmp_path):
        result = check_service_autostart()
    assert result.status == "warn"
    assert "services install" in result.message


def test_service_autostart_passes_when_all_installed_linux(tmp_path: Path) -> None:
    from backyard_bird.service_install import SERVICE_DEFINITIONS, systemd_unit_filename

    systemd_dir = tmp_path / "etc" / "systemd" / "system"
    systemd_dir.mkdir(parents=True)
    for service in SERVICE_DEFINITIONS:
        (systemd_dir / systemd_unit_filename(service)).touch()

    with patch("platform.system", return_value="Linux"):
        # Patch the exact call site (Path("/etc/systemd/system", ...)) by
        # redirecting Path.exists to check under tmp_path instead of the
        # real filesystem root - avoids needing real root-owned files.
        real_exists = Path.exists

        def fake_exists(self: Path) -> bool:
            if str(self).startswith("/etc/systemd/system"):
                return (systemd_dir / self.name).exists()
            return real_exists(self)

        with patch.object(Path, "exists", fake_exists):
            result = check_service_autostart()

    assert result.status == "pass"
    assert f"All {len(SERVICE_DEFINITIONS)}" in result.message


def test_service_autostart_warns_when_partially_installed_linux() -> None:
    from backyard_bird.service_install import SERVICE_DEFINITIONS, systemd_unit_filename

    only_first = systemd_unit_filename(SERVICE_DEFINITIONS[0])
    real_exists = Path.exists

    def fake_exists(self: Path) -> bool:
        if str(self).startswith("/etc/systemd/system"):
            return self.name == only_first
        return real_exists(self)

    with patch("platform.system", return_value="Linux"), patch.object(Path, "exists", fake_exists):
        result = check_service_autostart()

    assert result.status == "warn"
    assert "1/" in result.message


def test_service_autostart_unsupported_os_warns() -> None:
    with patch("platform.system", return_value="Windows"):
        result = check_service_autostart()
    assert result.status == "warn"


# -- database access (§25) ----------------------------------------------------


def test_database_access_warns_when_no_database_exists_yet(tmp_path: Path) -> None:
    result = check_database_access(tmp_path)
    assert result.status == "warn"
    assert "database migrate" in result.message


def test_database_access_passes_on_a_fully_migrated_database(tmp_path: Path) -> None:
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations
    from backyard_bird.layout import DataLayout

    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    db_path = DataLayout.under(tmp_path).database_path
    db_path.parent.mkdir(parents=True)
    conn = get_connection(db_path)
    apply_migrations(conn, migrations_dir)
    conn.close()

    result = check_database_access(tmp_path, migrations_dir)
    assert result.status == "pass"
    assert "up to date" in result.message


def test_database_access_warns_about_pending_migrations(tmp_path: Path) -> None:
    """The real failure this check exists for: a database that opens
    fine but is missing a migration, so features 500 at runtime
    (migration 005, found live 2026-09-21)."""
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations
    from backyard_bird.layout import DataLayout

    real_migrations = Path(__file__).resolve().parents[2] / "migrations"
    partial_dir = tmp_path / "partial_migrations"
    partial_dir.mkdir()
    everything = sorted(real_migrations.glob("*.sql"))
    for path in everything[:-1]:
        (partial_dir / path.name).write_text(path.read_text())

    db_path = DataLayout.under(tmp_path).database_path
    db_path.parent.mkdir(parents=True)
    conn = get_connection(db_path)
    apply_migrations(conn, partial_dir)
    conn.close()

    result = check_database_access(tmp_path, real_migrations)
    assert result.status == "warn"
    assert "1 migration(s) not applied" in result.message


def test_database_access_fails_on_a_corrupt_database(tmp_path: Path) -> None:
    from backyard_bird.layout import DataLayout

    db_path = DataLayout.under(tmp_path).database_path
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"this is definitely not a SQLite file")

    result = check_database_access(tmp_path)
    assert result.status == "fail"


# -- image providers (§25) ----------------------------------------------------


def test_image_providers_passes_on_the_default_configuration() -> None:
    result = check_image_providers(["wikimedia_commons", "inaturalist"])
    assert result.status == "pass"


def test_image_providers_warns_about_an_unimplemented_source() -> None:
    """build_providers() drops unknown names silently on its way to the
    Wikimedia fallback — this check is the only thing that says so."""
    result = check_image_providers(["wikimedia_commons", "flickr"])
    assert result.status == "warn"
    assert "flickr" in result.message


def test_image_providers_warns_when_every_source_is_unimplemented() -> None:
    result = check_image_providers(["flickr"])
    assert result.status == "warn"
    assert "wikimedia_commons" in result.message


def test_image_providers_warns_on_an_empty_source_list() -> None:
    result = check_image_providers([])
    assert result.status == "warn"


def test_image_providers_skipped_without_config() -> None:
    result = check_image_providers(None)
    assert result.status == "warn"
    assert "Skipped" in result.message


# -- frame configuration (§25) ------------------------------------------------


def test_frame_configuration_passes_when_the_export_directory_is_writable(tmp_path: Path) -> None:
    from backyard_bird.config import PhotoFrameConfig

    config = PhotoFrameConfig(export_directory=tmp_path / "frame-export" / "current")
    result = check_frame_configuration(config)
    assert result.status == "pass"
    assert "local_export" in result.message


def test_frame_configuration_warns_on_an_unimplemented_adapter(tmp_path: Path) -> None:
    """`unconfigured` is a legitimate state (no Uhale API exists, §30
    rule 24) — warn, never fail (rule 12)."""
    from backyard_bird.config import PhotoFrameConfig

    config = PhotoFrameConfig(adapter="uhale_web", export_directory=tmp_path / "current")
    result = check_frame_configuration(config)
    assert result.status == "warn"


def test_frame_configuration_skipped_without_config() -> None:
    result = check_frame_configuration(None)
    assert result.status == "warn"
    assert "Skipped" in result.message


# -- network access (§25) -----------------------------------------------------


def test_network_access_warns_rather_than_fails_when_offline() -> None:
    """Rule 11: capture never needs the internet, so an offline Pi is a
    working bird detector and doctor must not exit non-zero over it."""
    with patch("urllib.request.urlopen", side_effect=OSError("no route to host")):
        result = check_network_access()
    assert result.status == "warn"
    assert "Audio capture and BirdNET are unaffected" in result.message


def test_network_access_passes_when_providers_are_reachable() -> None:
    from contextlib import contextmanager

    @contextmanager
    def fake_urlopen(*args, **kwargs):
        yield object()

    with patch("urllib.request.urlopen", fake_urlopen):
        result = check_network_access()
    assert result.status == "pass"


def test_network_access_warns_when_only_one_provider_is_reachable() -> None:
    from contextlib import contextmanager

    calls = []

    @contextmanager
    def flaky_urlopen(request, *args, **kwargs):
        calls.append(request)
        if len(calls) > 1:
            raise OSError("timed out")
        yield object()

    with patch("urllib.request.urlopen", flaky_urlopen):
        result = check_network_access()
    assert result.status == "warn"
    assert "Not reachable" in result.message


def test_run_all_checks_includes_every_section_25_item(tmp_path: Path) -> None:
    """§25's list, as one assertion — so an item can't quietly go
    missing again the way database access did."""
    from backyard_bird.config import PhotoFrameConfig

    with patch("birdnetlib.analyzer.Analyzer") as mock_analyzer, \
         patch("backyard_bird.audio.devices.list_input_devices", return_value=[]), \
         patch("urllib.request.urlopen", side_effect=OSError("offline")):
        mock_analyzer.return_value = object()
        results = run_all_checks(
            data_directory=tmp_path,
            preferred_image_sources=["wikimedia_commons"],
            photo_frame_config=PhotoFrameConfig(export_directory=tmp_path / "current"),
        )
    names = {r.name for r in results}
    assert {
        "python_version",
        "birdnet",
        "audio_devices",
        "microphone_permission",
        "database_access",
        "required_directories",
        "disk_space",
        "image_providers",
        "frame_configuration",
        "network_access",
        "service_autostart",
    } <= names
