"""Dashboard route tests via Flask's test client — no real HTTP server,
socket, or browser involved.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backyard_bird.audio.clips import species_clip_path
from backyard_bird.config import AppConfig, AudioConfig, LocationConfig, SystemConfig
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

    response = client_with_recording.delete(f"/api/species/{SCIENTIFIC}")
    assert response.status_code == 200
    assert response.get_json() == {"scientific_name": SCIENTIFIC, "detections_deleted": 1}

    assert not clip_path.exists()  # the audio file itself was removed, not just the DB row

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
