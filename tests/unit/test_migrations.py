import sqlite3
from pathlib import Path

from backyard_bird.database.migrations import apply_migrations, discover_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def test_discover_migrations_finds_001_through_005() -> None:
    versions = [version for version, _, _ in discover_migrations(MIGRATIONS_DIR)]
    assert 1 in versions
    assert 2 in versions
    assert 3 in versions
    assert 4 in versions
    assert 5 in versions


def test_apply_migrations_creates_expected_tables(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row

    applied = apply_migrations(conn, MIGRATIONS_DIR)

    assert applied == [1, 2, 3, 4, 5]
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "schema_migrations", "audio_segments", "species", "detections", "bird_images", "best_recordings",
        "daily_species_summary", "slideshows", "slideshow_items",
    } <= tables
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(best_recordings)").fetchall()}
    assert "highpass_hz" in columns


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row

    first = apply_migrations(conn, MIGRATIONS_DIR)
    second = apply_migrations(conn, MIGRATIONS_DIR)

    assert first == [1, 2, 3, 4, 5]
    assert second == []


def test_apply_migrations_tolerates_a_crash_between_add_column_and_its_schema_migrations_row(
    tmp_path: Path,
) -> None:
    # Simulates the exact crash window ALTER TABLE ADD COLUMN can't
    # close on its own (no IF NOT EXISTS clause in SQLite, unlike
    # CREATE TABLE/INDEX): the column change landed, but the process
    # died before schema_migrations recorded version 4 as applied.
    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS_DIR)  # 1-4 all land normally first

    conn.execute("DELETE FROM schema_migrations WHERE version = 4")  # ...then "forget" 4 was ever applied
    conn.commit()

    reapplied = apply_migrations(conn, MIGRATIONS_DIR)

    assert reapplied == [4]
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(best_recordings)").fetchall()}
    assert "highpass_hz" in columns
