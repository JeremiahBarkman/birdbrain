"""Tests the iNaturalist adapter against canned responses shaped like
the real API (captured live during development — see README's Phase 4
notes) — no network access, so these run the same over SSH as locally.
"""
from __future__ import annotations

import requests

from backyard_bird.images.providers.inaturalist import INaturalistProvider

SEARCH_RESPONSE = {
    "total_results": 1,
    "results": [{"id": 199840, "name": "Haemorhous mexicanus", "preferred_common_name": "House Finch"}],
}

TAXON_DETAIL_RESPONSE = {
    "results": [
        {
            "id": 199840,
            "name": "Haemorhous mexicanus",
            "taxon_photos": [
                {
                    "photo": {
                        "id": 178968933,
                        "license_code": None,  # "all rights reserved" — must be filtered when required
                        "attribution": "(c) Juan Miguel Artigas Azas, all rights reserved",
                        "attribution_name": "Juan Miguel Artigas Azas",
                        "original_dimensions": {"width": 1200, "height": 800},
                        "square_url": "https://static.inaturalist.org/photos/178968933/square.jpg",
                        "original_url": "https://static.inaturalist.org/photos/178968933/original.jpg",
                    }
                },
                {
                    "photo": {
                        "id": 56363382,
                        "license_code": "cc-by-nc",
                        "attribution": "(c) Donna Pomeroy, some rights reserved (CC BY-NC)",
                        "attribution_name": "Donna Pomeroy",
                        "original_dimensions": {"width": 2048, "height": 1365},
                        "original_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/56363382/original.jpg",
                    }
                },
            ],
        }
    ]
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


def test_search_returns_photos_from_matched_taxon() -> None:
    session = _FakeSession([_FakeResponse(SEARCH_RESPONSE), _FakeResponse(TAXON_DETAIL_RESPONSE)])
    provider = INaturalistProvider(session=session)

    candidates = provider.search("Haemorhous mexicanus")

    assert len(candidates) == 2
    by_url = {c.original_image_url: c for c in candidates}

    no_license = by_url["https://static.inaturalist.org/photos/178968933/original.jpg"]
    assert no_license.license_name is None  # "all rights reserved" candidate, correctly unlicensed
    assert no_license.image_width == 1200

    licensed = by_url["https://inaturalist-open-data.s3.amazonaws.com/photos/56363382/original.jpg"]
    assert licensed.source_provider == "inaturalist"
    assert licensed.license_name == "CC-BY-NC"
    assert licensed.license_url == "https://creativecommons.org/licenses/by-nc/4.0/"
    assert licensed.photographer_name == "Donna Pomeroy"
    assert licensed.image_width == 2048
    assert licensed.mime_type == "image/jpeg"
    assert licensed.search_query == "Haemorhous mexicanus"


def test_search_returns_empty_list_when_taxon_not_found() -> None:
    session = _FakeSession([_FakeResponse({"results": []})])
    provider = INaturalistProvider(session=session)

    assert provider.search("Nonexistent Bird") == []


def test_search_handles_api_failure_gracefully() -> None:
    session = _FakeSession([_FailingResponse()])
    provider = INaturalistProvider(session=session)

    assert provider.search("Haemorhous mexicanus") == []


def test_taxon_detail_failure_after_successful_search_returns_empty() -> None:
    session = _FakeSession([_FakeResponse(SEARCH_RESPONSE), _FailingResponse()])
    provider = INaturalistProvider(session=session)

    assert provider.search("Haemorhous mexicanus") == []
