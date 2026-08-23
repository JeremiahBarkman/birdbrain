from backyard_bird.audio.devices import list_input_devices


def test_list_input_devices_does_not_raise() -> None:
    # Device availability depends on the host; this only proves the
    # PortAudio binding works and the return shape is right.
    devices = list_input_devices()
    assert isinstance(devices, list)
    for device in devices:
        assert device.max_input_channels > 0
        assert isinstance(device.name, str) and device.name
