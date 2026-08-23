"""Image validation (§15.4). Operates on already-downloaded bytes, so
it's testable without a network provider.

License-metadata presence and cache-duplicate checks aren't done here:
license filtering happens in service.py before a candidate is even
downloaded (cheaper — no point fetching bytes for a candidate that will
be rejected regardless), and duplicate-hash checking needs cache
access this module deliberately doesn't have.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from backyard_bird.config import ImagesConfig

_ACCEPTED_MIME_TYPES = {"image/jpeg", "image/png"}
_MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # generous but bounded
_MIN_ASPECT_RATIO = 0.4  # guards against extreme crops/panoramas
_MAX_ASPECT_RATIO = 3.0


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None
    width: int | None
    height: int | None


def validate_image_bytes(content: bytes, mime_type: str | None, config: ImagesConfig) -> ValidationResult:
    if not content:
        return ValidationResult(False, "empty response body", None, None)
    if len(content) > _MAX_FILE_SIZE_BYTES:
        return ValidationResult(False, f"file too large ({len(content)} bytes)", None, None)
    if mime_type and mime_type not in _ACCEPTED_MIME_TYPES:
        return ValidationResult(False, f"unsupported MIME type {mime_type!r}", None, None)

    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()  # cheap structural check; invalidates the handle
        with Image.open(io.BytesIO(content)) as img:  # reopen to actually read pixel size
            width, height = img.size
    except UnidentifiedImageError:
        return ValidationResult(False, "could not decode image", None, None)
    except Exception as exc:  # noqa: BLE001 — any decode failure is a validation failure, not a crash
        return ValidationResult(False, f"decode error: {exc}", None, None)

    if width < config.minimum_width or height < config.minimum_height:
        return ValidationResult(False, f"too small ({width}x{height})", width, height)

    aspect_ratio = width / height
    if not (_MIN_ASPECT_RATIO <= aspect_ratio <= _MAX_ASPECT_RATIO):
        return ValidationResult(False, f"unusable aspect ratio ({aspect_ratio:.2f})", width, height)

    return ValidationResult(True, None, width, height)
