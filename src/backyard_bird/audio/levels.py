"""Live microphone level/status reporting for the dashboard (user
request, added 2026-09-14): computes a peak level from each captured
chunk and writes it to a small status file, updated roughly once a
second by capture_service.py, for the dashboard (a separate process)
to poll at the same cadence.

Deliberately a file, not the `service_health` table the requirements
doc describes (§12.10): that table doesn't actually exist yet (no
migration ever created it — heartbeats are a still-unbuilt §20.2
concern), and even if it did, it's designed for once-a-minute-per-
service health snapshots, not sub-second live level data from one
specific service. A plain JSON file, written atomically the same way
segment files are (§8.4: write to a temp path, rename), is simpler and
avoids adding 1x/second write traffic to the SQLite database the
analyzer is also writing to.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_SILENCE_DBFS = -60.0
_FULL_SCALE_INT16 = 32768.0

# How old a status file can be before the dashboard should treat it as
# stale (capture crashed, was killed, or never started) rather than a
# live reading — a few seconds of slack past the ~1x/second write
# cadence, not a tight deadline.
STALE_AFTER_SECONDS = 5.0


@dataclass(frozen=True)
class MicLevel:
    peak_percent: float
    peak_dbfs: float


def compute_level(chunk: np.ndarray) -> MicLevel:
    """Peak (not RMS) amplitude of one raw int16 audio chunk, as both
    a 0-100 linear percent (sizes a UI meter bar directly) and dBFS
    (floored at -60 for a stable "silence" reading instead of -inf).
    """
    if chunk.size == 0:
        return MicLevel(peak_percent=0.0, peak_dbfs=_SILENCE_DBFS)
    # int32, not abs(int16) directly: -32768 is a valid, real int16
    # sample (silence-adjacent audio can hit it), and +32768 isn't
    # representable in int16 — abs() on the raw dtype silently
    # overflows (two's complement wraps it) instead of raising.
    peak = float(np.abs(chunk.astype(np.int32)).max())
    peak_percent = min(100.0, (peak / _FULL_SCALE_INT16) * 100.0)
    if peak <= 0:
        peak_dbfs = _SILENCE_DBFS
    else:
        peak_dbfs = max(_SILENCE_DBFS, 20.0 * float(np.log10(peak / _FULL_SCALE_INT16)))
    return MicLevel(peak_percent=peak_percent, peak_dbfs=peak_dbfs)


def write_mic_status(
    status_path: Path,
    *,
    device_name: str,
    status: str,
    level: MicLevel | None = None,
    error_message: str | None = None,
) -> None:
    """Atomic write (temp file + rename, same pattern as §8.4's
    segment files) so the dashboard never reads a half-written file.
    `status` is a short machine-readable word ("capturing", "stopped",
    "error") — free-form rather than an enum since this is a status
    display, not a state machine anything else drives.
    """
    payload = {
        "device_name": device_name,
        "status": status,
        "peak_percent": level.peak_percent if level else None,
        "peak_dbfs": level.peak_dbfs if level else None,
        "error_message": error_message,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload))
    tmp_path.replace(status_path)  # atomic on the same filesystem


def read_mic_status(status_path: Path) -> dict | None:
    """The parsed status dict, or None if the file doesn't exist, is
    malformed, or is stale (older than STALE_AFTER_SECONDS — capture
    likely crashed, was killed, or never started). Never raises: a
    status display reading a corrupt/missing file should show
    "unknown," not 500 the whole dashboard.
    """
    try:
        payload = json.loads(status_path.read_text())
        updated_at = datetime.fromisoformat(payload["updated_at_utc"])
        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age_seconds > STALE_AFTER_SECONDS:
            return None
        return payload
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
