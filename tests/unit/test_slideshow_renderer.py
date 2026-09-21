from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from backyard_bird.slideshow.renderer import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    SlideContent,
    render_slide,
    slide_filename,
)


@pytest.fixture()
def base_image(tmp_path: Path) -> Path:
    path = tmp_path / "base.jpg"
    Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (40, 80, 40)).save(path, format="JPEG")
    return path


def _content(**overrides: object) -> SlideContent:
    defaults = dict(
        common_name="Black-capped Chickadee",
        scientific_name="Poecile atricapillus",
        first_detected_local=datetime(2026, 8, 17, 6, 42, 0),
        detection_count=18,
    )
    defaults.update(overrides)
    return SlideContent(**defaults)  # type: ignore[arg-type]


def test_slide_filename_is_ordered_and_slug_safe() -> None:
    assert slide_filename(1, "Poecile atricapillus") == "01_poecile-atricapillus.jpg"
    assert slide_filename(12, "Turdus migratorius") == "12_turdus-migratorius.jpg"


def test_render_slide_produces_a_1920x1080_jpeg(base_image: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "out" / "slide.jpg"
    result = render_slide(base_image, _content(), output_path)

    assert result == output_path
    with Image.open(output_path) as img:
        assert img.size == (FRAME_WIDTH, FRAME_HEIGHT)
        assert img.format == "JPEG"


def test_render_slide_creates_output_parent_directories(base_image: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "nested" / "dir" / "slide.jpg"
    render_slide(base_image, _content(), output_path)
    assert output_path.exists()


def test_render_slide_resizes_a_wrong_sized_base_image(tmp_path: Path) -> None:
    wrong_size_base = tmp_path / "wrong.jpg"
    Image.new("RGB", (800, 600), (0, 0, 0)).save(wrong_size_base, format="JPEG")

    output_path = tmp_path / "slide.jpg"
    render_slide(wrong_size_base, _content(), output_path)

    with Image.open(output_path) as img:
        assert img.size == (FRAME_WIDTH, FRAME_HEIGHT)


def test_render_slide_is_deterministic_for_the_same_inputs(base_image: Path, tmp_path: Path) -> None:
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    render_slide(base_image, _content(), first)
    render_slide(base_image, _content(), second)

    assert first.read_bytes() == second.read_bytes()


def test_render_slide_clean_mode_and_informational_mode_differ(base_image: Path, tmp_path: Path) -> None:
    clean_path = tmp_path / "clean.jpg"
    informational_path = tmp_path / "informational.jpg"
    render_slide(base_image, _content(), clean_path, display_mode="clean")
    render_slide(base_image, _content(), informational_path, display_mode="informational")

    assert clean_path.read_bytes() != informational_path.read_bytes()


def test_render_slide_handles_a_long_common_name_without_raising(base_image: Path, tmp_path: Path) -> None:
    content = _content(common_name="Yellow-bellied Sapsucker with an Unusually Long Common Name")
    output_path = tmp_path / "slide.jpg"
    render_slide(base_image, content, output_path)
    with Image.open(output_path) as img:
        assert img.size == (FRAME_WIDTH, FRAME_HEIGHT)


def test_render_slide_with_attribution_and_confidence_does_not_raise(base_image: Path, tmp_path: Path) -> None:
    content = _content(highest_confidence=0.93, attribution_text="Photo by Jane Doe, CC BY 4.0")
    output_path = tmp_path / "slide.jpg"
    render_slide(base_image, content, output_path)
    assert output_path.exists()


def test_render_slide_with_empty_attribution_text_omits_it(base_image: Path, tmp_path: Path) -> None:
    with_attribution = tmp_path / "with.jpg"
    without_attribution = tmp_path / "without.jpg"
    render_slide(base_image, _content(attribution_text="Photo by Jane Doe"), with_attribution)
    render_slide(base_image, _content(attribution_text=None), without_attribution)

    assert with_attribution.read_bytes() != without_attribution.read_bytes()
