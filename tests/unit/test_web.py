"""Dashboard route tests via Flask's test client — no real HTTP server,
socket, or browser involved.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backyard_bird.audio.clips import species_clip_path, species_spectrogram_path
from backyard_bird.config import AppConfig, AudioConfig, BirdNETConfig, LocationConfig, SlideshowConfig, SystemConfig
from backyard_bird.database.connection import get_connection
from backyard_bird.database.migrations import apply_migrations
from backyard_bird.database.repositories import (
    get_or_create_species,
    insert_audio_segment,
    insert_bird_image,
    insert_detection,
    upsert_best_recording,
)
from backyard_bird.images.cache import species_cache_dir
from backyard_bird.web.app import create_app

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
SCIENTIFIC = "Poecile atricapillus"


def _app_config(data_directory: Path) -> AppConfig:
    return AppConfig(
        system=SystemConfig(data_directory=data_directory, timezone="UTC"),
        location=LocationConfig(latitude=45.0, longitude=-123.0),
        audio=AudioConfig(device_name="Fake Mic", microphone_id="mic-01"),
    )


def _seed_detection_with_image(
    data_directory: Path, *, with_image: bool = True, with_recording: bool = False
) -> None:
    conn = get_connection(data_directory / "database" / "birds.sqlite3")
    apply_migrations(conn, MIGRATIONS_DIR)
    # "5 minutes ago" rather than a fixed date, so species_today/
    # detections_today assertions stay correct regardless of when the
    # test suite happens to run.
    detected_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    started = detected_at - timedelta(seconds=5)
    with conn:
        segment_id = insert_audio_segment(
            conn, "mic-01", "incoming/a.wav", started, started + timedelta(seconds=30),
            30.0, 48000, 1, 1000, "abc",
        )
        species_id = get_or_create_species(conn, SCIENTIFIC, "Black-capped Chickadee")
        detection_id = insert_detection(
            conn, segment_id, species_id, detected_at, 5.0, 8.0, 0.81, 1.0, None, None, False, None
        )
        if with_recording:
            clip_path = species_clip_path(data_directory / "audio" / "best_clips", SCIENTIFIC)
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            clip_path.write_bytes(b"RIFF....WAVEfmt fake clip bytes for testing")
            upsert_best_recording(conn, species_id, detection_id, 0.81, clip_path)
        if with_image:
            cache_dir = species_cache_dir(data_directory / "images", SCIENTIFIC)
            cache_dir.mkdir(parents=True, exist_ok=True)
            image_path = cache_dir / "optimized_1920x1080.jpg"
            image_path.write_bytes(b"\xff\xd8\xff fake jpeg bytes for testing")
            insert_bird_image(
                conn,
                species_id=species_id,
                source_provider="wikimedia_commons",
                original_image_url="https://example.com/chickadee.jpg",
                status="approved",
                local_file_path=str(image_path),
            )
    conn.close()


@pytest.fixture()
def client(tmp_path: Path):
    _seed_detection_with_image(tmp_path)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        yield c


def test_index_page_loads(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert b"Backyard Bird Discovery" in response.data


def test_index_page_includes_image_popup_markup(client) -> None:
    # Regression check for the click-to-enlarge feature: the modal
    # container/close button/image element the JS wires up must
    # actually be present in the rendered page.
    response = client.get("/")
    assert b'id="image-modal"' in response.data
    assert b'id="image-modal-close"' in response.data
    assert b'id="image-modal-img"' in response.data


def test_index_page_includes_mic_status_markup(client) -> None:
    # Regression check for the live mic status/level meter feature:
    # the elements dashboard.js's pollMicStatus()/renderMicStatus()
    # wire up must actually be present in the rendered page.
    response = client.get("/")
    assert b'id="mic-status-dot"' in response.data
    assert b'id="mic-status-text"' in response.data
    assert b'id="mic-device-select"' in response.data
    assert b'id="level-meter-fill"' in response.data
    assert b'id="live-monitor-btn"' in response.data
    assert b'id="live-monitor-audio"' in response.data


def test_api_stats_reports_seeded_detection(client) -> None:
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.get_json()

    assert data["total_species"] == 1
    assert data["total_detections"] == 1
    assert data["species_today"] == 1
    assert data["detections_today"] == 1
    assert data["most_recent"]["common_name"] == "Black-capped Chickadee"
    assert data["most_recent"]["image_url"] == "/media/species/poecile-atricapillus/optimized_1920x1080.jpg"
    assert len(data["species"]) == 1
    assert data["species"][0]["detection_count"] == 1
    assert data["species"][0]["confidence"] == 0.81
    assert data["species"][0]["image_url"] == "/media/species/poecile-atricapillus/optimized_1920x1080.jpg"
    assert data["species"][0]["recording"] is None  # no best_recordings row seeded for this fixture


def test_api_stats_min_confidence_filters_species_table(client) -> None:
    # Seeded detection is 0.81 confidence — above this filters it out...
    above = client.get("/api/stats?min_confidence=0.90").get_json()
    assert above["species"] == []
    # ...at or below it, it's still there. The overview stat cards are
    # unaffected by the filter either way (§ scoped to the table only).
    below = client.get("/api/stats?min_confidence=0.5").get_json()
    assert len(below["species"]) == 1
    assert above["total_species"] == below["total_species"] == 1


def test_api_stats_date_filter_on_species_table(client) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    todays = client.get(f"/api/stats?date={today}").get_json()
    assert len(todays["species"]) == 1

    others = client.get(f"/api/stats?date={yesterday}").get_json()
    assert others["species"] == []


def test_api_stats_malformed_date_falls_back_to_unfiltered(client) -> None:
    response = client.get("/api/stats?date=not-a-date")
    assert response.status_code == 200
    assert len(response.get_json()["species"]) == 1


def test_index_page_includes_heatmap_markup(client) -> None:
    # Regression check for the species-activity-by-hour heatmap view:
    # the toggle button (in the same row as Slideshow/Save Slides) and
    # the elements dashboard.js's renderHeatmap() wires up must both
    # actually be present in the rendered page.
    response = client.get("/")
    assert b'id="heatmap-view-btn"' in response.data
    assert b'id="species-heatmap-wrap"' in response.data
    assert b'id="species-heatmap-head"' in response.data
    assert b'id="species-heatmap-body"' in response.data


def test_api_heatmap_buckets_seeded_detection_into_its_local_hour(client) -> None:
    # The fixture's detection is "5 minutes ago" in UTC and the test
    # app's timezone is UTC (_app_config above), so its local hour is
    # just datetime.now(UTC).hour.
    expected_hour = datetime.now(timezone.utc).hour

    response = client.get("/api/heatmap")
    assert response.status_code == 200
    data = response.get_json()

    assert data["total_detections"] == 1
    assert len(data["species"]) == 1
    species = data["species"][0]
    assert species["common_name"] == "Black-capped Chickadee"
    assert len(species["hours"]) == 24
    assert species["hours"][expected_hour] == 1
    assert species["total"] == 1
    assert sum(species["hours"]) == 1


def test_api_heatmap_min_confidence_filters(client) -> None:
    # Seeded detection is 0.81 confidence, same filter semantics as
    # /api/stats' species table.
    above = client.get("/api/heatmap?min_confidence=0.90").get_json()
    assert above["species"] == []
    assert above["total_detections"] == 0

    below = client.get("/api/heatmap?min_confidence=0.5").get_json()
    assert len(below["species"]) == 1


def test_api_heatmap_date_filter(client) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    todays = client.get(f"/api/heatmap?date={today}").get_json()
    assert len(todays["species"]) == 1

    others = client.get(f"/api/heatmap?date={yesterday}").get_json()
    assert others["species"] == []


def test_api_heatmap_with_empty_database(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    apply_migrations(conn, MIGRATIONS_DIR)
    conn.close()

    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/heatmap")

    assert response.status_code == 200
    data = response.get_json()
    assert data["species"] == []
    assert data["total_detections"] == 0


def test_api_slideshow_includes_qualifying_species(client) -> None:
    response = client.get("/api/slideshow")
    assert response.status_code == 200
    data = response.get_json()

    assert data["display_mode"] == "informational"  # SlideshowConfig's default
    assert data["image_duration_seconds"] == 20
    assert len(data["slides"]) == 1
    slide = data["slides"][0]
    assert slide["common_name"] == "Black-capped Chickadee"
    assert slide["scientific_name"] == SCIENTIFIC
    assert slide["image_url"] == "/media/species/poecile-atricapillus/optimized_1920x1080.jpg"
    assert slide["detection_count"] == 1
    assert slide["highest_confidence"] == 0.81
    assert "first_detected_local_iso" in slide


def test_api_slideshow_excludes_species_without_an_approved_image(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        data = c.get("/api/slideshow").get_json()

    assert data["slides"] == []


def test_api_slideshow_excludes_species_below_the_configured_confidence_threshold(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path)  # confidence 0.81
    config = _app_config(tmp_path)
    config.birdnet = BirdNETConfig(slideshow_minimum_confidence=0.95)
    app = create_app(config)
    app.testing = True
    with app.test_client() as c:
        data = c.get("/api/slideshow").get_json()

    assert data["slides"] == []


def test_api_slideshow_excludes_a_detection_from_a_previous_day(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    apply_migrations(conn, MIGRATIONS_DIR)
    detected_at = datetime.now(timezone.utc) - timedelta(days=2)
    with conn:
        segment_id = insert_audio_segment(
            conn, "mic-01", "incoming/a.wav", detected_at, detected_at + timedelta(seconds=30),
            30.0, 48000, 1, 1000, "abc",
        )
        species_id = get_or_create_species(conn, SCIENTIFIC, "Black-capped Chickadee")
        insert_detection(conn, segment_id, species_id, detected_at, 5.0, 8.0, 0.90, 1.0, None, None, False, None)
        cache_dir = species_cache_dir(tmp_path / "images", SCIENTIFIC)
        cache_dir.mkdir(parents=True, exist_ok=True)
        image_path = cache_dir / "optimized_1920x1080.jpg"
        image_path.write_bytes(b"\xff\xd8\xff fake jpeg bytes for testing")
        insert_bird_image(
            conn, species_id=species_id, source_provider="wikimedia_commons",
            original_image_url="https://example.com/chickadee.jpg", status="approved",
            local_file_path=str(image_path),
        )
    conn.close()

    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        data = c.get("/api/slideshow").get_json()

    assert data["slides"] == []


def test_api_slideshow_respects_configured_display_mode_and_duration(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path)
    config = _app_config(tmp_path)
    config.slideshow = SlideshowConfig(display_mode="clean", image_duration_seconds=45)
    app = create_app(config)
    app.testing = True
    with app.test_client() as c:
        data = c.get("/api/slideshow").get_json()

    assert data["display_mode"] == "clean"
    assert data["image_duration_seconds"] == 45


def test_api_slideshow_orders_alphabetically_when_configured(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    apply_migrations(conn, MIGRATIONS_DIR)
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    with conn:
        for name, scientific, offset in (
            ("Zebra Finch", "Taeniopygia guttata", 0),
            ("American Robin", "Turdus migratorius", 1),
        ):
            segment_id = insert_audio_segment(
                conn, "mic-01", f"incoming/{scientific}.wav", now, now + timedelta(seconds=30),
                30.0, 48000, 1, 1000, scientific,
            )
            species_id = get_or_create_species(conn, scientific, name)
            insert_detection(
                conn, segment_id, species_id, now + timedelta(seconds=offset), 5.0, 8.0, 0.90, 1.0,
                None, None, False, None,
            )
            cache_dir = species_cache_dir(tmp_path / "images", scientific)
            cache_dir.mkdir(parents=True, exist_ok=True)
            image_path = cache_dir / "optimized_1920x1080.jpg"
            image_path.write_bytes(b"\xff\xd8\xff fake jpeg bytes for testing")
            insert_bird_image(
                conn, species_id=species_id, source_provider="wikimedia_commons",
                original_image_url=f"https://example.com/{scientific}.jpg", status="approved",
                local_file_path=str(image_path),
            )
    conn.close()

    config = _app_config(tmp_path)
    config.slideshow = SlideshowConfig(order="alphabetical")
    app = create_app(config)
    app.testing = True
    with app.test_client() as c:
        data = c.get("/api/slideshow").get_json()

    assert [s["common_name"] for s in data["slides"]] == ["American Robin", "Zebra Finch"]


def _seed_detection_with_real_image(data_directory: Path) -> None:
    """Like _seed_detection_with_image, but with an actual decodable
    JPEG rather than placeholder bytes — the export endpoint really
    renders through Pillow (slideshow/builder.py -> renderer.py),
    unlike everything else in this file that only ever serves the
    cached file back verbatim.
    """
    from PIL import Image

    from backyard_bird.slideshow.renderer import FRAME_HEIGHT, FRAME_WIDTH

    conn = get_connection(data_directory / "database" / "birds.sqlite3")
    apply_migrations(conn, MIGRATIONS_DIR)
    detected_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    started = detected_at - timedelta(seconds=5)
    with conn:
        segment_id = insert_audio_segment(
            conn, "mic-01", "incoming/a.wav", started, started + timedelta(seconds=30),
            30.0, 48000, 1, 1000, "abc",
        )
        species_id = get_or_create_species(conn, SCIENTIFIC, "Black-capped Chickadee")
        insert_detection(conn, segment_id, species_id, detected_at, 5.0, 8.0, 0.81, 1.0, None, None, False, None)
        cache_dir = species_cache_dir(data_directory / "images", SCIENTIFIC)
        cache_dir.mkdir(parents=True, exist_ok=True)
        image_path = cache_dir / "optimized_1920x1080.jpg"
        Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (40, 80, 40)).save(image_path, format="JPEG")
        insert_bird_image(
            conn, species_id=species_id, source_provider="wikimedia_commons",
            original_image_url="https://example.com/chickadee.jpg", status="approved",
            local_file_path=str(image_path), attribution_text="Jane Doe",
        )
    conn.close()


def test_api_slideshow_export_zip_actually_builds_the_slideshow_in_the_database(tmp_path: Path) -> None:
    _seed_detection_with_real_image(tmp_path)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/slideshow/export.zip")
    assert response.status_code == 200

    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute("SELECT * FROM slideshows WHERE local_date = ?", (today,)).fetchone()
    conn.close()
    assert row is not None
    assert row["species_count"] == 1


def test_api_slideshow_export_zip_contains_a_date_named_folder(tmp_path: Path) -> None:
    _seed_detection_with_real_image(tmp_path)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/slideshow/export.zip")

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert f'filename="Birdbrain Slideshow {today}.zip"' in response.headers["Content-Disposition"]

    with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
        names = zf.namelist()
        # Every entry lives under <date>/ — extracting the zip produces
        # exactly one dated folder, not files loose at the zip root.
        assert all(name.startswith(f"{today}/") for name in names)
        assert f"{today}/manifest.json" in names
        assert f"{today}/README.txt" in names
        assert any(name.endswith(".jpg") for name in names)


def test_api_slideshow_export_zip_404s_when_nothing_qualifies(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    apply_migrations(conn, MIGRATIONS_DIR)
    conn.close()

    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/slideshow/export.zip")

    assert response.status_code == 404
    assert "error" in response.get_json()


def test_species_without_an_approved_image_has_null_image_url(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        data = c.get("/api/stats").get_json()

    assert data["species"][0]["image_url"] is None
    assert data["most_recent"]["image_url"] is None


def test_cached_image_is_actually_served(client) -> None:
    response = client.get("/media/species/poecile-atricapillus/optimized_1920x1080.jpg")
    assert response.status_code == 200
    assert response.data == b"\xff\xd8\xff fake jpeg bytes for testing"


def test_image_serving_works_with_a_relative_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: Flask's send_from_directory resolves a relative
    directory against the app's own module path, not the process's
    working directory — a relative data_directory (exactly what
    config.yaml actually ships, `data_directory: ./data`) silently
    404'd every image until app.py started calling .resolve() on it.
    This is deliberately not covered by the `client` fixture above,
    which — like every other test here — uses pytest's tmp_path and so
    is always absolute already, unable to catch this class of bug.
    """
    monkeypatch.chdir(tmp_path)
    _seed_detection_with_image(Path("data"))
    app = create_app(_app_config(Path("data")))
    app.testing = True

    with app.test_client() as c:
        response = c.get("/media/species/poecile-atricapillus/optimized_1920x1080.jpg")

    assert response.status_code == 200
    assert response.data == b"\xff\xd8\xff fake jpeg bytes for testing"


def test_api_stats_with_empty_database(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    apply_migrations(conn, MIGRATIONS_DIR)
    conn.close()

    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/stats")

    data = response.get_json()
    assert data["total_species"] == 0
    assert data["total_detections"] == 0
    assert data["most_recent"] is None
    assert data["species"] == []


def test_api_recent_detections(client) -> None:
    response = client.get("/api/detections/recent")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]["common_name"] == "Black-capped Chickadee"


# -- best_recordings (play/star/save) -----------------------------------------


@pytest.fixture()
def client_with_recording(tmp_path: Path):
    _seed_detection_with_image(tmp_path, with_image=False, with_recording=True)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        yield c


def test_api_stats_includes_recording_info(client_with_recording) -> None:
    data = client_with_recording.get("/api/stats").get_json()
    recording = data["species"][0]["recording"]
    assert recording["url"] == "/media/audio/poecile-atricapillus/clip.wav"
    assert recording["download_url"] == "/media/audio/poecile-atricapillus/clip.wav?download=1"
    assert recording["confidence"] == 0.81
    assert recording["is_approved"] is False
    # No spectrogram.png written by this fixture — generation is
    # best-effort (worker.py), so a missing file must read as "no
    # spectrogram yet," not a broken image link.
    assert recording["spectrogram_url"] is None
    # This fixture's clip.wav is fake (non-WAV) bytes, so duration/rate
    # can't be read — must degrade to None, not 500 the whole request.
    assert recording["duration_seconds"] is None
    assert recording["sample_rate"] is None


def test_api_stats_includes_real_clip_duration_and_sample_rate(client_with_recording, tmp_path: Path) -> None:
    import wave

    clip_path = species_clip_path(tmp_path / "audio" / "best_clips", SCIENTIFIC)
    with wave.open(str(clip_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48000)
        wav_file.writeframes(b"\x00\x00" * 48000 * 3)  # 3 seconds of silence

    data = client_with_recording.get("/api/stats").get_json()
    recording = data["species"][0]["recording"]
    assert recording["duration_seconds"] == pytest.approx(3.0)
    assert recording["sample_rate"] == 48000


def test_api_stats_includes_spectrogram_url_when_file_exists(client_with_recording, tmp_path: Path) -> None:
    spectrogram_path = species_spectrogram_path(tmp_path / "audio" / "best_clips", SCIENTIFIC)
    spectrogram_path.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes for testing")

    data = client_with_recording.get("/api/stats").get_json()

    assert data["species"][0]["recording"]["spectrogram_url"] == "/media/audio/poecile-atricapillus/spectrogram.png"


def test_species_spectrogram_is_served(client_with_recording, tmp_path: Path) -> None:
    spectrogram_path = species_spectrogram_path(tmp_path / "audio" / "best_clips", SCIENTIFIC)
    spectrogram_path.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes for testing")

    response = client_with_recording.get("/media/audio/poecile-atricapillus/spectrogram.png")

    assert response.status_code == 200
    assert response.data == b"\x89PNG\r\n\x1a\n fake png bytes for testing"


def test_species_spectrogram_highpass_returns_a_regenerated_png(
    client_with_recording, tmp_path: Path
) -> None:
    import wave

    import numpy as np

    clip_path = species_clip_path(tmp_path / "audio" / "best_clips", SCIENTIFIC)
    sample_rate = 48000
    t = np.arange(sample_rate * 2) / sample_rate
    tone = (np.sin(2 * np.pi * 2000 * t) * 20000).astype(np.int16)
    with wave.open(str(clip_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(tone.tobytes())
    species_spectrogram_path(tmp_path / "audio" / "best_clips", SCIENTIFIC).write_bytes(b"cached default png")

    plain = client_with_recording.get("/media/audio/poecile-atricapillus/spectrogram.png")
    filtered = client_with_recording.get("/media/audio/poecile-atricapillus/spectrogram.png?highpass=10000")

    assert plain.status_code == 200
    assert plain.data == b"cached default png"  # no highpass param -> serves the cached file untouched
    assert filtered.status_code == 200
    assert filtered.data != plain.data  # a real, freshly rendered PNG, not the cached default
    assert filtered.data.startswith(b"\x89PNG")


def test_species_spectrogram_highpass_falls_back_to_cached_file_on_bad_clip(
    client_with_recording, tmp_path: Path
) -> None:
    # client_with_recording's clip.wav is fake (non-WAV) bytes -
    # rendering must fail closed to the cached default, not 500.
    species_spectrogram_path(tmp_path / "audio" / "best_clips", SCIENTIFIC).write_bytes(b"cached default png")

    response = client_with_recording.get("/media/audio/poecile-atricapillus/spectrogram.png?highpass=500")

    assert response.status_code == 200
    assert response.data == b"cached default png"


def test_species_clip_is_served_for_playback(client_with_recording) -> None:
    response = client_with_recording.get("/media/audio/poecile-atricapillus/clip.wav")
    assert response.status_code == 200
    assert response.data == b"RIFF....WAVEfmt fake clip bytes for testing"
    assert "attachment" not in response.headers.get("Content-Disposition", "")


def test_species_clip_download_sets_attachment_header(client_with_recording) -> None:
    response = client_with_recording.get("/media/audio/poecile-atricapillus/clip.wav?download=1")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert "poecile-atricapillus.wav" in response.headers["Content-Disposition"]


def test_star_recording_approves_and_persists(client_with_recording) -> None:
    response = client_with_recording.post(
        f"/api/species/{SCIENTIFIC}/recording/star", json={"approved": True}
    )
    assert response.status_code == 200
    assert response.get_json()["is_approved"] is True

    data = client_with_recording.get("/api/stats").get_json()
    assert data["species"][0]["recording"]["is_approved"] is True


def test_star_recording_can_unapprove(client_with_recording) -> None:
    client_with_recording.post(f"/api/species/{SCIENTIFIC}/recording/star", json={"approved": True})
    response = client_with_recording.post(
        f"/api/species/{SCIENTIFIC}/recording/star", json={"approved": False}
    )
    assert response.get_json()["is_approved"] is False


def test_star_recording_unknown_species_404s(client_with_recording) -> None:
    response = client_with_recording.post(
        "/api/species/Nonexistent species/recording/star", json={"approved": True}
    )
    assert response.status_code == 404


def test_star_recording_species_with_no_recording_yet_404s(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False, with_recording=False)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.post(f"/api/species/{SCIENTIFIC}/recording/star", json={"approved": True})

    assert response.status_code == 404


# -- recording high-pass filter selection (user request: remember it) -------


def test_api_stats_includes_default_highpass(client_with_recording) -> None:
    data = client_with_recording.get("/api/stats").get_json()
    assert data["species"][0]["recording"]["highpass_hz"] == 0


def test_set_recording_highpass_persists(client_with_recording) -> None:
    response = client_with_recording.post(
        f"/api/species/{SCIENTIFIC}/recording/highpass", json={"highpass_hz": 1000}
    )
    assert response.status_code == 200
    assert response.get_json()["highpass_hz"] == 1000

    data = client_with_recording.get("/api/stats").get_json()
    assert data["species"][0]["recording"]["highpass_hz"] == 1000


def test_set_recording_highpass_back_to_off(client_with_recording) -> None:
    client_with_recording.post(f"/api/species/{SCIENTIFIC}/recording/highpass", json={"highpass_hz": 1000})
    response = client_with_recording.post(
        f"/api/species/{SCIENTIFIC}/recording/highpass", json={"highpass_hz": 0}
    )
    assert response.get_json()["highpass_hz"] == 0


def test_set_recording_highpass_unknown_species_404s(client_with_recording) -> None:
    response = client_with_recording.post(
        "/api/species/Nonexistent species/recording/highpass", json={"highpass_hz": 500}
    )
    assert response.status_code == 404


def test_set_recording_highpass_species_with_no_recording_yet_404s(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False, with_recording=False)
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.post(f"/api/species/{SCIENTIFIC}/recording/highpass", json={"highpass_hz": 500})

    assert response.status_code == 404


def test_set_recording_highpass_rejects_non_numeric_value(client_with_recording) -> None:
    response = client_with_recording.post(
        f"/api/species/{SCIENTIFIC}/recording/highpass", json={"highpass_hz": "loud"}
    )
    assert response.status_code == 400


# -- reject/delete species ("kill a false detection", dashboard feature) -----


def test_reject_species_marks_detections_and_hides_from_stats(client) -> None:
    response = client.post(f"/api/species/{SCIENTIFIC}/reject")
    assert response.status_code == 200
    assert response.get_json() == {"scientific_name": SCIENTIFIC, "rejected_detections": 1}

    data = client.get("/api/stats").get_json()
    assert data["species"] == []  # dropped out entirely, not shown as a zero row
    assert data["total_species"] == 0
    assert data["total_detections"] == 0


def test_reject_species_unknown_species_404s(client) -> None:
    response = client.post("/api/species/Nonexistent species/reject")
    assert response.status_code == 404


def test_reject_species_does_not_delete_anything(client, tmp_path: Path) -> None:
    client.post(f"/api/species/{SCIENTIFIC}/reject")

    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM detections").fetchone()["c"]
    finally:
        conn.close()
    assert count == 1  # still there, just marked rejected — nothing physically removed


def test_delete_species_removes_detections_and_clip_file(client_with_recording, tmp_path: Path) -> None:
    clip_path = species_clip_path(tmp_path / "audio" / "best_clips", SCIENTIFIC)
    assert clip_path.exists()
    spectrogram_path = species_spectrogram_path(tmp_path / "audio" / "best_clips", SCIENTIFIC)
    spectrogram_path.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes for testing")

    response = client_with_recording.delete(f"/api/species/{SCIENTIFIC}")
    assert response.status_code == 200
    assert response.get_json() == {"scientific_name": SCIENTIFIC, "detections_deleted": 1}

    assert not clip_path.exists()  # the audio file itself was removed, not just the DB row
    assert not spectrogram_path.exists()  # the whole species clip directory is removed

    conn = get_connection(tmp_path / "database" / "birds.sqlite3")
    try:
        detection_count = conn.execute("SELECT COUNT(*) AS c FROM detections").fetchone()["c"]
        best_recording_count = conn.execute("SELECT COUNT(*) AS c FROM best_recordings").fetchone()["c"]
    finally:
        conn.close()
    assert detection_count == 0
    assert best_recording_count == 0

    data = client_with_recording.get("/api/stats").get_json()
    assert data["species"] == []


def test_delete_species_without_a_recording_still_works(client) -> None:
    # No best_recordings row at all for this species (client fixture
    # seeds a detection but no recording) — must not error just
    # because there's no clip file to clean up.
    response = client.delete(f"/api/species/{SCIENTIFIC}")
    assert response.status_code == 200
    assert response.get_json()["detections_deleted"] == 1


def test_delete_species_unknown_species_404s(client) -> None:
    response = client.delete("/api/species/Nonexistent species")
    assert response.status_code == 404


# -- live mic status/level (dashboard meter, user-requested feature) --------


def test_mic_status_unknown_when_no_status_file(client) -> None:
    response = client.get("/api/mic-status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "unknown"
    assert data["peak_percent"] is None


def test_mic_status_reports_real_capture_data(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False)
    from backyard_bird.audio.levels import MicLevel, write_mic_status

    write_mic_status(
        tmp_path / "run" / "mic_status.json",
        device_name="TONOR G11 USB microphone",
        status="capturing",
        level=MicLevel(peak_percent=42.5, peak_dbfs=-7.4),
    )
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/mic-status")

    data = response.get_json()
    assert data["status"] == "capturing"
    assert data["device_name"] == "TONOR G11 USB microphone"
    assert data["peak_percent"] == 42.5
    assert data["peak_dbfs"] == -7.4


# -- gain (user request: mic input runs quiet by default) --------------------


def test_get_mic_gain_defaults_to_configured_value_when_no_control_file(client) -> None:
    response = client.get("/api/mic-gain")
    assert response.status_code == 200
    data = response.get_json()
    assert data["gain"] == 1.0  # AudioConfig's own default, from _app_config
    assert data["min"] > 0
    assert data["max"] > data["min"]


def test_post_mic_gain_persists_and_is_read_back(client) -> None:
    response = client.post("/api/mic-gain", json={"gain": 2.5})
    assert response.status_code == 200
    assert response.get_json()["gain"] == 2.5

    response = client.get("/api/mic-gain")
    assert response.get_json()["gain"] == 2.5


def test_post_mic_gain_clamps_out_of_range_values(client) -> None:
    response = client.post("/api/mic-gain", json={"gain": 999.0})
    assert response.status_code == 200
    data = response.get_json()
    assert data["gain"] == data["max"]


def test_post_mic_gain_rejects_non_numeric_value(client) -> None:
    response = client.post("/api/mic-gain", json={"gain": "loud"})
    assert response.status_code == 400


# -- mic devices (user request: "in case there is more than one mic") -------


def test_get_mic_devices_lists_available_devices(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from backyard_bird.audio.devices import AudioDevice

    fake_devices = [
        AudioDevice(index=0, name="Mic A", max_input_channels=1, default_samplerate=48000.0, host_api="ALSA"),
        AudioDevice(index=1, name="Mic B", max_input_channels=2, default_samplerate=44100.0, host_api="ALSA"),
    ]
    monkeypatch.setattr("backyard_bird.web.routes.list_input_devices", lambda: fake_devices)

    response = client.get("/api/mic-devices")
    assert response.status_code == 200
    data = response.get_json()
    assert [d["name"] for d in data["devices"]] == ["Mic A", "Mic B"]
    assert data["current"] == "Fake Mic"  # AudioConfig's own default from _app_config, no control file yet


def test_get_mic_devices_reports_control_file_selection(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backyard_bird.audio.device_control import write_device_control
    from backyard_bird.audio.devices import AudioDevice

    monkeypatch.setattr(
        "backyard_bird.web.routes.list_input_devices",
        lambda: [AudioDevice(index=0, name="Mic A", max_input_channels=1, default_samplerate=48000.0, host_api="ALSA")],
    )
    write_device_control(tmp_path / "run" / "mic_device.json", "Mic A")

    response = client.get("/api/mic-devices")
    assert response.get_json()["current"] == "Mic A"


def test_get_mic_devices_handles_enumeration_failure(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("PortAudio not available")

    monkeypatch.setattr("backyard_bird.web.routes.list_input_devices", _boom)

    response = client.get("/api/mic-devices")
    assert response.status_code == 200  # a listing failure must never 500 the whole dashboard
    data = response.get_json()
    assert data["devices"] == []
    assert "error" in data


def test_post_mic_device_writes_control_file_without_config_path(client, tmp_path: Path) -> None:
    from backyard_bird.audio.device_control import read_device_control

    response = client.post("/api/mic-device", json={"device_name": "Mic B"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["device_name"] == "Mic B"
    assert data["persisted"] is False  # the `client` fixture's app has no CONFIG_PATH

    assert read_device_control(tmp_path / "run" / "mic_device.json", default="") == "Mic B"


def test_post_mic_device_persists_to_config_yaml_when_config_path_known(tmp_path: Path) -> None:
    import yaml

    config_path = tmp_path / "config.yaml"
    config_path.write_text("audio:\n  device_name: Fake Mic\n  microphone_id: mic-01\n")
    _seed_detection_with_image(tmp_path, with_image=False)
    app = create_app(_app_config(tmp_path), config_path=config_path)
    app.testing = True
    with app.test_client() as c:
        response = c.post("/api/mic-device", json={"device_name": "Mic B"})

    assert response.status_code == 200
    assert response.get_json()["persisted"] is True
    assert yaml.safe_load(config_path.read_text())["audio"]["device_name"] == "Mic B"


def test_post_mic_device_rejects_empty_name(client) -> None:
    response = client.post("/api/mic-device", json={"device_name": ""})
    assert response.status_code == 400


def test_post_mic_device_rejects_missing_name(client) -> None:
    response = client.post("/api/mic-device", json={})
    assert response.status_code == 400


# -- live audio monitor (dashboard "listen live" button) --------------------


def _fake_relay_server(payload: bytes):
    """A minimal real local TCP server standing in for
    capture_service.py's live-monitor relay: accepts exactly one
    connection, sends `payload`, then closes. Runs in a background
    thread; returns (thread, port).
    """
    import socket
    import threading

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    port = server_socket.getsockname()[1]

    def _serve() -> None:
        conn, _ = server_socket.accept()
        try:
            conn.sendall(payload)
        finally:
            conn.close()
            server_socket.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return thread, port


def test_monitor_live_streams_wav_header_then_relayed_bytes(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False)
    thread, port = _fake_relay_server(b"fake-pcm-audio-bytes")

    config = _app_config(tmp_path)
    config.audio.live_monitor_port = port
    config.audio.enable_live_monitor = True
    app = create_app(config)
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/monitor/live")

    assert response.status_code == 200
    assert response.mimetype == "audio/wav"
    body = response.get_data()
    assert body[:4] == b"RIFF"
    assert body.endswith(b"fake-pcm-audio-bytes")
    thread.join(timeout=5)


def test_monitor_live_returns_503_when_relay_is_unreachable(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False)
    config = _app_config(tmp_path)
    config.audio.live_monitor_port = 1  # nothing listens on port 1
    config.audio.enable_live_monitor = True
    app = create_app(config)
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/monitor/live")

    assert response.status_code == 503


def test_monitor_live_returns_404_when_disabled(tmp_path: Path) -> None:
    _seed_detection_with_image(tmp_path, with_image=False)
    config = _app_config(tmp_path)
    config.audio.enable_live_monitor = False
    app = create_app(config)
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/monitor/live")

    assert response.status_code == 404


def test_mic_status_reports_stale_data_as_unknown(tmp_path: Path) -> None:
    # capture crashed/was killed a while ago — must not keep showing a
    # frozen "capturing" reading forever (see levels.py's STALE_AFTER_SECONDS).
    import json
    from datetime import datetime, timedelta, timezone as tz

    from backyard_bird.audio.levels import STALE_AFTER_SECONDS

    status_path = tmp_path / "run" / "mic_status.json"
    status_path.parent.mkdir(parents=True)
    stale_time = datetime.now(tz.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 5)
    status_path.write_text(
        json.dumps({"device_name": "Mic", "status": "capturing", "peak_percent": 10.0, "peak_dbfs": -20.0,
                    "error_message": None, "updated_at_utc": stale_time.isoformat()})
    )
    app = create_app(_app_config(tmp_path))
    app.testing = True
    with app.test_client() as c:
        response = c.get("/api/mic-status")

    assert response.get_json()["status"] == "unknown"
