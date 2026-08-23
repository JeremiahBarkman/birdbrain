"""Pure duplicate-detection decision logic (§10.5).

Segments overlap by design (§8.2), so the same call can legitimately
be detected once in each of two adjacent segments. DB access (fetching
"recent" candidate detections to compare against) lives in
database/repositories.py — this module only decides, given
already-fetched rows, whether a new candidate is a duplicate of one of
them, which keeps it testable without a database.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Mapping, Sequence, Union

# sqlite3.Row in production; a plain Mapping is enough in tests.
DetectionLike = Union[sqlite3.Row, Mapping[str, object]]


def find_duplicate(
    detected_at_utc: datetime,
    recent_detections: Sequence[DetectionLike],
    window_seconds: float,
) -> DetectionLike | None:
    """Return the earliest recent detection within window_seconds, if any.

    recent_detections must already be filtered to the same species and
    microphone (see repositories.get_recent_detections_for_species) and
    ordered oldest-first, so the first match found is the original.
    """
    for existing in recent_detections:
        existing_time = datetime.fromisoformat(existing["detected_at_utc"])
        if abs((detected_at_utc - existing_time).total_seconds()) <= window_seconds:
            return existing
    return None
