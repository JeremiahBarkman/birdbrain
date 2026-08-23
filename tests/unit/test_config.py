from pathlib import Path

import pytest

from backyard_bird.config import AppConfig, ConfigError, load_config

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"


def test_example_config_is_valid() -> None:
    config = load_config(EXAMPLE_CONFIG)
    assert isinstance(config, AppConfig)
    assert config.audio.sample_rate == 48000
    assert config.birdnet.database_minimum_confidence == 0.60
    assert config.photo_frame.model == "WF1561"
    assert config.dashboard.host == "127.0.0.1"  # localhost-only by default, per §23.3
    assert config.dashboard.port == 8765


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_invalid_latitude_rejected(tmp_path: Path) -> None:
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        """
location:
  latitude: 200
  longitude: -123.0
audio:
  device_name: Test Mic
  microphone_id: test-mic
"""
    )
    with pytest.raises(ConfigError):
        load_config(bad_config)


def test_missing_required_field_rejected(tmp_path: Path) -> None:
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        """
location:
  latitude: 45.0
  longitude: -123.0
"""
    )
    with pytest.raises(ConfigError):
        load_config(bad_config)
