"""Queue-driven BirdNET analysis worker (§9, §10.1).

Watches data/audio/incoming/, claims one file at a time by moving it
into processing/, runs it through the BirdNET adapter, writes
species/detections rows, and files the segment into processed/ or
failed/. A bad file is never allowed to stop the queue (§10.1) and the
whole thing is restart-safe (§20.3/§20.4) — see recover_stuck_segments.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
import wave
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from backyard_bird.analysis.birdnet_adapter import analyze_file
from backyard_bird.analysis.deduplicator import find_duplicate
from backyard_bird.analysis.result_parser import ParsedDetection, parse_detection
from backyard_bird.audio.clips import extract_clip, species_clip_path
from backyard_bird.audio.retention import sweep_failed, sweep_processed
from backyard_bird.audio.segmenter import parse_segment_filename
from backyard_bird.config import AudioConfig, BirdNETConfig, DetectionsConfig, LocationConfig
from backyard_bird.database.repositories import (
    find_audio_segment_by_file_path,
    get_best_recording_confidence,
    get_or_create_species,
    get_recent_detections_for_species,
    insert_audio_segment,
    insert_detection,
    mark_audio_segment_completed,
    mark_audio_segment_failed,
    reset_audio_segment_to_pending,
    upsert_best_recording,
)

logger = logging.getLogger(__name__)

_STALE_PARTIAL_SECONDS = 3600.0  # a .partial older than this means capture crashed mid-write
_RETENTION_SWEEP_INTERVAL_SECONDS = 3600.0  # matches capture_service's incoming/ sweep cadence


@dataclass(frozen=True)
class QueueDirs:
    incoming: Path
    processing: Path
    processed: Path
    failed: Path
    best_clips: Path  # data/audio/best_clips/<species-slug>/clip.wav — see audio/clips.py

    @classmethod
    def under(cls, data_directory: Path) -> "QueueDirs":
        base = data_directory / "audio"
        return cls(
            incoming=base / "incoming",
            processing=base / "processing",
            processed=base / "processed",
            failed=base / "failed",
            best_clips=base / "best_clips",
        )

    def ensure(self) -> None:
        for d in (self.incoming, self.processing, self.processed, self.failed, self.best_clips):
            d.mkdir(parents=True, exist_ok=True)


def cleanup_stale_partial_files(incoming_dir: Path, older_than_seconds: float = _STALE_PARTIAL_SECONDS) -> int:
    """§20.3 point 1: a .partial left behind by a crashed capture process
    will never be completed — remove it rather than leave it forever."""
    if not incoming_dir.exists():
        return 0
    cutoff = time.time() - older_than_seconds
    removed = 0
    for path in incoming_dir.glob("*.wav.partial"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    if removed:
        logger.info("stale_partial_cleanup", extra={"event": "stale_partial_cleanup", "removed_count": removed})
    return removed


def recover_stuck_segments(conn: sqlite3.Connection, dirs: QueueDirs) -> int:
    """§20.3/§20.4: make an interrupted run safe to resume, without ever
    reprocessing a segment that actually finished (which would insert
    duplicate detections). A file sitting in processing/ at startup
    means we crashed somewhere mid-lifecycle; where exactly determines
    the safe recovery action:

    - no DB row at all      -> claim never got past the rename; requeue.
    - status == 'completed' -> DB write succeeded, only the final file
                                move didn't happen; finish the move.
    - status == 'failed'    -> same idea, for the failure path.
    - otherwise ('processing'/'pending') -> genuinely interrupted mid-
      analysis; nothing was committed (see repositories.py's comment
      on transaction boundaries), so it's safe to redo from scratch.
    """
    recovered = 0
    for path in sorted(dirs.processing.glob("*.wav")):
        original_path = str(dirs.incoming / path.name)
        row = find_audio_segment_by_file_path(conn, original_path)
        if row is None:
            path.rename(dirs.incoming / path.name)
        elif row["processing_status"] == "completed":
            path.rename(dirs.processed / path.name)
        elif row["processing_status"] == "failed":
            path.rename(dirs.failed / path.name)
        else:
            with conn:
                reset_audio_segment_to_pending(conn, row["id"])
            path.rename(dirs.incoming / path.name)
        recovered += 1
    if recovered:
        logger.info(
            "queue_recovery_completed",
            extra={"event": "queue_recovery_completed", "recovered_count": recovered},
        )
    return recovered


def _maybe_update_best_recording(
    conn: sqlite3.Connection,
    dirs: QueueDirs,
    processing_path: Path,
    species_id: int,
    detection_id: int,
    parsed: ParsedDetection,
    detections_config: DetectionsConfig,
) -> None:
    """Extracts a clip and replaces the species' best_recordings row
    only if this detection actually beats the current best — cheap
    confidence check first so extraction (real file I/O) only happens
    for the rare detection that wins. Never raises: like BirdNET
    analysis failures and image/frame failures elsewhere in this
    codebase, a bug here must not stop a real detection from being
    recorded.
    """
    try:
        current_best = get_best_recording_confidence(conn, species_id)
        if current_best is not None and parsed.confidence <= current_best:
            return
        clip_path = species_clip_path(dirs.best_clips, parsed.scientific_name)
        extract_clip(
            processing_path,
            parsed.segment_offset_start_seconds,
            parsed.segment_offset_end_seconds,
            detections_config.clip_padding_seconds,
            clip_path,
        )
        upsert_best_recording(conn, species_id, detection_id, parsed.confidence, clip_path)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.error(
            "best_recording_update_failed",
            extra={"event": "best_recording_update_failed", "species_id": species_id, "error": str(exc)},
            exc_info=True,
        )


def process_one_file(
    conn: sqlite3.Connection,
    incoming_path: Path,
    dirs: QueueDirs,
    birdnet_config: BirdNETConfig,
    location: LocationConfig,
    detections_config: DetectionsConfig,
) -> None:
    """Claim, analyze, and file one segment. Never raises for an
    analysis failure — that's recorded on the segment and the file
    moves to failed/ instead, so one bad file can't block the queue.
    """
    file_info = parse_segment_filename(incoming_path.name)
    processing_path = dirs.processing / incoming_path.name
    incoming_path.rename(processing_path)  # atomic claim

    try:
        with wave.open(str(processing_path)) as wav_file:
            duration_seconds = wav_file.getnframes() / wav_file.getframerate()
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
        file_size_bytes = processing_path.stat().st_size
        sha256 = hashlib.sha256(processing_path.read_bytes()).hexdigest()
    except Exception as exc:  # noqa: BLE001 — §10.1: corrupt/unsupported files must not stop the queue
        # No audio_segments row exists yet at this point, so there's
        # nothing to mark failed — just file it away. Recovery only
        # rescans processing/, so this can't retry forever.
        processing_path.rename(dirs.failed / processing_path.name)
        logger.error(
            "segment_unreadable",
            extra={"event": "segment_unreadable", "file": str(processing_path), "error": str(exc)},
            exc_info=True,
        )
        return

    recording_ended_at = file_info.recording_started_at_utc + timedelta(seconds=duration_seconds)

    with conn:
        segment_id = insert_audio_segment(
            conn,
            microphone_id=file_info.microphone_id,
            file_path=str(incoming_path),  # original location: a stable identity, not a live pointer
            recording_started_at_utc=file_info.recording_started_at_utc,
            recording_ended_at_utc=recording_ended_at,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            channels=channels,
            file_size_bytes=file_size_bytes,
            sha256=sha256,
        )

    try:
        result = analyze_file(processing_path, birdnet_config, location=location)
    except Exception as exc:  # noqa: BLE001 — a bad file must not stop the queue
        with conn:
            mark_audio_segment_failed(conn, segment_id, str(exc))
        processing_path.rename(dirs.failed / processing_path.name)
        logger.error(
            "segment_analysis_failed",
            extra={"event": "segment_analysis_failed", "segment_id": segment_id, "error": str(exc)},
            exc_info=True,
        )
        return

    with conn:
        for raw in result.detections:
            parsed = parse_detection(raw, file_info.recording_started_at_utc)
            species_id = get_or_create_species(conn, parsed.scientific_name, parsed.common_name)
            recent = get_recent_detections_for_species(
                conn,
                species_id,
                file_info.microphone_id,
                since_utc=parsed.detected_at_utc - timedelta(seconds=birdnet_config.duplicate_window_seconds),
            )
            duplicate_of = find_duplicate(
                parsed.detected_at_utc, recent, birdnet_config.duplicate_window_seconds
            )
            detection_id = insert_detection(
                conn,
                audio_segment_id=segment_id,
                species_id=species_id,
                detected_at_utc=parsed.detected_at_utc,
                segment_offset_start_seconds=parsed.segment_offset_start_seconds,
                segment_offset_end_seconds=parsed.segment_offset_end_seconds,
                confidence=parsed.confidence,
                sensitivity=birdnet_config.sensitivity,
                latitude=location.latitude if birdnet_config.geographic_filter_enabled else None,
                longitude=location.longitude if birdnet_config.geographic_filter_enabled else None,
                is_duplicate=duplicate_of is not None,
                duplicate_of_detection_id=duplicate_of["id"] if duplicate_of is not None else None,
            )

            # Duplicates don't compete for "best recording" — they're
            # the same real call re-seen in an overlapping segment, the
            # same reason they're excluded from the species table.
            if detections_config.extract_detection_clips and duplicate_of is None:
                _maybe_update_best_recording(
                    conn, dirs, processing_path, species_id, detection_id, parsed, detections_config
                )
        mark_audio_segment_completed(conn, segment_id, result.birdnet_version)

    processing_path.rename(dirs.processed / processing_path.name)
    logger.info(
        "segment_processed",
        extra={
            "event": "segment_processed",
            "segment_id": segment_id,
            "detection_count": len(result.detections),
        },
    )


def _maybe_sweep_processed_and_failed(
    dirs: QueueDirs,
    audio_config: AudioConfig | None,
    last_swept_at: float,
    now: float,
) -> float:
    """Run the processed/ and failed/ retention sweeps if the interval
    has elapsed, and return the (possibly updated) last-swept time.

    A None audio_config disables both sweeps — used by callers/tests
    that don't care about retention rather than making them supply one.
    """
    if audio_config is None or now - last_swept_at < _RETENTION_SWEEP_INTERVAL_SECONDS:
        return last_swept_at
    sweep_processed(dirs.processed, audio_config.processed_audio_retention_days)
    sweep_failed(dirs.failed, audio_config.failed_audio_retention_days)
    return now


def run_worker_loop(
    conn: sqlite3.Connection,
    birdnet_config: BirdNETConfig,
    location: LocationConfig,
    dirs: QueueDirs,
    stop_event: threading.Event,
    detections_config: DetectionsConfig,
    audio_config: AudioConfig | None = None,
    max_files: int | None = None,
    poll_interval_seconds: float = 2.0,
) -> int:
    """Block, processing data/audio/incoming/ continuously until
    stop_event is set or max_files is reached (the latter is for
    bounded local smoke tests — production runs pass None).

    Also periodically sweeps processed/ and failed/ per
    audio_config's retention settings (§8.1, §9) — the same policy
    capture_service already applies to incoming/. See
    _maybe_sweep_processed_and_failed.
    """
    dirs.ensure()
    cleanup_stale_partial_files(dirs.incoming)
    recover_stuck_segments(conn, dirs)

    processed_count = 0
    last_retention_sweep = 0.0
    while not stop_event.is_set():
        last_retention_sweep = _maybe_sweep_processed_and_failed(
            dirs, audio_config, last_retention_sweep, time.monotonic()
        )

        pending = sorted(dirs.incoming.glob("*.wav"))
        if not pending:
            stop_event.wait(poll_interval_seconds)
            continue

        for path in pending:
            if stop_event.is_set():
                break
            try:
                process_one_file(conn, path, dirs, birdnet_config, location, detections_config)
            except Exception as exc:  # noqa: BLE001 — final safety net; see process_one_file's own handling
                logger.error(
                    "worker_unexpected_error",
                    extra={"event": "worker_unexpected_error", "file": str(path), "error": str(exc)},
                    exc_info=True,
                )
            processed_count += 1
            if max_files is not None and processed_count >= max_files:
                return processed_count

    return processed_count
