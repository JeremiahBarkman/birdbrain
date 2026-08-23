"""Normalizes raw BirdNET detections into DB-ready values (§10.1:
"Normalize species names and confidence values"). Pure — no DB, no
birdnetlib import — so it's directly testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from backyard_bird.analysis.birdnet_adapter import RawDetection


@dataclass(frozen=True)
class ParsedDetection:
    scientific_name: str
    common_name: str
    confidence: float
    segment_offset_start_seconds: float
    segment_offset_end_seconds: float
    detected_at_utc: datetime


def _normalize_name(name: str) -> str:
    return " ".join(name.split())  # trim + collapse internal whitespace


def parse_detection(raw: RawDetection, segment_started_at_utc: datetime) -> ParsedDetection:
    """segment_started_at_utc anchors the detection's offset-within-file
    (raw.start_time_seconds) to an absolute timestamp for the timeline.
    """
    return ParsedDetection(
        scientific_name=_normalize_name(raw.scientific_name),
        common_name=_normalize_name(raw.common_name),
        confidence=round(raw.confidence, 4),
        segment_offset_start_seconds=raw.start_time_seconds,
        segment_offset_end_seconds=raw.end_time_seconds,
        detected_at_utc=segment_started_at_utc + timedelta(seconds=raw.start_time_seconds),
    )
