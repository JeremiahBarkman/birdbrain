import io

from PIL import Image

from backyard_bird.config import ImagesConfig
from backyard_bird.images.validation import validate_image_bytes


def _jpeg_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(120, 150, 90))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


def _config(**overrides) -> ImagesConfig:
    return ImagesConfig(**overrides)


def test_accepts_valid_landscape_image() -> None:
    content = _jpeg_bytes(1600, 900)
    result = validate_image_bytes(content, "image/jpeg", _config(minimum_width=1280, minimum_height=720))
    assert result.ok
    assert result.width == 1600
    assert result.height == 900


def test_rejects_empty_content() -> None:
    result = validate_image_bytes(b"", "image/jpeg", _config())
    assert not result.ok
    assert "empty" in result.reason


def test_rejects_undecodable_bytes() -> None:
    result = validate_image_bytes(b"not an image at all", "image/jpeg", _config())
    assert not result.ok
    assert "decode" in result.reason


def test_rejects_unsupported_mime_type() -> None:
    content = _jpeg_bytes(1600, 900)
    result = validate_image_bytes(content, "image/svg+xml", _config())
    assert not result.ok
    assert "MIME" in result.reason


def test_rejects_too_small_image() -> None:
    content = _jpeg_bytes(400, 300)
    result = validate_image_bytes(content, "image/jpeg", _config(minimum_width=1280, minimum_height=720))
    assert not result.ok
    assert "too small" in result.reason


def test_rejects_extreme_aspect_ratio() -> None:
    content = _jpeg_bytes(3000, 400)  # very wide panorama
    result = validate_image_bytes(content, "image/jpeg", _config(minimum_width=100, minimum_height=100))
    assert not result.ok
    assert "aspect ratio" in result.reason


def test_rejects_oversized_file() -> None:
    content = _jpeg_bytes(1600, 900)
    result = validate_image_bytes(content, "image/jpeg", _config())
    assert result.ok  # sanity: it's a small real file
    # now force the size limit below the real content length
    import backyard_bird.images.validation as validation_module

    original_limit = validation_module._MAX_FILE_SIZE_BYTES
    validation_module._MAX_FILE_SIZE_BYTES = 10
    try:
        result = validate_image_bytes(content, "image/jpeg", _config())
    finally:
        validation_module._MAX_FILE_SIZE_BYTES = original_limit
    assert not result.ok
    assert "too large" in result.reason
