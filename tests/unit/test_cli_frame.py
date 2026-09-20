"""Tests `bird-display frame test`/`frame inspect` end to end through
the real CLI, with the config file itself giving each test its own
export_directory under tmp_path — deliberately not config.example.yaml
(its photo_frame.export_directory is the relative "./data/frame-export/
current" real installs actually use, which would write into whatever
directory the test process happens to be run from otherwise).
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from backyard_bird.cli import cli


def _write_config(config_path: Path, export_directory: Path, adapter: str = "local_export") -> None:
    config_path.write_text(
        "location:\n"
        "  latitude: 45.0\n"
        "  longitude: -123.0\n"
        "audio:\n"
        "  device_name: Fake Mic\n"
        "  microphone_id: mic-01\n"
        "photo_frame:\n"
        f"  adapter: {adapter}\n"
        f"  export_directory: {export_directory}\n"
    )


def test_frame_test_succeeds_for_a_writable_local_export_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path / "frame-export" / "current")

    result = CliRunner().invoke(cli, ["--config", str(config_path), "frame", "test"])

    assert result.exit_code == 0, result.output
    assert "writable" in result.output


def test_frame_test_fails_for_an_unknown_adapter(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path / "frame-export" / "current", adapter="uhale_web")

    result = CliRunner().invoke(cli, ["--config", str(config_path), "frame", "test"])

    assert result.exit_code == 1
    assert "uhale_web" in result.output


def test_frame_inspect_reports_no_export_yet(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path / "frame-export" / "current")

    result = CliRunner().invoke(cli, ["--config", str(config_path), "frame", "inspect"])

    assert result.exit_code == 0, result.output
    assert "local_export" in result.output
    assert "No export yet" in result.output
