"""Runtime-adjustable capture device selection (user request: "in case
there is more than one mic"). Same file-based, atomic-write,
poll-to-read pattern as audio/gain.py — dashboard -> capture, since the
two are always separate OS processes (see live_monitor.py's docstring
for why capture and the dashboard can never be the same process).

Switching devices doesn't need to tear down and restart the whole
capture_service process: capture_service.py's existing periodic
device-presence check (originally built to notice an unplugged mic and
trigger a reconnect) also now notices a dashboard-requested device
change the same way, and reconnects onto the new device through the
same code path it already reconnects after a real disconnect through —
just without the backoff delay or "error" status a real disconnect
gets, since a deliberate switch isn't a failure.
"""
from __future__ import annotations

import json
from pathlib import Path


def write_device_control(control_path: Path, device_name: str) -> None:
    control_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = control_path.with_suffix(control_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"device_name": device_name}))
    tmp_path.replace(control_path)


def read_device_control(control_path: Path, default: str) -> str:
    """The current desired device name, or `default` if the control
    file doesn't exist yet, is malformed, or is unreadable — never
    raises, since a device-name read must never be able to interrupt
    capture itself.
    """
    try:
        payload = json.loads(control_path.read_text())
        name = payload["device_name"]
        return name if isinstance(name, str) and name else default
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return default
