import io
from pathlib import Path

from PIL import Image

from backyard_bird.images.cache import (
    build_optimized_frame_image,
    optimized_image_path,
    original_image_path,
    species_cache_dir,
    species_slug,
)


def test_species_slug_normalizes_name() -> None:
    assert species_slug("Haemorhous mexicanus") == "haemorhous-mexicanus"
    assert species_slug("  Poecile   atricapillus ") == "poecile-atricapillus"


def test_cache_paths_are_nested_under_species_slug(tmp_path: Path) -> None:
    root = tmp_path / "images"
    scientific_name = "Haemorhous mexicanus"

    cache_dir = species_cache_dir(root, scientific_name)
    original = original_image_path(root, scientific_name, ".jpg")
    optimized = optimized_image_path(root, scientific_name)

    assert cache_dir == root / "species" / "haemorhous-mexicanus"
    assert original.parent == cache_dir
    assert optimized.parent == cache_dir
    assert original.name == "original.jpg"
    assert optimized.name == "optimized_1920x1080.jpg"


def test_build_optimized_frame_image_produces_exact_target_size() -> None:
    # A tall portrait source — exercises the crop-narrower-dimension path.
    source = Image.new("RGB", (800, 2000), color=(10, 200, 30))
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG")

    optimized_bytes = build_optimized_frame_image(buffer.getvalue())

    with Image.open(io.BytesIO(optimized_bytes)) as result:
        assert result.size == (1920, 1080)
        assert result.format == "JPEG"


def test_build_optimized_frame_image_handles_wide_source() -> None:
    source = Image.new("RGB", (4000, 1000), color=(200, 10, 30))
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG")

    optimized_bytes = build_optimized_frame_image(buffer.getvalue())

    with Image.open(io.BytesIO(optimized_bytes)) as result:
        assert result.size == (1920, 1080)
