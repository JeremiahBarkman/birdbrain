"""service.py tests use a fake provider and a fake requests session —
no real network or real provider adapter involved.
"""
from __future__ import annotations

import io
import sqlite3
import threading
from pathlib import Path

import pytest
from PIL import Image

from backyard_bird.config import ImagesConfig
from backyard_bird.database.migrations import apply_migrations
from backyard_bird.database.repositories import get_or_create_species
from backyard_bird.images.providers.base import ImageCandidate, ImageProvider
from backyard_bird.images.service import acquire_image_for_species, run_watch_loop

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

SCIENTIFIC = "Haemorhous mexicanus"
COMMON = "House Finch"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "test.sqlite3")
    connection.row_factory = sqlite3.Row
    apply_migrations(connection, MIGRATIONS_DIR)
    yield connection
    connection.close()


@pytest.fixture()
def species_id(conn: sqlite3.Connection) -> int:
    with conn:
        return get_or_create_species(conn, SCIENTIFIC, COMMON)


def _jpeg_bytes(width: int = 1920, height: int = 1080) -> bytes:
    img = Image.new("RGB", (width, height), color=(80, 140, 60))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


def _candidate(**overrides) -> ImageCandidate:
    defaults = dict(
        source_provider="fake",
        source_page_url="https://example.com/page",
        original_image_url="https://example.com/bird.jpg",
        image_width=1920,
        image_height=1080,
        mime_type="image/jpeg",
        photographer_name="Jane Doe",
        license_name="CC BY 4.0",
        license_url="https://example.com/license",
        attribution_text="Jane Doe",
        search_query=SCIENTIFIC,
    )
    defaults.update(overrides)
    return ImageCandidate(**defaults)


class _FakeProvider(ImageProvider):
    name = "fake"

    def __init__(self, candidates_by_query: dict) -> None:
        self._candidates_by_query = candidates_by_query

    def search(self, query: str) -> list:
        return self._candidates_by_query.get(query, [])


class _FakeHttpResponse:
    def __init__(self, content: bytes, ok: bool = True) -> None:
        self.content = content
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            import requests

            raise requests.exceptions.HTTPError("404")


class _FakeSession:
    def __init__(self, content_by_url: dict) -> None:
        self._content_by_url = content_by_url

    def get(self, url: str, timeout: float, headers: dict | None = None):
        if url not in self._content_by_url:
            return _FakeHttpResponse(b"", ok=False)
        return _FakeHttpResponse(self._content_by_url[url])


def test_approves_the_best_valid_candidate(
    conn: sqlite3.Connection, species_id: int, tmp_path: Path
) -> None:
    good_url = "https://example.com/good.jpg"
    provider = _FakeProvider({SCIENTIFIC: [_candidate(original_image_url=good_url)]})
    session = _FakeSession({good_url: _jpeg_bytes()})

    status = acquire_image_for_species(
        conn, species_id, SCIENTIFIC, COMMON, [provider], ImagesConfig(), tmp_path / "images", session=session
    )

    assert status == "approved"
    row = conn.execute("SELECT * FROM bird_images WHERE species_id = ?", (species_id,)).fetchone()
    assert row["status"] == "approved"
    assert row["local_file_path"] is not None
    assert Path(row["local_file_path"]).exists()
    assert row["license_name"] == "CC BY 4.0"


def test_falls_back_when_best_candidate_fails_validation(
    conn: sqlite3.Connection, species_id: int, tmp_path: Path
) -> None:
    small_url = "https://example.com/small.jpg"
    good_url = "https://example.com/good.jpg"
    provider = _FakeProvider(
        {
            SCIENTIFIC: [
                _candidate(original_image_url=small_url),  # ranks first (exact sci-name query)
                _candidate(original_image_url=good_url, search_query=COMMON),
            ]
        }
    )
    session = _FakeSession({small_url: _jpeg_bytes(200, 150), good_url: _jpeg_bytes()})

    status = acquire_image_for_species(
        conn, species_id, SCIENTIFIC, COMMON, [provider], ImagesConfig(), tmp_path / "images", session=session
    )

    assert status == "approved"
    row = conn.execute("SELECT * FROM bird_images WHERE species_id = ?", (species_id,)).fetchone()
    assert row["original_image_url"] == good_url


def test_records_unavailable_when_no_candidates_found(
    conn: sqlite3.Connection, species_id: int, tmp_path: Path
) -> None:
    provider = _FakeProvider({})  # no candidates for any query
    status = acquire_image_for_species(
        conn, species_id, SCIENTIFIC, COMMON, [provider], ImagesConfig(), tmp_path / "images"
    )

    assert status == "unavailable"
    row = conn.execute("SELECT * FROM bird_images WHERE species_id = ?", (species_id,)).fetchone()
    assert row["status"] == "unavailable"
    assert row["source_provider"] == "none"


def test_records_unavailable_when_all_candidates_fail_validation(
    conn: sqlite3.Connection, species_id: int, tmp_path: Path
) -> None:
    bad_url = "https://example.com/tiny.jpg"
    provider = _FakeProvider({SCIENTIFIC: [_candidate(original_image_url=bad_url)]})
    session = _FakeSession({bad_url: _jpeg_bytes(100, 80)})

    status = acquire_image_for_species(
        conn, species_id, SCIENTIFIC, COMMON, [provider], ImagesConfig(), tmp_path / "images", session=session
    )

    assert status == "unavailable"
    row = conn.execute("SELECT * FROM bird_images WHERE species_id = ?", (species_id,)).fetchone()
    assert row["status"] == "unavailable"
    assert row["source_provider"] == "fake"  # last attempted candidate is still recorded


def test_require_license_metadata_filters_before_download(
    conn: sqlite3.Connection, species_id: int, tmp_path: Path
) -> None:
    no_license_url = "https://example.com/no-license.jpg"
    provider = _FakeProvider(
        {SCIENTIFIC: [_candidate(original_image_url=no_license_url, license_name=None)]}
    )
    session = _FakeSession({no_license_url: _jpeg_bytes()})

    status = acquire_image_for_species(
        conn,
        species_id,
        SCIENTIFIC,
        COMMON,
        [provider],
        ImagesConfig(require_license_metadata=True),
        tmp_path / "images",
        session=session,
    )

    assert status == "unavailable"
    # never even attempted a download -> no candidate recorded to attribute the failure to
    row = conn.execute("SELECT * FROM bird_images WHERE species_id = ?", (species_id,)).fetchone()
    assert row["source_provider"] == "none"


def test_duplicate_candidate_urls_across_queries_are_deduped(
    conn: sqlite3.Connection, species_id: int, tmp_path: Path
) -> None:
    # Mirrors iNaturalist in practice: different query forms (scientific
    # name, common name) resolve to the same taxon and return the same
    # photo set — the duplicate shouldn't cost a second download attempt.
    shared_url = "https://example.com/shared.jpg"
    candidate = _candidate(original_image_url=shared_url)
    provider = _FakeProvider({SCIENTIFIC: [candidate], COMMON: [candidate]})

    download_calls = []

    class _CountingSession(_FakeSession):
        def get(self, url, timeout, headers=None):  # noqa: D102
            download_calls.append(url)
            return super().get(url, timeout, headers)

    session = _CountingSession({shared_url: _jpeg_bytes()})

    status = acquire_image_for_species(
        conn, species_id, SCIENTIFIC, COMMON, [provider], ImagesConfig(), tmp_path / "images", session=session
    )

    assert status == "approved"
    assert download_calls == [shared_url]  # exactly one attempt, not one per query


def test_one_providers_many_candidates_dont_crowd_out_another(
    conn: sqlite3.Connection, species_id: int, tmp_path: Path
) -> None:
    # 8 mediocre candidates from one provider, all ranking above a
    # single strong candidate from a second provider (by raw score) —
    # without interleaving, the strong candidate would never get tried
    # within a small download-attempt budget.
    weak_provider_urls = [f"https://weak.example.com/{i}.jpg" for i in range(8)]
    weak_candidates = [
        _candidate(original_image_url=url, license_name="CC BY 4.0") for url in weak_provider_urls
    ]
    strong_url = "https://strong.example.com/best.jpg"
    strong_candidate = _candidate(
        source_provider="other", original_image_url=strong_url, license_name="CC BY 4.0"
    )

    weak_provider = _FakeProvider({SCIENTIFIC: weak_candidates})
    strong_provider = _FakeProvider({SCIENTIFIC: [strong_candidate]})

    # Every weak candidate is broken (tiny image); only the strong one validates.
    content_by_url = {url: _jpeg_bytes(100, 80) for url in weak_provider_urls}
    content_by_url[strong_url] = _jpeg_bytes()
    session = _FakeSession(content_by_url)

    status = acquire_image_for_species(
        conn,
        species_id,
        SCIENTIFIC,
        COMMON,
        [weak_provider, strong_provider],
        ImagesConfig(),
        tmp_path / "images",
        session=session,
    )

    assert status == "approved"
    row = conn.execute("SELECT * FROM bird_images WHERE species_id = ?", (species_id,)).fetchone()
    assert row["original_image_url"] == strong_url


# -- run_watch_loop (§15.1 automatic search) ----------------------------------


def test_watch_loop_acquires_image_for_pending_species(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backyard_bird.images import service as service_module

    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS_DIR)
    with conn:
        species_id = get_or_create_species(conn, SCIENTIFIC, COMMON)

    good_url = "https://example.com/good.jpg"

    class _FakeWatchProvider(ImageProvider):
        name = "fake"

        def search(self, query: str) -> list:
            return [_candidate(original_image_url=good_url)] if query == SCIENTIFIC else []

    session = _FakeSession({good_url: _jpeg_bytes()})
    stop_event = threading.Event()

    # Stop *after* a species is actually processed, not before — setting
    # stop_event any earlier (e.g. inside the lookup call) would make
    # run_watch_loop's own `if stop_event.is_set(): break` skip
    # processing entirely, testing nothing.
    real_acquire = service_module.acquire_image_for_species

    def _acquire_then_stop(*args: object, **kwargs: object) -> str:
        result = real_acquire(*args, **kwargs)
        stop_event.set()
        return result

    monkeypatch.setattr(service_module, "acquire_image_for_species", _acquire_then_stop)

    run_watch_loop(
        conn,
        [_FakeWatchProvider()],
        ImagesConfig(),
        tmp_path / "images",
        stop_event,
        poll_interval_seconds=0.01,
        session=session,
    )

    row = conn.execute("SELECT * FROM bird_images WHERE species_id = ?", (species_id,)).fetchone()
    assert row is not None
    assert row["status"] == "approved"
    conn.close()


def test_watch_loop_survives_an_exception_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backyard_bird.images import service as service_module

    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS_DIR)

    stop_event = threading.Event()
    call_count = {"n": 0}

    def _boom_once_then_stop(conn: sqlite3.Connection, refresh_after_days: int) -> list:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated db hiccup")
        stop_event.set()
        return []

    monkeypatch.setattr(service_module, "list_species_needing_image_search", _boom_once_then_stop)

    run_watch_loop(conn, [], ImagesConfig(), tmp_path / "images", stop_event, poll_interval_seconds=0.01)

    assert call_count["n"] == 2  # first cycle raised; the loop kept going to a second one
    conn.close()
