"""Raw-audio retention sweep (§8.1: "prevent unlimited growth of raw
audio storage").

This applies to data/audio/incoming/ — files an analyzer hasn't yet
picked up. Retention for the processed/ and failed/ queue stages is a
Phase 3 concern, added once that lifecycle (§9) exists.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def sweep_incoming(incoming_dir: Path, retention_days: float) -> list[Path]:
    """Delete .wav files in incoming_dir older than retention_days.

    Returns the paths deleted. A non-positive retention_days disables
    the sweep entirely (returns immediately, deletes nothing).
    """
    if retention_days <= 0 or not incoming_dir.exists():
        return []

    cutoff = time.time() - retention_days * 86400
    deleted: list[Path] = []
    for path in incoming_dir.glob("*.wav"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(path)
        except FileNotFoundError:
            continue  # already removed by something else — not our problem

    if deleted:
        logger.info(
            "retention_sweep_completed",
            extra={"event": "retention_sweep_completed", "deleted_count": len(deleted)},
        )
    return deleted
