"""iNaturalist image provider (§15.3): a second, independent source so
image acquisition doesn't depend on a single provider (§3.2's "not
tied to a single image provider" quality goal). Keyless public API,
same as Wikimedia.

Two-step lookup, because the two endpoints serve different purposes:
the taxa *search* endpoint (`/v1/taxa`) is good at matching a name to a
species but only returns one photo per match; the taxon *detail*
endpoint (`/v1/taxa/{id}`) returns that species' full photo list, each
with its own real per-photo license — verified against the live API
before writing this (see README's Phase 4 notes).
"""
from __future__ import annotations

import logging

import requests

from backyard_bird.images.providers.base import ImageCandidate, ImageProvider

logger = logging.getLogger(__name__)

_TAXA_SEARCH_URL = "https://api.inaturalist.org/v1/taxa"
_TAXA_DETAIL_URL = "https://api.inaturalist.org/v1/taxa/{id}"
_REQUEST_TIMEOUT_SECONDS = 15
_MAX_PHOTOS_PER_TAXON = 10
_USER_AGENT_HEADER = {
    "User-Agent": "BackyardBirdDiscoverySystem/0.1 (local hobby project; no public deployment)"
}

# license_code -> a real license URL, for the handful of Creative
# Commons variants iNaturalist actually uses. Anything else (e.g. "pd",
# or a code not in this map) just gets no license_url — still has a
# license_name from the raw code, which is enough to pass
# require_license_metadata without inventing a URL we're not sure of.
_LICENSE_URLS = {
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "cc-by": "https://creativecommons.org/licenses/by/4.0/",
    "cc-by-nc": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc-by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc-by-nd": "https://creativecommons.org/licenses/by-nd/4.0/",
    "cc-by-nc-sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "cc-by-nc-nd": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
}


class INaturalistProvider(ImageProvider):
    name = "inaturalist"

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def search(self, query: str) -> list[ImageCandidate]:
        taxon_id = self._find_best_taxon(query)
        if taxon_id is None:
            return []
        return self._fetch_taxon_photos(taxon_id, query)

    def _find_best_taxon(self, query: str) -> int | None:
        try:
            response = self._session.get(
                _TAXA_SEARCH_URL,
                params={"q": query, "rank": "species", "per_page": 1},
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers=_USER_AGENT_HEADER,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            return results[0]["id"] if results else None
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            logger.warning(
                "inaturalist_search_failed",
                extra={"event": "inaturalist_search_failed", "query": query, "error": str(exc)},
            )
            return None

    def _fetch_taxon_photos(self, taxon_id: int, query: str) -> list[ImageCandidate]:
        try:
            response = self._session.get(
                _TAXA_DETAIL_URL.format(id=taxon_id),
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers=_USER_AGENT_HEADER,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            taxon_photos = results[0].get("taxon_photos", []) if results else []
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            logger.warning(
                "inaturalist_taxon_detail_failed",
                extra={"event": "inaturalist_taxon_detail_failed", "taxon_id": taxon_id, "error": str(exc)},
            )
            return []

        candidates: list[ImageCandidate] = []
        for entry in taxon_photos[:_MAX_PHOTOS_PER_TAXON]:
            photo = entry.get("photo", {})
            image_url = photo.get("original_url") or photo.get("large_url")
            if not image_url:
                continue
            license_code = photo.get("license_code")
            dimensions = photo.get("original_dimensions") or {}
            photo_id = photo.get("id")
            candidates.append(
                ImageCandidate(
                    source_provider=self.name,
                    source_page_url=f"https://www.inaturalist.org/photos/{photo_id}" if photo_id else None,
                    original_image_url=image_url,
                    image_width=dimensions.get("width"),
                    image_height=dimensions.get("height"),
                    mime_type="image/jpeg",  # every size iNaturalist serves is a JPEG, confirmed against the live API
                    photographer_name=photo.get("attribution_name"),
                    license_name=license_code.upper() if license_code else None,
                    license_url=_LICENSE_URLS.get(license_code) if license_code else None,
                    attribution_text=photo.get("attribution"),
                    search_query=query,
                )
            )
        return candidates
