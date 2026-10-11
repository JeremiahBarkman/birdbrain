"""Tests scripts/uninstall.sh's removal of boot-time auto-start services.

Runs the real script against a throwaway copy of the repo layout, with
`uname`/`launchctl` stubbed on PATH to simulate macOS and $HOME pointed
at a temp dir — so nothing on the host is touched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from backyard_bird.service_install import SERVICE_DEFINITIONS, launchd_plist_filename

REPO_DIR = Path(__file__).resolve().parents[2]


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n")
    stub.chmod(0o755)


def _run_uninstall_as_macos(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO_DIR / "scripts" / "uninstall.sh", repo / "scripts" / "uninstall.sh")
    (repo / ".venv").mkdir()

    home = tmp_path / "home"
    agents_dir = home / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True)
    for service in SERVICE_DEFINITIONS:
        (agents_dir / launchd_plist_filename(service)).write_text("<plist/>")
    (agents_dir / "com.example.unrelated.plist").write_text("<plist/>")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl_log = tmp_path / "launchctl.log"
    _write_stub(bin_dir, "uname", "echo Darwin")
    _write_stub(bin_dir, "launchctl", f'echo "$*" >> "{launchctl_log}"')

    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "uninstall.sh")],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return result, agents_dir, launchctl_log


def test_uninstall_removes_launch_agents_on_macos(tmp_path: Path) -> None:
    result, agents_dir, launchctl_log = _run_uninstall_as_macos(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    for service in SERVICE_DEFINITIONS:
        assert not (agents_dir / launchd_plist_filename(service)).exists()
    assert (agents_dir / "com.example.unrelated.plist").exists()

    bootouts = launchctl_log.read_text().splitlines()
    assert len(bootouts) == len(SERVICE_DEFINITIONS)
    assert all(line.startswith("bootout gui/") for line in bootouts)
    assert not (tmp_path / "repo" / ".venv").exists()


def test_uninstall_with_no_launch_agents_is_a_no_op(tmp_path: Path) -> None:
    _run_uninstall_as_macos(tmp_path)
    # Second run: agents and .venv already gone.
    result = subprocess.run(
        ["bash", str(tmp_path / "repo" / "scripts" / "uninstall.sh")],
        env={**os.environ, "HOME": str(tmp_path / "home"), "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LaunchAgent" not in result.stdout
    assert ".venv/ not found" in result.stdout
