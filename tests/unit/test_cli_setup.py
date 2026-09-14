"""Tests the interactive `bird-display setup` command's prompt flow.
geocode_location and list_input_devices are monkeypatched (fake
network/hardware); tests/unit/test_setup_wizard.py covers their real
logic directly.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import backyard_bird.audio.devices as devices_module
import backyard_bird.setup_wizard as setup_wizard_module
from backyard_bird.audio.devices import AudioDevice
from backyard_bird.cli import cli
from backyard_bird.setup_wizard import GeocodeResult


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    # Copying the tracked example (not hand-writing a minimal one) keeps
    # this from drifting out of sync with config.yaml's required fields.
    dest = tmp_path / "config.yaml"
    shutil.copy(Path("config/config.example.yaml"), dest)
    return dest


def _mic(index: int, name: str) -> AudioDevice:
    return AudioDevice(index=index, name=name, max_input_channels=1, default_samplerate=48000.0, host_api="ALSA")


def test_setup_creates_config_from_example_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing_path = tmp_path / "config.yaml"
    shutil.copy(Path("config/config.example.yaml"), tmp_path / "config.example.yaml")
    monkeypatch.setattr(devices_module, "list_input_devices", lambda: [])

    result = CliRunner().invoke(cli, ["--config", str(missing_path), "setup"], input="\n")

    assert result.exit_code == 0, result.output
    assert missing_path.exists()
    assert "Created" in result.output


def test_setup_happy_path_saves_location_and_single_mic(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_wizard_module,
        "geocode_location",
        lambda query, **kwargs: GeocodeResult(45.2853, -123.1998, "Carlton, Oregon, United States"),
    )
    monkeypatch.setattr(
        devices_module, "list_input_devices", lambda: [_mic(0, "TONOR G11 USB microphone: Audio (hw:1,0)")]
    )

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "setup"],
        input="Carlton, OR\ny\ny\n",  # place, confirm location, confirm mic
    )

    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(config_path.read_text())
    assert parsed["location"]["latitude"] == 45.2853
    assert parsed["location"]["longitude"] == -123.1998
    assert parsed["audio"]["device_name"] == "TONOR G11 USB microphone: Audio (hw:1,0)"
    # untouched fields survive
    assert parsed["audio"]["microphone_id"] == "backyard-mic-01"


def test_setup_skips_location_and_mic_on_blank_input(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(devices_module, "list_input_devices", lambda: [_mic(0, "Some Mic")])
    original = config_path.read_text()

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "setup"],
        input="\nn\n",  # skip location prompt, decline the single mic
    )

    assert result.exit_code == 0, result.output
    assert config_path.read_text() == original  # nothing changed


def test_setup_geocode_failure_then_retry_then_skip(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard_module, "geocode_location", lambda query, **kwargs: None)
    monkeypatch.setattr(devices_module, "list_input_devices", lambda: [])

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "setup"],
        input="nowhere\nn\n",  # place (fails), decline retry
    )

    assert result.exit_code == 0, result.output
    assert "Couldn't find" in result.output


def test_setup_multiple_devices_picks_chosen_index(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        devices_module,
        "list_input_devices",
        lambda: [_mic(0, "Built-in Microphone"), _mic(1, "USB Mic (hw:1,0)")],
    )

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "setup"],
        input="\n1\n",  # skip location, pick device index 1
    )

    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(config_path.read_text())
    assert parsed["audio"]["device_name"] == "USB Mic (hw:1,0)"
