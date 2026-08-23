from backyard_bird.images.providers.base import ImageCandidate
from backyard_bird.images.ranking import score_candidate

SCIENTIFIC = "Haemorhous mexicanus"
COMMON = "House Finch"


def _candidate(**overrides) -> ImageCandidate:
    defaults = dict(
        source_provider="wikimedia_commons",
        source_page_url="https://commons.wikimedia.org/wiki/File:x.jpg",
        original_image_url="https://upload.wikimedia.org/x.jpg",
        image_width=1920,
        image_height=1080,
        mime_type="image/jpeg",
        photographer_name="Jane Doe",
        license_name="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution_text="Jane Doe",
        search_query=SCIENTIFIC,
    )
    defaults.update(overrides)
    return ImageCandidate(**defaults)


def test_exact_scientific_name_query_scores_higher_than_common_name() -> None:
    scientific_match = _candidate(search_query=SCIENTIFIC)
    common_match = _candidate(search_query=COMMON)

    score_a = score_candidate(scientific_match, SCIENTIFIC, COMMON, 1920, 1080)
    score_b = score_candidate(common_match, SCIENTIFIC, COMMON, 1920, 1080)

    assert score_a > score_b


def test_license_and_attribution_increase_score() -> None:
    with_license = _candidate(license_name="CC BY 4.0", attribution_text="Jane Doe", photographer_name="Jane Doe")
    without_license = _candidate(license_name=None, attribution_text=None, photographer_name=None)

    score_with = score_candidate(with_license, SCIENTIFIC, COMMON, 1920, 1080)
    score_without = score_candidate(without_license, SCIENTIFIC, COMMON, 1920, 1080)

    assert score_with > score_without


def test_higher_resolution_scores_higher() -> None:
    candidate = _candidate()

    high_res_score = score_candidate(candidate, SCIENTIFIC, COMMON, 1920, 1080)
    low_res_score = score_candidate(candidate, SCIENTIFIC, COMMON, 640, 360)

    assert high_res_score > low_res_score


def test_landscape_orientation_preferred_over_portrait() -> None:
    candidate = _candidate()

    landscape_score = score_candidate(candidate, SCIENTIFIC, COMMON, 1920, 1080)  # 16:9
    portrait_score = score_candidate(candidate, SCIENTIFIC, COMMON, 1080, 1920)  # 9:16, same pixel count

    assert landscape_score > portrait_score


def test_missing_dimensions_does_not_crash() -> None:
    candidate = _candidate()
    score = score_candidate(candidate, SCIENTIFIC, COMMON, None, None)
    assert score >= 0
