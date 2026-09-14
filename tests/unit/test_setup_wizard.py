"""Tests the setup wizard's two testable-without-a-TTY pieces:
geocoding (against a canned response shaped like the real Nominatim
API) and config.yaml editing. The interactive prompt flow itself is
covered separately in tests/unit/test_cli_setup.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import requests
import yaml

from backyard_bird.setup_wizard import geocode_location, set_config_value

NOMINATIM_RESPONSE = [
    {
        "lat": "39.7817",
        "lon": "-89.6501",
        "display_name": "Springfield, Sangamon County, Illinois, United States",
    }
]


class _FakeResponse:
    def __init__(self, json_data, status: int = 200) -> None:
        self._json_data = json_data
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self._status}")

    def json(self):
        return self._json_data


class _FakeSession:
    def __init__(self, response) -> None:
        self._response = response
        self.last_request: dict | None = None

    def get(self, url, *, params, headers, timeout):
        self.last_request = {"url": url, "params": params, "headers": headers, "timeout": timeout}
        return self._response


class _RaisingSession:
    def get(self, *args, **kwargs):
        raise requests.exceptions.ConnectionError("network unreachable")


def test_geocode_location_returns_result_on_success() -> None:
    session = _FakeSession(_FakeResponse(NOMINATIM_RESPONSE))

    result = geocode_location("Springfield, IL", session=session)

    assert result is not None
    assert result.latitude == 39.7817
    assert result.longitude == -89.6501
    assert "Springfield" in result.display_name


def test_geocode_location_sends_a_descriptive_user_agent() -> None:
    # Nominatim's usage policy requires this - a missing one was a real
    # silent-403 bug this project already hit once with Wikimedia.
    session = _FakeSession(_FakeResponse(NOMINATIM_RESPONSE))

    geocode_location("Springfield, IL", session=session)

    assert "User-Agent" in session.last_request["headers"]
    assert session.last_request["headers"]["User-Agent"]


def test_geocode_location_returns_none_when_no_results() -> None:
    session = _FakeSession(_FakeResponse([]))
    assert geocode_location("someplace that does not exist", session=session) is None


def test_geocode_location_returns_none_on_http_error() -> None:
    session = _FakeSession(_FakeResponse({}, status=503))
    assert geocode_location("Springfield, IL", session=session) is None


def test_geocode_location_returns_none_on_network_error() -> None:
    assert geocode_location("Springfield, IL", session=_RaisingSession()) is None


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "system:\n"
        "  timezone: America/Los_Angeles\n"
        "\n"
        "location:\n"
        "  # a comment that must survive\n"
        "  latitude: 45.0\n"
        "  longitude: -123.0\n"
        "\n"
        "audio:\n"
        "  device_name: Outdoor USB Microphone\n"
        "  microphone_id: backyard-mic-01\n"
    )
    return path


def test_set_config_value_replaces_target_line_only(config_file: Path) -> None:
    ok = set_config_value(config_file, "location", "latitude", 39.7817)
    assert ok is True

    text = config_file.read_text()
    assert "latitude: 39.7817" in text
    assert "longitude: -123.0" in text  # untouched
    assert "# a comment that must survive" in text  # comments preserved
    assert "timezone: America/Los_Angeles" in text  # other sections untouched


def test_set_config_value_result_is_valid_yaml_and_round_trips(config_file: Path) -> None:
    set_config_value(config_file, "location", "latitude", 39.7817)
    set_config_value(config_file, "location", "longitude", -89.6501)

    parsed = yaml.safe_load(config_file.read_text())
    assert parsed["location"]["latitude"] == 39.7817
    assert parsed["location"]["longitude"] == -89.6501


def test_set_config_value_quotes_a_value_containing_a_colon(config_file: Path) -> None:
    # The exact real bug this project hit on the Raspberry Pi: an ALSA
    # device name containing ": " breaks YAML if written unquoted.
    tricky_name = "TONOR G11 USB microphone: Audio (hw:1,0)"

    ok = set_config_value(config_file, "audio", "device_name", tricky_name)
    assert ok is True

    parsed = yaml.safe_load(config_file.read_text())
    assert parsed["audio"]["device_name"] == tricky_name
    assert parsed["audio"]["microphone_id"] == "backyard-mic-01"  # untouched


def test_set_config_value_returns_false_for_unknown_section(config_file: Path) -> None:
    original = config_file.read_text()
    ok = set_config_value(config_file, "does_not_exist", "latitude", 1.0)
    assert ok is False
    assert config_file.read_text() == original  # nothing written on failure


def test_set_config_value_returns_false_for_unknown_key(config_file: Path) -> None:
    original = config_file.read_text()
    ok = set_config_value(config_file, "location", "not_a_real_key", 1.0)
    assert ok is False
    assert config_file.read_text() == original
