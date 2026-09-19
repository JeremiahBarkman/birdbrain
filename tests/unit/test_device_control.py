from pathlib import Path

from backyard_bird.audio.device_control import read_device_control, write_device_control


def test_write_then_read_device_control_round_trips(tmp_path: Path) -> None:
    control_path = tmp_path / "run" / "mic_device.json"
    write_device_control(control_path, "USB Microphone")

    assert control_path.exists()
    assert read_device_control(control_path, default="fallback") == "USB Microphone"


def test_write_device_control_overwrites_atomically(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_device.json"
    write_device_control(control_path, "Mic A")
    write_device_control(control_path, "Mic B")

    assert read_device_control(control_path, default="fallback") == "Mic B"
    assert list(control_path.parent.glob("mic_device*")) == [control_path]  # no stray .tmp left behind


def test_read_device_control_returns_default_when_file_missing(tmp_path: Path) -> None:
    assert read_device_control(tmp_path / "does_not_exist.json", default="fallback") == "fallback"


def test_read_device_control_returns_default_on_malformed_json(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_device.json"
    control_path.write_text("not json at all")

    assert read_device_control(control_path, default="fallback") == "fallback"


def test_read_device_control_returns_default_when_key_missing(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_device.json"
    control_path.write_text('{"something_else": 1}')

    assert read_device_control(control_path, default="fallback") == "fallback"


def test_read_device_control_returns_default_when_value_is_empty(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_device.json"
    control_path.write_text('{"device_name": ""}')

    assert read_device_control(control_path, default="fallback") == "fallback"


def test_read_device_control_returns_default_when_value_is_not_a_string(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_device.json"
    control_path.write_text('{"device_name": 42}')

    assert read_device_control(control_path, default="fallback") == "fallback"
