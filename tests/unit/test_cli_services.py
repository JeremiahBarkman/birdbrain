"""Tests `bird-display services install/uninstall/status` orchestration
with subprocess and platform.system mocked - never touches a real
systemd/launchd, never needs root. Real installation was validated
manually on the actual Mac and Pi hardware (see README).
"""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from backyard_bird.cli import cli
from backyard_bird.service_install import SERVICE_DEFINITIONS


class _RecordingRun:
    """Fake subprocess.run that records every call and returns a
    configurable result, so tests can assert on exactly what commands
    would have been executed without running them for real.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="active\n", stderr="")


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> _RecordingRun:
    recorder = _RecordingRun()
    monkeypatch.setattr(subprocess, "run", recorder)
    return recorder


def test_services_install_linux_writes_units_and_enables(
    fake_run: _RecordingRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("getpass.getuser", lambda: "jeremiah")

    result = CliRunner().invoke(cli, ["services", "install", "--yes"])

    assert result.exit_code == 0, result.output
    # One `sudo tee` per service, then daemon-reload, then enable --now.
    tee_calls = [c for c in fake_run.calls if c["args"][:2] == ["sudo", "tee"]]
    assert len(tee_calls) == len(SERVICE_DEFINITIONS)
    assert any(c["args"] == ["sudo", "systemctl", "daemon-reload"] for c in fake_run.calls)
    enable_calls = [c for c in fake_run.calls if c["args"][:3] == ["sudo", "systemctl", "enable"]]
    assert len(enable_calls) == 1
    assert "--now" in enable_calls[0]["args"]
    for service in SERVICE_DEFINITIONS:
        assert f"birdbrain-{service.name}.service" in enable_calls[0]["args"]


def test_services_install_linux_aborts_without_confirmation(
    fake_run: _RecordingRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("getpass.getuser", lambda: "jeremiah")

    result = CliRunner().invoke(cli, ["services", "install"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    assert fake_run.calls == []  # nothing executed


def test_services_install_macos_writes_plists_and_bootstraps(
    fake_run: _RecordingRun, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = CliRunner().invoke(cli, ["services", "install", "--yes"])

    assert result.exit_code == 0, result.output
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    written = sorted(p.name for p in agents_dir.glob("*.plist"))
    assert written == sorted(f"com.backyardbird.{s.name}.plist" for s in SERVICE_DEFINITIONS)
    bootstrap_calls = [c for c in fake_run.calls if c["args"][:2] == ["launchctl", "bootstrap"]]
    assert len(bootstrap_calls) == len(SERVICE_DEFINITIONS)


def test_services_uninstall_linux_disables_and_removes(
    fake_run: _RecordingRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    result = CliRunner().invoke(cli, ["services", "uninstall", "--yes"])

    assert result.exit_code == 0, result.output
    disable_calls = [c for c in fake_run.calls if c["args"][:3] == ["sudo", "systemctl", "disable"]]
    assert len(disable_calls) == 1
    rm_calls = [c for c in fake_run.calls if c["args"][:2] == ["sudo", "rm"]]
    assert len(rm_calls) == len(SERVICE_DEFINITIONS)


def test_services_status_reports_not_installed_when_absent(
    fake_run: _RecordingRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    result = CliRunner().invoke(cli, ["services", "status"])

    assert result.exit_code == 0, result.output
    for service in SERVICE_DEFINITIONS:
        assert f"{service.name:15s} not installed" in result.output
