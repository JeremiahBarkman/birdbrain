"""Orchestrates image acquisition for one species (§15): search across
providers using the §15.2 query order, filter/validate/rank candidates
(§15.4, §15.5), download and cache the best one (§15.6), and record the
outcome — 'approved' or 'unavailable' — in bird_images (§12.6).

acquire_image_for_species() is only ever called from the `images` CLI
commands, never from capture or analysis — an image failure must never
affect detection (CLAUDE.md rule 12), and keeping this a separate,
optional step is what guarantees that. run_watch_loop() is what makes
that automatic (§15.1: "search for a representative image when a newly
detected species has no approved cached image") without ever coupling
it to the analyzer itself — it's its own process, polling the same
database the analyzer only ever writes to, so a slow or failing image
search still can't delay or block a single detection.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from pathlib import Path

import requests

from backyard_bird.config import ImagesConfig
from backyard_bird.database.repositories import insert_bird_image, list_species_needing_image_search
from backyard_bird.images.cache import (
    build_optimized_frame_image,
    optimized_image_path,
    original_image_path,
    species_cache_dir,
)
from backyard_bird.images.providers.base import ImageCandidate, ImageProvider
from backyard_bird.images.ranking import score_candidate
from backyard_bird.images.validation import validate_image_bytes

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 20
_MAX_CANDIDATES = 20  # enough to rank from without hammering providers' *search* endpoint
# Providers' download/CDN endpoints (Wikimedia's included — confirmed by
# a real 429 "does not comply with our robot policy" response during
# testing) are far less tolerant of bursts than search. Real detections
# only need one good image, so there's no reason to try more than a
# handful of ranked candidates, and a short pause between attempts is
# what their own error message asked for ("a less disruptive approach").
_MAX_DOWNLOAD_ATTEMPTS = 5
_DOWNLOAD_DELAY_SECONDS = 1.0
_MIME_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png"}
# Several image CDNs (Wikimedia's included) reject requests with the
# default python-requests User-Agent as bot-like — a descriptive one is
# both good general HTTP citizenship and required in practice here.
_DOWNLOAD_HEADERS = {
    "User-Agent": "BackyardBirdDiscoverySystem/0.1 (local hobby project; no public deployment)"
}


def _search_queries(scientific_name: str, common_name: str, region: str | None) -> list[str]:
    """§15.2, in priority order. The last two (common/scientific name
    plus region) are skipped when no region is configured — there's no
    region name in config.yaml yet (only lat/long), so this covers the
    first three query forms honestly rather than fabricating a region.
    """
    queries = [scientific_name, common_name, f"{scientific_name} bird"]
    if region:
        queries.append(f"{common_name} {region}")
        queries.append(f"{scientific_name} {region}")
    return queries


def _interleave_by_provider(ranked: list[ImageCandidate]) -> list[ImageCandidate]:
    """Round-robins an already-ranked candidate list across providers,
    preserving each provider's internal rank order, so one provider
    with many candidates can't crowd out another before it gets a try.
    """
    by_provider: dict[str, list[ImageCandidate]] = {}
    provider_order: list[str] = []
    for candidate in ranked:
        if candidate.source_provider not in by_provider:
            by_provider[candidate.source_provider] = []
            provider_order.append(candidate.source_provider)
        by_provider[candidate.source_provider].append(candidate)

    interleaved: list[ImageCandidate] = []
    while any(by_provider[p] for p in provider_order):
        for p in provider_order:
            if by_provider[p]:
                interleaved.append(by_provider[p].pop(0))
    return interleaved


def _record_unavailable(
    conn: sqlite3.Connection,
    species_id: int,
    last_attempted: ImageCandidate | None,
) -> None:
    with conn:
        insert_bird_image(
            conn,
            species_id=species_id,
            source_provider=last_attempted.source_provider if last_attempted else "none",
            original_image_url=last_attempted.original_image_url if last_attempted else "",
            search_query=last_attempted.search_query if last_attempted else None,
            status="unavailable",
        )


def acquire_image_for_species(
    conn: sqlite3.Connection,
    species_id: int,
    scientific_name: str,
    common_name: str,
    providers: list[ImageProvider],
    images_config: ImagesConfig,
    images_root: Path,
    region: str | None = None,
    session: requests.Session | None = None,
) -> str:
    """Runs the full search -> filter -> rank -> download flow for one
    species. Returns 'approved' or 'unavailable' — never raises for an
    ordinary search/download/validation failure.
    """
    session = session or requests.Session()
    candidates: list[ImageCandidate] = []

    for provider in providers:
        for query in _search_queries(scientific_name, common_name, region):
            try:
                candidates.extend(provider.search(query))
            except Exception as exc:  # noqa: BLE001 — one provider's failure shouldn't sink the whole search
                logger.warning(
                    "image_provider_search_failed",
                    extra={
                        "event": "image_provider_search_failed",
                        "provider": provider.name,
                        "query": query,
                        "error": str(exc),
                    },
                )
            if len(candidates) >= _MAX_CANDIDATES:
                break

    # Different query forms (scientific/common/"X bird") often resolve
    # to the same underlying photo, particularly for providers like
    # iNaturalist that key off a matched taxon rather than free-text
    # search — dedup before ranking so download attempts aren't spent
    # retrying a URL that already failed once this run.
    seen_urls: set[str] = set()
    deduped: list[ImageCandidate] = []
    for candidate in candidates:
        if candidate.original_image_url not in seen_urls:
            seen_urls.add(candidate.original_image_url)
            deduped.append(candidate)
    candidates = deduped

    if images_config.require_license_metadata:
        candidates = [c for c in candidates if c.license_name]

    if not candidates:
        _record_unavailable(conn, species_id, None)
        logger.info(
            "image_unavailable",
            extra={"event": "image_unavailable", "species_id": species_id, "reason": "no candidates found"},
        )
        return "unavailable"

    ranked = sorted(
        candidates,
        key=lambda c: score_candidate(c, scientific_name, common_name, c.image_width, c.image_height),
        reverse=True,
    )
    # One provider returning many decent candidates can otherwise crowd
    # a whole species' download budget before a second provider's
    # candidates get any turn at all — confirmed happening in practice
    # (see README's Phase 4 notes). Interleaving by provider guarantees
    # every provider gets an early attempt, while keeping each
    # provider's own internal rank order.
    ranked = _interleave_by_provider(ranked)[:_MAX_DOWNLOAD_ATTEMPTS]

    last_attempted: ImageCandidate | None = None
    for attempt_index, candidate in enumerate(ranked):
        if attempt_index > 0:
            time.sleep(_DOWNLOAD_DELAY_SECONDS)
        last_attempted = candidate
        try:
            response = session.get(
                candidate.original_image_url, timeout=_REQUEST_TIMEOUT_SECONDS, headers=_DOWNLOAD_HEADERS
            )
            response.raise_for_status()
            content = response.content
        except requests.RequestException as exc:
            logger.info(
                "image_candidate_rejected",
                extra={"event": "image_candidate_rejected", "reason": f"download failed: {exc}"},
            )
            continue

        result = validate_image_bytes(content, candidate.mime_type, images_config)
        if not result.ok:
            logger.info(
                "image_candidate_rejected",
                extra={"event": "image_candidate_rejected", "reason": result.reason},
            )
            continue

        extension = _MIME_EXTENSIONS.get(candidate.mime_type or "", ".jpg")
        cache_dir = species_cache_dir(images_root, scientific_name)
        cache_dir.mkdir(parents=True, exist_ok=True)
        original_image_path(images_root, scientific_name, extension).write_bytes(content)

        optimized_bytes = build_optimized_frame_image(content)
        optimized_path = optimized_image_path(images_root, scientific_name)
        optimized_path.write_bytes(optimized_bytes)

        score = score_candidate(candidate, scientific_name, common_name, result.width, result.height)
        with conn:
            insert_bird_image(
                conn,
                species_id=species_id,
                source_provider=candidate.source_provider,
                original_image_url=candidate.original_image_url,
                status="approved",
                source_page_url=candidate.source_page_url,
                local_file_path=str(optimized_path),
                image_width=result.width,
                image_height=result.height,
                mime_type=candidate.mime_type,
                sha256=hashlib.sha256(content).hexdigest(),
                photographer_name=candidate.photographer_name,
                license_name=candidate.license_name,
                license_url=candidate.license_url,
                attribution_text=candidate.attribution_text,
                search_query=candidate.search_query,
                suitability_score=score,
            )
        logger.info(
            "image_approved",
            extra={"event": "image_approved", "species_id": species_id, "provider": candidate.source_provider},
        )
        return "approved"

    _record_unavailable(conn, species_id, last_attempted)
    logger.info("image_unavailable", extra={"event": "image_unavailable", "species_id": species_id})
    return "unavailable"


def run_watch_loop(
    conn: sqlite3.Connection,
    providers: list[ImageProvider],
    images_config: ImagesConfig,
    images_root: Path,
    stop_event: threading.Event,
    poll_interval_seconds: float = 600.0,
    session: requests.Session | None = None,
) -> None:
    """§15.1, automated: periodically checks for species with no fresh
    image and acquires one, rather than requiring a manual
    `images fetch-missing`. A 10-minute default poll is frequent enough
    that a newly detected species gets its photo within one cycle
    without checking so often it'd be indistinguishable from the
    rapid-retry pattern that got this codebase rate-limited earlier
    (see README's Phase 4 notes) — each species within a cycle is
    still subject to acquire_image_for_species's own attempt cap and
    inter-attempt delay, this just controls how often a *new* cycle
    starts.
    """
    while not stop_event.is_set():
        try:
            species_rows = list_species_needing_image_search(conn, images_config.refresh_after_days)
            for row in species_rows:
                if stop_event.is_set():
                    break
                logger.info(
                    "watch_species_search_starting",
                    extra={
                        "event": "watch_species_search_starting",
                        "species_id": row["id"],
                        "scientific_name": row["scientific_name"],
                    },
                )
                acquire_image_for_species(
                    conn,
                    row["id"],
                    row["scientific_name"],
                    row["common_name"],
                    providers,
                    images_config,
                    images_root,
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 — this service must survive indefinitely, not crash on one bad cycle
            logger.error(
                "watch_loop_error", extra={"event": "watch_loop_error", "error": str(exc)}, exc_info=True
            )

        stop_event.wait(poll_interval_seconds)
