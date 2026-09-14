"""Tests the pure template-rendering half of auto-start support —
no root, no real systemd/launchd, no subprocess involved. The
orchestration half (writing files, calling systemctl/launchctl) is
covered in tests/unit/test_cli_services.py with subprocess mocked.
"""
from __future__ import annotations

import plistlib
from pathlib import Path

from backyard_bird.service_install import (
    SERVICE_DEFINITIONS,
    ServiceDefinition,
    launchd_label,
    launchd_plist_filename,
    render_launchd_plist,
    render_systemd_unit,
    systemd_unit_filename,
)

CAPTURE = ServiceDefinition("capture", "continuous audio capture", ("capture", "run"))


def test_service_definitions_cover_all_four_services() -> None:
    names = {s.name for s in SERVICE_DEFINITIONS}
    assert names == {"capture", "analyzer", "images-watch", "dashboard"}


def test_systemd_unit_filename() -> None:
    assert systemd_unit_filename(CAPTURE) == "birdbrain-capture.service"


def test_launchd_label_and_filename() -> None:
    assert launchd_label(CAPTURE) == "com.backyardbird.capture"
    assert launchd_plist_filename(CAPTURE) == "com.backyardbird.capture.plist"


def test_render_systemd_unit_contains_correct_exec_and_user() -> None:
    unit = render_systemd_unit(
        CAPTURE, repo_dir=Path("/home/jeremiah/birdbrain"), venv_bin=Path("/home/jeremiah/birdbrain/.venv/bin"), user="jeremiah"
    )
    assert "ExecStart=/home/jeremiah/birdbrain/.venv/bin/bird-display capture run" in unit
    assert "User=jeremiah" in unit
    assert "WorkingDirectory=/home/jeremiah/birdbrain" in unit
    assert "Restart=on-failure" in unit
    assert "[Unit]" in unit and "[Service]" in unit and "[Install]" in unit


def test_render_systemd_unit_is_per_service_independent() -> None:
    # §20.1: one crashing service must not affect another - each is
    # its own unit with its own Restart=, not a shared unit.
    for service in SERVICE_DEFINITIONS:
        unit = render_systemd_unit(service, repo_dir=Path("/repo"), venv_bin=Path("/repo/.venv/bin"), user="pi")
        assert f"ExecStart=/repo/.venv/bin/bird-display {' '.join(service.cli_args)}" in unit


def test_render_launchd_plist_round_trips_via_plistlib() -> None:
    raw = render_launchd_plist(CAPTURE, repo_dir=Path("/Users/jeremiah/birdbrain"), venv_bin=Path("/Users/jeremiah/birdbrain/.venv/bin"))
    parsed = plistlib.loads(raw)

    assert parsed["Label"] == "com.backyardbird.capture"
    assert parsed["ProgramArguments"] == ["/Users/jeremiah/birdbrain/.venv/bin/bird-display", "capture", "run"]
    assert parsed["WorkingDirectory"] == "/Users/jeremiah/birdbrain"
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True
    assert parsed["StandardOutPath"].endswith("data/logs/capture.launchd.out.log")
    assert parsed["StandardErrorPath"].endswith("data/logs/capture.launchd.err.log")


def test_render_launchd_plist_all_services_have_two_program_arguments() -> None:
    # Every SERVICE_DEFINITIONS entry has exactly a 2-word CLI command
    # (e.g. "capture run", "images watch") - a real assumption the
    # renderer depends on; this pins it down explicitly.
    for service in SERVICE_DEFINITIONS:
        assert len(service.cli_args) == 2
        raw = render_launchd_plist(service, repo_dir=Path("/repo"), venv_bin=Path("/repo/.venv/bin"))
        parsed = plistlib.loads(raw)
        assert parsed["ProgramArguments"][1:] == list(service.cli_args)
