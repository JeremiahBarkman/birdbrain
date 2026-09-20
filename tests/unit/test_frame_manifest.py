from datetime import datetime, timezone
from pathlib import Path

from backyard_bird.frame.manifest import SlideshowManifest, SlideshowManifestItem


def _item(name: str, order: int) -> SlideshowManifestItem:
    return SlideshowManifestItem(
        scientific_name=f"Testus {name}",
        common_name=name,
        rendered_file_path=Path(f"/slides/{name}.jpg"),
        display_order=order,
        detection_count=3,
        first_detected_at_utc="2026-09-20T06:00:00+00:00",
    )


def test_species_count_reflects_item_count() -> None:
    manifest = SlideshowManifest(
        local_date="2026-09-20",
        generated_at_utc=datetime(2026, 9, 20, tzinfo=timezone.utc),
        items=[_item("robin", 0), _item("jay", 1)],
    )
    assert manifest.species_count == 2


def test_species_count_is_zero_for_an_empty_manifest() -> None:
    manifest = SlideshowManifest(local_date="2026-09-20", generated_at_utc=datetime.now(timezone.utc))
    assert manifest.species_count == 0


def test_to_json_dict_shape() -> None:
    manifest = SlideshowManifest(
        local_date="2026-09-20",
        generated_at_utc=datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc),
        items=[_item("robin", 0)],
    )

    data = manifest.to_json_dict()

    assert data["local_date"] == "2026-09-20"
    assert data["generated_at_utc"] == "2026-09-20T12:00:00+00:00"
    assert data["species_count"] == 1
    assert data["slides"] == [
        {
            "scientific_name": "Testus robin",
            "common_name": "robin",
            "file_name": "robin.jpg",  # basename only, not the full source path — see docstring
            "display_order": 0,
            "detection_count": 3,
            "first_detected_at_utc": "2026-09-20T06:00:00+00:00",
        }
    ]


def test_to_json_dict_sorts_slides_by_display_order() -> None:
    manifest = SlideshowManifest(
        local_date="2026-09-20",
        generated_at_utc=datetime.now(timezone.utc),
        items=[_item("jay", 2), _item("robin", 0), _item("finch", 1)],
    )

    data = manifest.to_json_dict()

    assert [slide["common_name"] for slide in data["slides"]] == ["robin", "finch", "jay"]
