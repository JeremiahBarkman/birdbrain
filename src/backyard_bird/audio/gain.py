"""Runtime-adjustable capture gain (user request): the microphone's
input level runs quite low by default, with no way to compensate short
of physically swapping hardware. This applies a linear multiplier in
software to every captured chunk, at the single point in
capture_service.py all downstream consumers already flow through — so
raising it improves the live level meter, "Listen Live," and the
actual audio BirdNET analyzes, all at once, rather than just one of
them.

Adjustable from the dashboard without restarting capture: the current
value lives in a small JSON control file, the same atomic-write,
poll-to-read pattern audio/levels.py already uses for mic_status.json
- just the other direction (dashboard -> capture instead of capture ->
dashboard), since the two are always separate OS processes (see
live_monitor.py's docstring for why capture and the dashboard can never
be the same process).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

GAIN_MIN = 0.25
GAIN_MAX = 8.0
DEFAULT_GAIN = 1.0


def clamp_gain(gain: float) -> float:
    return max(GAIN_MIN, min(GAIN_MAX, gain))


def write_gain_control(control_path: Path, gain: float) -> float:
    """Writes the clamped gain atomically (temp file + rename, §8.4's
    pattern) and returns the value actually applied, so a caller (the
    dashboard route) can report back what took effect rather than
    assuming the requested value was in range.
    """
    clamped = clamp_gain(gain)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = control_path.with_suffix(control_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"gain": clamped}))
    tmp_path.replace(control_path)
    return clamped


def read_gain_control(control_path: Path, default: float) -> float:
    """The current gain, or `default` if the control file doesn't
    exist yet, is malformed, or is unreadable — never raises, since a
    gain read must never be able to interrupt capture itself (the same
    principle read_mic_status already follows for the reverse
    direction).
    """
    try:
        payload = json.loads(control_path.read_text())
        return clamp_gain(float(payload["gain"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return default


def apply_gain(chunk: np.ndarray, gain: float) -> np.ndarray:
    """Multiplies every sample by `gain` and clips back into int16
    range — boosting a quiet signal without clipping would otherwise
    wrap around (two's-complement overflow) instead, which sounds far
    worse than ordinary clipping does. Skips the float round-trip
    entirely at gain == 1.0, the common/default case.
    """
    if gain == 1.0:
        return chunk
    boosted = chunk.astype(np.float32) * gain
    return np.clip(boosted, -32768, 32767).astype(np.int16)
