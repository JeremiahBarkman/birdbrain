"""Adapter around BirdNET-Analyzer (via birdnetlib).

This is the single integration point with BirdNET — no other module
should import birdnetlib directly (CLAUDE.md rule: keep BirdNET behind
its adapter). Species-name normalization, duplicate suppression, and
database persistence are Phase 3 concerns and live elsewhere; this
module only runs the model and returns its raw results in a typed
shape.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path

from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer

from backyard_bird.config import BirdNETConfig, LocationConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawDetection:
    """One BirdNET detection candidate, field-renamed but not yet normalized."""

    scientific_name: str
    common_name: str
    confidence: float
    start_time_seconds: float
    end_time_seconds: float


@dataclass(frozen=True)
class AnalysisResult:
    detections: list[RawDetection]
    birdnet_version: str
    analysis_duration_seconds: float


_analyzer: Analyzer | None = None


def _get_analyzer() -> Analyzer:
    """Lazily load the BirdNET model once per process (it's slow to load)."""
    global _analyzer
    if _analyzer is None:
        logger.info("loading_birdnet_model", extra={"event": "loading_birdnet_model"})
        started = time.monotonic()
        _analyzer = Analyzer()
        logger.info(
            "birdnet_model_loaded",
            extra={
                "event": "birdnet_model_loaded",
                "duration_seconds": round(time.monotonic() - started, 3),
            },
        )
    return _analyzer


def analyze_file(
    audio_path: Path,
    birdnet_config: BirdNETConfig,
    location: LocationConfig | None = None,
    analysis_date: date_type | None = None,
) -> AnalysisResult:
    """Run BirdNET-Analyzer on a single audio file and return raw detections.

    Applies birdnet_config.database_minimum_confidence as BirdNET's
    min_conf threshold. Callers are responsible for the higher
    slideshow_minimum_confidence filtering used for slide inclusion.
    """
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    analyzer = _get_analyzer()

    kwargs: dict = {"min_conf": birdnet_config.database_minimum_confidence}
    if birdnet_config.geographic_filter_enabled and location is not None:
        kwargs["lat"] = location.latitude
        kwargs["lon"] = location.longitude
    if analysis_date is not None:
        kwargs["date"] = analysis_date

    recording = Recording(analyzer, str(audio_path), **kwargs)

    started = time.monotonic()
    recording.analyze()
    duration = time.monotonic() - started

    detections = [
        RawDetection(
            scientific_name=d["scientific_name"],
            common_name=d["common_name"],
            confidence=float(d["confidence"]),
            start_time_seconds=float(d["start_time"]),
            end_time_seconds=float(d["end_time"]),
        )
        for d in recording.detections
    ]

    logger.info(
        "birdnet_analysis_complete",
        extra={
            "event": "birdnet_analysis_complete",
            "audio_path": str(audio_path),
            "detection_count": len(detections),
            "duration_seconds": round(duration, 3),
        },
    )

    return AnalysisResult(
        detections=detections,
        birdnet_version=getattr(analyzer, "version", "unknown"),
        analysis_duration_seconds=duration,
    )
