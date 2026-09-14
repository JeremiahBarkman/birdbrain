from unittest.mock import patch

from backyard_bird.audio.devices import AudioDevice, find_input_device, list_input_devices


def test_list_input_devices_does_not_raise() -> None:
    # Device availability depends on the host; this only proves the
    # PortAudio binding works and the return shape is right.
    devices = list_input_devices()
    assert isinstance(devices, list)
    for device in devices:
        assert device.max_input_channels > 0
        assert isinstance(device.name, str) and device.name


def _mic(index: int, name: str) -> AudioDevice:
    return AudioDevice(index=index, name=name, max_input_channels=1, default_samplerate=48000.0, host_api="ALSA")


def test_find_input_device_exact_match() -> None:
    devices = [_mic(0, "TONOR G11 USB microphone: Audio (hw:1,0)")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        found = find_input_device("TONOR G11 USB microphone: Audio (hw:1,0)")
    assert found is not None and found.index == 0


def test_find_input_device_falls_back_when_alsa_hw_index_shifted() -> None:
    # The real scenario hit on the Raspberry Pi host profile: a reboot
    # renumbered the ALSA card from hw:1,0 to hw:3,0. The configured
    # name (from before the reboot) should still resolve.
    devices = [_mic(0, "TONOR G11 USB microphone: Audio (hw:3,0)")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        found = find_input_device("TONOR G11 USB microphone: Audio (hw:1,0)")
    assert found is not None and found.index == 0


def test_find_input_device_returns_none_when_truly_absent() -> None:
    devices = [_mic(0, "Built-in Microphone")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        found = find_input_device("USB Mic (hw:1,0)")
    assert found is None


def test_find_input_device_no_fallback_without_hw_suffix() -> None:
    # A configured name with no (hw:N,M) suffix at all (e.g. a macOS
    # CoreAudio name) must not fuzzy-match a different device.
    devices = [_mic(0, "Built-in Microphone"), _mic(1, "USB Mic (hw:1,0)")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        found = find_input_device("Built-in Micro")
    assert found is None


def test_find_input_device_by_numeric_index_unaffected() -> None:
    devices = [_mic(0, "Mic A"), _mic(1, "Mic B (hw:1,0)")]
    with patch("backyard_bird.audio.devices.list_input_devices", return_value=devices):
        found = find_input_device("1")
    assert found is not None and found.name == "Mic B (hw:1,0)"
