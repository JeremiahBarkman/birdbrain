"""Audio retention sweeps (§8.1: "prevent unlimited growth of raw audio
storage"; §9's queue lifecycle: "successful files move to processed/
or are deleted according to retention policy").

Three independent sweeps share one mechanism, one per queue stage:

- incoming/  — segments an analyzer hasn't yet picked up.
- processed/ — segments BirdNET already finished with; detections and
  any best-recording clip are already durable in SQLite / best_clips/,
  so these are the safest to delete of the three.
- failed/    — segments that errored out of analysis, kept longer by
  default so there's a window to investigate before they're gone.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _sweep(directory: Path, retention_days: float, event_name: str) -> list[Path]:
    """Delete .wav files in directory older than retention_days.

    Returns the paths deleted. A non-positive retention_days disables
    the sweep entirely (returns immediately, deletes nothing).
    """
    if retention_days <= 0 or not directory.exists():
        return []

    cutoff = time.time() - retention_days * 86400
    deleted: list[Path] = []
    for path in directory.glob("*.wav"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(path)
        except FileNotFoundError:
            continue  # already removed by something else — not our problem

    if deleted:
        logger.info(event_name, extra={"event": event_name, "deleted_count": len(deleted)})
    return deleted


def sweep_incoming(incoming_dir: Path, retention_days: float) -> list[Path]:
    """Delete unanalyzed .wav files in incoming_dir older than retention_days."""
    return _sweep(incoming_dir, retention_days, "retention_sweep_completed")


def sweep_processed(processed_dir: Path, retention_days: float) -> list[Path]:
    """Delete already-analyzed .wav files in processed_dir older than retention_days."""
    return _sweep(processed_dir, retention_days, "processed_retention_sweep_completed")


def sweep_failed(failed_dir: Path, retention_days: float) -> list[Path]:
    """Delete already-failed .wav files in failed_dir older than retention_days."""
    return _sweep(failed_dir, retention_days, "failed_retention_sweep_completed")
