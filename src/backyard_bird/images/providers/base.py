"""Image provider interface (§15.3). Every provider adapter implements
this and nothing else in the codebase should call a provider's API
directly — this is the seam CLAUDE.md rule 8 requires.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageCandidate:
    """One image search result, before download/validation."""

    source_provider: str
    source_page_url: str | None
    original_image_url: str
    image_width: int | None
    image_height: int | None
    mime_type: str | None
    photographer_name: str | None
    license_name: str | None
    license_url: str | None
    attribution_text: str | None
    search_query: str


class ImageProvider(ABC):
    name: str

    @abstractmethod
    def search(self, query: str) -> list[ImageCandidate]:
        """Return candidates for one search query string.

        Implementations are free to raise on network/API failures —
        service.py wraps every call and logs+continues to the next
        query/provider rather than letting one bad call sink a whole
        species' search. A provider adapter only needs to worry about
        turning a successful response into ImageCandidate objects.
        """
