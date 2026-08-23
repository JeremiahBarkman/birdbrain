"""Tests the Wikimedia adapter against canned responses shaped like the
real API — no network access, so these run the same over SSH as
locally. tests/integration/ is where the real API gets exercised.
"""
from __future__ import annotations

import requests

from backyard_bird.images.providers.wikimedia import WikimediaCommonsProvider

SEARCH_RESPONSE = {
    "query": {
        "search": [
            {"title": "File:House Finch male.jpg"},
            {"title": "File:House Finch female.jpg"},
        ]
    }
}

IMAGEINFO_RESPONSE = {
    "query": {
        "pages": {
            "123": {
                "title": "File:House Finch male.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/house-finch-male.jpg",
                        "thumburl": "https://upload.wikimedia.org/thumb/house-finch-male-1920px.jpg",
                        "thumbwidth": 1920,
                        "thumbheight": 1280,
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:House_Finch_male.jpg",
                        "width": 2400,
                        "height": 1600,
                        "mime": "image/jpeg",
                        "extmetadata": {
                            "Artist": {"value": '<a href="https://example.com">Jane Doe</a>'},
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                            "Credit": {"value": "Own work"},
                        },
                    }
                ],
            },
            "456": {
                "title": "File:House Finch female.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/house-finch-female.jpg",
                        "width": 1200,
                        "height": 800,
                        "mime": "image/jpeg",
                        "extmetadata": {},
                    }
                ],
            },
        }
    }
}


class _FakeResponse:
    def __init__(self, json_data: dict) -> None:
        self._json_data = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json_data


class _FailingResponse:
    def raise_for_status(self) -> None:
        raise requests.exceptions.HTTPError("HTTP 503")


class _FakeSession:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    def get(self, *args, **kwargs):
        return self._responses.pop(0)


def test_search_returns_parsed_candidates() -> None:
    session = _FakeSession([_FakeResponse(SEARCH_RESPONSE), _FakeResponse(IMAGEINFO_RESPONSE)])
    provider = WikimediaCommonsProvider(session=session)

    candidates = provider.search("House Finch")

    assert len(candidates) == 2

    # Thumbnail preferred over the full-size original when available
    # (Commons rate-limits/blocks direct original downloads — see
    # providers/wikimedia.py's iiurlwidth comment).
    male = next(c for c in candidates if "male" in c.original_image_url)
    assert male.original_image_url == "https://upload.wikimedia.org/thumb/house-finch-male-1920px.jpg"
    assert male.source_provider == "wikimedia_commons"
    assert male.image_width == 1920
    assert male.image_height == 1280
    assert male.photographer_name == "Jane Doe"  # HTML stripped
    assert male.license_name == "CC BY-SA 4.0"
    assert male.search_query == "House Finch"

    # No thumburl in this entry -> falls back to the full-size original.
    female = next(c for c in candidates if "female" in c.original_image_url)
    assert female.original_image_url == "https://upload.wikimedia.org/house-finch-female.jpg"
    assert female.image_width == 1200
    assert female.license_name is None  # no extmetadata provided — must not crash


def test_search_returns_empty_list_when_no_results() -> None:
    session = _FakeSession([_FakeResponse({"query": {"search": []}})])
    provider = WikimediaCommonsProvider(session=session)

    assert provider.search("Nonexistent Bird") == []


def test_search_handles_api_failure_gracefully() -> None:
    session = _FakeSession([_FailingResponse()])
    provider = WikimediaCommonsProvider(session=session)

    assert provider.search("House Finch") == []
