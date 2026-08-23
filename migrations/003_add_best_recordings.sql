-- Migration 003: best_recordings — one human-reviewable audio clip per
-- species, kept up to date rather than accumulated. Deliberately NOT
-- one row per detection (that's what detections.audio_clip_path/
-- detections.extract_detection_clips/retain_clips_days originally
-- implied): species_id is the primary key, so there is exactly one
-- row per species, holding whichever detection has the highest
-- confidence ever seen for it. A new higher-confidence detection
-- overwrites clip_path in place (see repositories.upsert_best_recording)
-- rather than adding a row, so this table — and the audio/best_clips/
-- directory it points into — can never grow past one clip per species.
--
-- is_approved is a human "starred this as a good recording" flag, set
-- via the dashboard's star button. It resets to 0 whenever the clip is
-- replaced: approval is a judgment about *this specific recording*,
-- and a replacement is a different actual audio clip that hasn't been
-- listened to yet.

CREATE TABLE IF NOT EXISTS best_recordings (
    species_id INTEGER PRIMARY KEY,
    detection_id INTEGER NOT NULL,
    confidence REAL NOT NULL,
    clip_path TEXT NOT NULL,
    is_approved INTEGER NOT NULL DEFAULT 0,
    approved_at_utc TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,

    FOREIGN KEY(species_id) REFERENCES species(id),
    FOREIGN KEY(detection_id) REFERENCES detections(id)
);
