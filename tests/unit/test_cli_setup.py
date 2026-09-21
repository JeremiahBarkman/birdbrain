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
    # data_directory is repointed at tmp_path defensively: the example's
    # `./data` is relative to the process's cwd (the repo root, under
    # pytest), not to this config file, so anything that ever resolves
    # it here must not be able to land on the *real* data/ directory —
    # see DEVELOPMENT.md's 2026-09-21 dated note on exactly that
    # happening in a sibling test file. `setup` itself never touches
    # data_directory today, but this fixture shouldn't rely on that
    # staying true.
    dest = tmp_path / "config.yaml"
    shutil.copy(Path("config/config.example.yaml"), dest)
    text = dest.read_text()
    assert "data_directory: ./data" in text  # sanity: config.example.yaml's shape hasn't drifted
    dest.write_text(text.replace("data_directory: ./data", f"data_directory: {tmp_path / 'data'}"))
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
        lambda query, **kwargs: GeocodeResult(39.7817, -89.6501, "Springfield, Illinois, United States"),
    )
    monkeypatch.setattr(
        devices_module, "list_input_devices", lambda: [_mic(0, "TONOR G11 USB microphone: Audio (hw:1,0)")]
    )

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "setup"],
        input="Springfield, IL\ny\ny\n",  # place, confirm location, confirm mic
    )

    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(config_path.read_text())
    assert parsed["location"]["latitude"] == 39.7817
    assert parsed["location"]["longitude"] == -89.6501
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
