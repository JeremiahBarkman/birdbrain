-- Migration 001: initial schema — the detection timeline core.
-- See Backyard_Bird_Discovery_System_Requirements.md §12.2-§12.4.
--
-- Deliberately scoped to Phase 3 (§29): daily_species_summary,
-- bird_images, slideshows/slideshow_items, frame_delivery_attempts,
-- and service_health belong to later phases and get their own
-- migrations when those phases start.
--
-- Every statement is idempotent (IF NOT EXISTS) so the migration
-- runner can safely re-run this file if it crashes between applying
-- the schema and recording the version (see database/migrations.py).

CREATE TABLE IF NOT EXISTS audio_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    microphone_id TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    recording_started_at_utc TEXT NOT NULL,
    recording_ended_at_utc TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    sample_rate INTEGER,
    channels INTEGER,
    file_size_bytes INTEGER,
    sha256 TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    processing_attempts INTEGER NOT NULL DEFAULT 0,
    birdnet_version TEXT,
    processing_started_at_utc TEXT,
    processing_completed_at_utc TEXT,
    error_message TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audio_segments_processing_status
    ON audio_segments(processing_status);

CREATE TABLE IF NOT EXISTS species (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scientific_name TEXT NOT NULL UNIQUE,
    common_name TEXT,
    common_name_locale TEXT,
    taxonomic_order TEXT,
    taxonomic_family TEXT,
    birdnet_label TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_segment_id INTEGER NOT NULL,
    species_id INTEGER NOT NULL,
    detected_at_utc TEXT NOT NULL,
    segment_offset_start_seconds REAL NOT NULL,
    segment_offset_end_seconds REAL NOT NULL,
    confidence REAL NOT NULL,
    sensitivity REAL,
    latitude REAL,
    longitude REAL,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    duplicate_of_detection_id INTEGER,
    is_reviewed INTEGER NOT NULL DEFAULT 0,
    review_status TEXT,
    audio_clip_path TEXT,
    spectrogram_path TEXT,
    raw_result_json TEXT,
    created_at_utc TEXT NOT NULL,

    FOREIGN KEY(audio_segment_id) REFERENCES audio_segments(id),
    FOREIGN KEY(species_id) REFERENCES species(id),
    FOREIGN KEY(duplicate_of_detection_id) REFERENCES detections(id)
);

CREATE INDEX IF NOT EXISTS idx_detections_detected_at ON detections(detected_at_utc);
CREATE INDEX IF NOT EXISTS idx_detections_species_id ON detections(species_id);
CREATE INDEX IF NOT EXISTS idx_detections_audio_segment_id ON detections(audio_segment_id);
