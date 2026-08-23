-- Migration 002: bird_images (§12.6) — Phase 4 image acquisition.

CREATE TABLE IF NOT EXISTS bird_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species_id INTEGER NOT NULL,
    source_provider TEXT NOT NULL,
    source_page_url TEXT,
    original_image_url TEXT NOT NULL,
    local_file_path TEXT,
    image_width INTEGER,
    image_height INTEGER,
    mime_type TEXT,
    sha256 TEXT,
    photographer_name TEXT,
    license_name TEXT,
    license_url TEXT,
    attribution_text TEXT,
    search_query TEXT,
    suitability_score REAL,
    status TEXT NOT NULL DEFAULT 'candidate',
    downloaded_at_utc TEXT,
    last_verified_at_utc TEXT,
    created_at_utc TEXT NOT NULL,

    FOREIGN KEY(species_id) REFERENCES species(id)
);

CREATE INDEX IF NOT EXISTS idx_bird_images_species_id ON bird_images(species_id);
CREATE INDEX IF NOT EXISTS idx_bird_images_status ON bird_images(status);
