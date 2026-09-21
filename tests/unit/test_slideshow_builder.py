import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from backyard_bird.database.migrations import apply_migrations
from backyard_bird.database.repositories import (
    get_or_create_species,
    get_slideshow_by_date,
    insert_audio_segment,
    insert_bird_image,
    insert_detection,
)
from backyard_bird.slideshow.builder import build_daily_slideshow
from backyard_bird.slideshow.renderer import FRAME_HEIGHT, FRAME_WIDTH

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "test.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection, MIGRATIONS_DIR)
    yield connection
    connection.close()


def _seed_species(
    conn: sqlite3.Connection,
    images_root: Path,
    scientific_name: str,
    common_name: str,
    confidence: float,
    detected_at: datetime,
    with_image: bool = True,
) -> int:
    with conn:
        segment_id = insert_audio_segment(
            conn, "mic-01", f"incoming/{scientific_name}.wav", detected_at, detected_at + timedelta(seconds=30),
            30.0, 48000, 1, 1000, scientific_name,
        )
        species_id = get_or_create_species(conn, scientific_name, common_name)
        insert_detection(
            conn, segment_id, species_id, detected_at, 0.0, 3.0, confidence, 1.0, None, None, False, None,
        )
        if with_image:
            image_dir = images_root / "species" / scientific_name.lower().replace(" ", "-")
            image_dir.mkdir(parents=True, exist_ok=True)
            image_path = image_dir / "optimized_1920x1080.jpg"
            Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (40, 80, 40)).save(image_path, format="JPEG")
            insert_bird_image(
                conn, species_id=species_id, source_provider="wikimedia_commons",
                original_image_url=f"https://example.com/{scientific_name}.jpg", status="approved",
                local_file_path=str(image_path), attribution_text="Jane Doe",
            )
    return species_id


def test_build_daily_slideshow_renders_qualifying_species(tmp_path: Path, conn: sqlite3.Connection) -> None:
    images_root = tmp_path / "images"
    output_root = tmp_path / "slideshows"
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    _seed_species(conn, images_root, "Poecile atricapillus", "Black-capped Chickadee", 0.90, now)

    local_date = now.astimezone().strftime("%Y-%m-%d")  # not used for filtering below; see next test for exactness
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    result = build_daily_slideshow(
        conn, today, "UTC", order="first_detection", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )

    assert result.manifest.species_count == 1
    assert result.slideshow_directory == output_root / today
    slide_files = list(result.slideshow_directory.glob("*.jpg"))
    assert len(slide_files) == 1
    with Image.open(slide_files[0]) as img:
        assert img.size == (FRAME_WIDTH, FRAME_HEIGHT)

    assert (result.slideshow_directory / "manifest.json").exists()
    assert (result.slideshow_directory / "README.txt").exists()

    row = get_slideshow_by_date(conn, today)
    assert row["species_count"] == 1
    assert row["status"] == "generated"
    items = conn.execute("SELECT * FROM slideshow_items WHERE slideshow_id = ?", (row["id"],)).fetchall()
    assert len(items) == 1
    assert items[0]["title_text"] == "Black-capped Chickadee"
    assert items[0]["subtitle_text"] == "Poecile atricapillus"


def test_build_daily_slideshow_excludes_species_without_an_image(tmp_path: Path, conn: sqlite3.Connection) -> None:
    images_root = tmp_path / "images"
    output_root = tmp_path / "slideshows"
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    today = now.strftime("%Y-%m-%d")
    _seed_species(conn, images_root, "Poecile atricapillus", "Black-capped Chickadee", 0.90, now, with_image=False)

    result = build_daily_slideshow(
        conn, today, "UTC", order="first_detection", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )

    assert result.manifest.species_count == 0
    assert list(result.slideshow_directory.glob("*.jpg")) == []


def test_build_daily_slideshow_excludes_species_below_confidence_threshold(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    images_root = tmp_path / "images"
    output_root = tmp_path / "slideshows"
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    today = now.strftime("%Y-%m-%d")
    _seed_species(conn, images_root, "Poecile atricapillus", "Black-capped Chickadee", 0.60, now)

    result = build_daily_slideshow(
        conn, today, "UTC", order="first_detection", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )

    assert result.manifest.species_count == 0


def test_build_daily_slideshow_is_idempotent(tmp_path: Path, conn: sqlite3.Connection) -> None:
    images_root = tmp_path / "images"
    output_root = tmp_path / "slideshows"
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    today = now.strftime("%Y-%m-%d")
    _seed_species(conn, images_root, "Poecile atricapillus", "Black-capped Chickadee", 0.90, now)

    first = build_daily_slideshow(
        conn, today, "UTC", order="first_detection", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )
    second = build_daily_slideshow(
        conn, today, "UTC", order="first_detection", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )

    assert first.slideshow_id == second.slideshow_id
    assert len(list(second.slideshow_directory.glob("*.jpg"))) == 1  # not doubled up on rebuild
    rows = conn.execute("SELECT COUNT(*) AS c FROM slideshows WHERE local_date = ?", (today,)).fetchone()
    assert rows["c"] == 1


def test_build_daily_slideshow_removes_stale_slides_no_longer_qualifying(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    images_root = tmp_path / "images"
    output_root = tmp_path / "slideshows"
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    today = now.strftime("%Y-%m-%d")
    species_id = _seed_species(conn, images_root, "Poecile atricapillus", "Black-capped Chickadee", 0.90, now)

    build_daily_slideshow(
        conn, today, "UTC", order="first_detection", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )
    with conn:
        conn.execute(
            "UPDATE detections SET review_status = 'rejected', is_reviewed = 1 WHERE species_id = ?", (species_id,)
        )
    result = build_daily_slideshow(
        conn, today, "UTC", order="first_detection", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )

    assert result.manifest.species_count == 0
    assert list(result.slideshow_directory.glob("*.jpg")) == []


def test_build_daily_slideshow_orders_alphabetically_when_configured(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    images_root = tmp_path / "images"
    output_root = tmp_path / "slideshows"
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    today = now.strftime("%Y-%m-%d")
    _seed_species(conn, images_root, "Taeniopygia guttata", "Zebra Finch", 0.90, now)
    _seed_species(conn, images_root, "Turdus migratorius", "American Robin", 0.90, now + timedelta(seconds=1))

    result = build_daily_slideshow(
        conn, today, "UTC", order="alphabetical", display_mode="informational",
        slideshow_minimum_confidence=0.75, output_root=output_root,
    )

    assert [item.common_name for item in result.manifest.items] == ["American Robin", "Zebra Finch"]
