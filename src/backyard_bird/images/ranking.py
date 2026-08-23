"""Image candidate ranking (§15.5). Pure — takes already-known
candidate metadata plus validated width/height, returns a score.
Higher is better; service.py sorts candidates by this before
attempting downloads in ranked order.
"""
from __future__ import annotations

from backyard_bird.images.providers.base import ImageCandidate

# Target aspect ratio band around the frame's 16:9 (§16.4) — candidates
# closer to landscape score better without requiring an exact match.
_PREFERRED_ASPECT_MIN = 1.3
_PREFERRED_ASPECT_MAX = 2.0
_TARGET_PIXELS = 1920 * 1080


def score_candidate(
    candidate: ImageCandidate,
    scientific_name: str,
    common_name: str,
    width: int | None,
    height: int | None,
) -> float:
    score = 0.0

    query_lower = candidate.search_query.lower()
    if query_lower == scientific_name.lower():
        score += 30.0
    elif query_lower == common_name.lower():
        score += 20.0

    if candidate.license_name:
        score += 15.0
    if candidate.photographer_name:
        score += 5.0
    if candidate.attribution_text:
        score += 5.0

    if width and height:
        # Resolution, capped at the frame's target so a huge image
        # doesn't outrank an appropriately-sized one for no reason.
        score += min(width * height, _TARGET_PIXELS) / _TARGET_PIXELS * 20.0
        aspect_ratio = width / height
        if _PREFERRED_ASPECT_MIN <= aspect_ratio <= _PREFERRED_ASPECT_MAX:
            score += 10.0

    return score
