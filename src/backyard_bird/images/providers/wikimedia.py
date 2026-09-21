"""Wikimedia Commons image provider (§15.3).

Chosen as the first adapter because it fits the source-selection
criteria unusually well for a keyless, no-signup API: every file on
Commons must be license-tagged, so license/attribution metadata comes
back in the same response as the image itself — no separate lookup or
guesswork required.
"""
from __future__ import annotations

import logging
import re

import requests

from backyard_bird.images.providers.base import ImageCandidate, ImageProvider

logger = logging.getLogger(__name__)

_API_URL = "https://commons.wikimedia.org/w/api.php"
_REQUEST_TIMEOUT_SECONDS = 15
# Matches the frame's target width (§16.4) — no point fetching more.
_THUMBNAIL_WIDTH = 1920
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Commons' Credit field is usually just a copy of the Source field, and
# for a self-published photo that's conventionally the literal string
# "Own work" — meaningful as Commons metadata (it's an uploader's claim
# about provenance) but useless as something to actually credit on a
# slide (§16.2's "required attribution" means crediting a person, not
# echoing an internal metadata convention). Filtered out below so the
# Artist field (the actual photographer/uploader name) gets shown
# instead whenever this is the only thing Credit has to offer.
_GENERIC_CREDIT_VALUES = {"own work", "self-published work", "self-photographed", "self photographed"}
# Wikimedia's API etiquette asks for an identifying User-Agent on all
# requests; generic/anonymous clients risk being rate-limited or blocked.
_USER_AGENT_HEADER = {
    "User-Agent": "BackyardBirdDiscoverySystem/0.1 (local hobby project; no public deployment)"
}


def _strip_html(value: str | None) -> str | None:
    if not value:
        return None
    return _HTML_TAG_RE.sub("", value).strip() or None


def _meta_value(extmetadata: dict, key: str) -> str | None:
    entry = extmetadata.get(key)
    return entry.get("value") if isinstance(entry, dict) else None


class WikimediaCommonsProvider(ImageProvider):
    name = "wikimedia_commons"

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def search(self, query: str) -> list[ImageCandidate]:
        titles = self._search_titles(query)
        if not titles:
            return []
        return self._fetch_image_info(titles, query)

    def _search_titles(self, query: str) -> list[str]:
        try:
            response = self._session.get(
                _API_URL,
                params={
                    "action": "query",
                    "list": "search",
                    "srnamespace": 6,  # the File: namespace
                    "srlimit": 10,
                    "srsearch": f"{query} filetype:bitmap",
                    "format": "json",
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers=_USER_AGENT_HEADER,
            )
            response.raise_for_status()
            results = response.json().get("query", {}).get("search", [])
            return [r["title"] for r in results if "title" in r]
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning(
                "wikimedia_search_failed",
                extra={"event": "wikimedia_search_failed", "query": query, "error": str(exc)},
            )
            return []

    def _fetch_image_info(self, titles: list[str], query: str) -> list[ImageCandidate]:
        try:
            response = self._session.get(
                _API_URL,
                params={
                    "action": "query",
                    "titles": "|".join(titles),
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime|extmetadata",
                    # Full-size originals from upload.wikimedia.org are
                    # rate-limited/blocked for automated clients — their
                    # own 429 response points at the thumbnail renderer
                    # instead. We only need frame-resolution images
                    # anyway, so request a matching thumbnail directly.
                    "iiurlwidth": _THUMBNAIL_WIDTH,
                    "format": "json",
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers=_USER_AGENT_HEADER,
            )
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", {})
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning(
                "wikimedia_imageinfo_failed",
                extra={"event": "wikimedia_imageinfo_failed", "query": query, "error": str(exc)},
            )
            return []

        candidates: list[ImageCandidate] = []
        for page in pages.values():
            info_list = page.get("imageinfo")
            if not info_list:
                continue
            info = info_list[0]
            # Prefer the thumbnail (see the iiurlwidth comment above);
            # fall back to the original only if no thumbnail came back
            # (e.g. the file is already narrower than _THUMBNAIL_WIDTH).
            image_url = info.get("thumburl") or info.get("url")
            if not image_url:
                continue
            width = info.get("thumbwidth") or info.get("width")
            height = info.get("thumbheight") or info.get("height")
            meta = info.get("extmetadata", {})
            artist = _strip_html(_meta_value(meta, "Artist"))
            credit = _strip_html(_meta_value(meta, "Credit"))
            if credit and credit.strip().lower() in _GENERIC_CREDIT_VALUES:
                credit = None
            candidates.append(
                ImageCandidate(
                    source_provider=self.name,
                    source_page_url=info.get("descriptionurl"),
                    original_image_url=image_url,
                    image_width=width,
                    image_height=height,
                    mime_type=info.get("mime"),
                    photographer_name=artist,
                    license_name=_meta_value(meta, "LicenseShortName"),
                    license_url=_meta_value(meta, "LicenseUrl"),
                    # Artist preferred over Credit — Credit is often
                    # just a copy of the Source field (see
                    # _GENERIC_CREDIT_VALUES above), while Artist is
                    # the actual name to credit.
                    attribution_text=artist or credit,
                    search_query=query,
                )
            )
        return candidates
