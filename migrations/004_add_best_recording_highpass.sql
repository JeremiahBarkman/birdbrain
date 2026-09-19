-- Migration 004: adds highpass_hz to best_recordings — remembers the
-- dashboard recording modal's high-pass filter selection (§22, added
-- 2026-09-19) per species, the same "one row per species, holds
-- human-set state about *this specific recording*" pattern is_approved
-- already established in migration 003. Resets to 0 (off) whenever the
-- clip is replaced (see repositories.upsert_best_recording), for the
-- same reason is_approved resets there: a replacement is a different
-- physical recording that hasn't been reviewed at any filter setting
-- yet.
--
-- No IF NOT EXISTS guard here — SQLite's ALTER TABLE ADD COLUMN has no
-- such clause (unlike CREATE TABLE/INDEX, which every other migration
-- in this project uses specifically so it can be that guard). See
-- migrations.py's apply_migrations() for how the narrow
-- crash-recovery window this would otherwise leave is handled instead.

ALTER TABLE best_recordings ADD COLUMN highpass_hz REAL NOT NULL DEFAULT 0;
