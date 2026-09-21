-- Migration 005: daily_species_summary, slideshows, slideshow_items
-- (§12.5/§12.7/§12.8) — Phase 5 slideshow generation.
--
-- §14 lists "whether an approved image is available" and "whether the
-- species qualifies for the slideshow" among what the daily
-- aggregation job determines, but §12.5's schema (reproduced here)
-- has no columns for either. Both are derivable at slideshow-build
-- time — a JOIN against bird_images for image availability, and
-- highest_confidence compared against
-- birdnet.slideshow_minimum_confidence for qualification — so storing
-- them here would be denormalized state that could drift from the
-- source tables. daily_species_summary sticks to exactly the columns
-- §12.5 specifies; the Phase 5 slideshow builder applies that
-- filtering when it reads this table.

CREATE TABLE IF NOT EXISTS daily_species_summary (
    local_date TEXT NOT NULL,
    species_id INTEGER NOT NULL,
    first_detected_at_utc TEXT NOT NULL,
    last_detected_at_utc TEXT NOT NULL,
    detection_count INTEGER NOT NULL,
    highest_confidence REAL NOT NULL,
    representative_detection_id INTEGER,
    updated_at_utc TEXT NOT NULL,

    PRIMARY KEY(local_date, species_id),

    FOREIGN KEY(species_id) REFERENCES species(id),
    FOREIGN KEY(representative_detection_id) REFERENCES detections(id)
);

CREATE INDEX IF NOT EXISTS idx_daily_species_summary_species_id ON daily_species_summary(species_id);

CREATE TABLE IF NOT EXISTS slideshows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_date TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    output_directory TEXT,
    manifest_path TEXT,
    species_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    generated_at_utc TEXT,
    delivered_at_utc TEXT,
    delivery_status TEXT,
    delivery_method TEXT,
    delivery_error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_slideshows_status ON slideshows(status);

CREATE TABLE IF NOT EXISTS slideshow_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slideshow_id INTEGER NOT NULL,
    species_id INTEGER NOT NULL,
    bird_image_id INTEGER NOT NULL,
    display_order INTEGER NOT NULL,
    title_text TEXT,
    subtitle_text TEXT,
    detection_summary_text TEXT,
    rendered_file_path TEXT,
    created_at_utc TEXT NOT NULL,

    FOREIGN KEY(slideshow_id) REFERENCES slideshows(id),
    FOREIGN KEY(species_id) REFERENCES species(id),
    FOREIGN KEY(bird_image_id) REFERENCES bird_images(id),

    UNIQUE(slideshow_id, display_order)
);

CREATE INDEX IF NOT EXISTS idx_slideshow_items_slideshow_id ON slideshow_items(slideshow_id);
