"""Audio retention sweeps (§8.1: "prevent unlimited growth of raw audio
storage"; §9's queue lifecycle: "successful files move to processed/
or are deleted according to retention policy").

Three independent age-based sweeps share one mechanism, one per queue
stage:

- incoming/  — segments an analyzer hasn't yet picked up.
- processed/ — segments BirdNET already finished with; detections and
  any best-recording clip are already durable in SQLite / best_clips/,
  so these are the safest to delete of the three.
- failed/    — segments that errored out of analysis, kept longer by
  default so there's a window to investigate before they're gone.

Age-based retention alone can't actually *guarantee* §8.1's "prevent
unlimited growth" — high enough capture volume, an over-generous
retention_days, or the sweep simply not having run yet (e.g. right
after this feature shipped, against a backlog that predates it — see
git history) all let disk usage grow well past what retention_days
implies before the next sweep catches up. enforce_disk_space_floor
below is the hard backstop: irrespective of age or configuration, it
guarantees a minimum amount of free disk space by deleting oldest
files first once that floor is breached.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Sequence

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


def _oldest_first(directory: Path) -> list[Path]:
    dated: list[tuple[float, Path]] = []
    for path in directory.glob("*.wav"):
        try:
            dated.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            continue  # already removed by something else
    dated.sort(key=lambda entry: entry[0])
    return [path for _, path in dated]


def enforce_disk_space_floor(
    check_path: Path,
    directories_by_priority: Sequence[Path],
    min_free_gb: float,
) -> list[Path]:
    """Guarantee at least min_free_gb free at check_path's filesystem,
    regardless of what the age-based sweeps above have or haven't done
    yet.

    A no-op while free space is already at or above min_free_gb.
    Otherwise, deletes the oldest .wav files in directories_by_priority
    — fully draining one directory before moving to the next, so pass
    the safest-to-lose directory first — stopping as soon as free space
    recovers or there's nothing left to delete.

    A non-positive min_free_gb disables this (returns immediately).
    """
    if min_free_gb <= 0:
        return []

    def free_gb() -> float:
        return shutil.disk_usage(check_path).free / (1024**3)

    try:
        if free_gb() >= min_free_gb:
            return []
    except OSError:
        return []  # can't check free space — don't guess, don't delete

    deleted: list[Path] = []
    for directory in directories_by_priority:
        if not directory.exists():
            continue
        for path in _oldest_first(directory):
            if free_gb() >= min_free_gb:
                break
            try:
                path.unlink()
                deleted.append(path)
            except FileNotFoundError:
                continue
        if free_gb() >= min_free_gb:
            break

    if deleted:
        logger.warning(
            "disk_space_floor_enforced",
            extra={
                "event": "disk_space_floor_enforced",
                "deleted_count": len(deleted),
                "min_free_gb": min_free_gb,
            },
        )
    return deleted
