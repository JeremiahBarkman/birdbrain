"""Input device discovery and quick microphone testing (macOS/Linux).

This is the only module that should import sounddevice directly, so
the rest of the application isn't coupled to the PortAudio bindings.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd


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
    """Look up a device by exact name match, or by numeric index."""
    devices = list_input_devices()
    if name_or_index.isdigit():
        target_index = int(name_or_index)
        return next((d for d in devices if d.index == target_index), None)
    return next((d for d in devices if d.name == name_or_index), None)


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
