import sqlite3
from pathlib import Path

from backyard_bird.database.migrations import apply_migrations, discover_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def test_discover_migrations_finds_001_through_003() -> None:
    versions = [version for version, _, _ in discover_migrations(MIGRATIONS_DIR)]
    assert 1 in versions
    assert 2 in versions
    assert 3 in versions


def test_apply_migrations_creates_expected_tables(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row

    applied = apply_migrations(conn, MIGRATIONS_DIR)

    assert applied == [1, 2, 3]
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "schema_migrations", "audio_segments", "species", "detections", "bird_images", "best_recordings",
    } <= tables


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row

    first = apply_migrations(conn, MIGRATIONS_DIR)
    second = apply_migrations(conn, MIGRATIONS_DIR)

    assert first == [1, 2, 3]
    assert second == []
