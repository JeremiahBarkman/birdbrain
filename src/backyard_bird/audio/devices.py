"""Input device discovery and quick microphone testing (macOS/Linux).

This is the only module that should import sounddevice directly, so
the rest of the application isn't coupled to the PortAudio bindings.
"""
from __future__ import annotations

import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd

# ALSA/PortAudio bakes the hardware card/device index into the device
# name itself, e.g. "TONOR G11 USB microphone: Audio (hw:3,0)" - and
# that index is NOT stable: it's assigned by USB enumeration order at
# boot, so a reboot or a replug can silently renumber it (confirmed
# directly: hw:1,0 became hw:3,0 across one power cycle on the
# Raspberry Pi host profile, 2026-09-13). macOS's CoreAudio names have
# no equivalent volatile suffix. Stripped out for a fallback match in
# find_input_device so a config written against one boot's index still
# resolves after a reboot renumbers it.
_ALSA_HW_SUFFIX_RE = re.compile(r"\s*\(hw:\d+,\d+\)$")


def _strip_alsa_hw_suffix(name: str) -> str:
    return _ALSA_HW_SUFFIX_RE.sub("", name)


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    default_samplerate: float
    host_api: str


def list_input_devices() -> list[AudioDevice]:
    """Return every device PortAudio reports with at least one input channel."""
    devices = sd.query_devices()
    host_apis = sd.query_hostapis()
    result: list[AudioDevice] = []
    for index, device in enumerate(devices):
        if device["max_input_channels"] > 0:
            result.append(
                AudioDevice(
                    index=index,
                    name=device["name"],
                    max_input_channels=device["max_input_channels"],
                    default_samplerate=device["default_samplerate"],
                    host_api=host_apis[device["hostapi"]]["name"],
                )
            )
    return result


def find_input_device(name_or_index: str) -> AudioDevice | None:
    """Look up a device by exact name match, or by numeric index.

    Falls back to matching with any trailing ALSA "(hw:N,M)" suffix
    stripped from both sides if the exact match fails - see the module
    docstring above on why that suffix isn't stable across reboots.
    A configured name with no such suffix (or on macOS, where it never
    appears) behaves exactly as before: exact match only.
    """
    devices = list_input_devices()
    if name_or_index.isdigit():
        target_index = int(name_or_index)
        return next((d for d in devices if d.index == target_index), None)

    exact = next((d for d in devices if d.name == name_or_index), None)
    if exact is not None:
        return exact

    target_stripped = _strip_alsa_hw_suffix(name_or_index)
    if target_stripped == name_or_index:
        return None  # configured name had no (hw:N,M) suffix to begin with
    return next((d for d in devices if _strip_alsa_hw_suffix(d.name) == target_stripped), None)


def record_test_clip(
    device: str | int | None,
    duration_seconds: float,
    sample_rate: int,
    channels: int,
    output_path: Path,
) -> Path:
    """Record a short clip synchronously and write it as 16-bit PCM WAV.

    Intended for manual microphone verification (`bird-display audio
    test`) — not for continuous capture. See §29 Phase 2 for the
    durable, restart-safe capture service.
    """
    frame_count = int(duration_seconds * sample_rate)

    resolved_device: str | int | None = device
    if isinstance(device, str) and device.strip():
        found = find_input_device(device)
        resolved_device = found.index if found is not None else device

    recording = sd.rec(
        frame_count,
        samplerate=sample_rate,
        channels=channels,
        dtype="int16",
        device=resolved_device,
    )
    sd.wait()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(np.asarray(recording, dtype=np.int16).tobytes())

    return output_path
