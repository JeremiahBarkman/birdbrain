"""Local image cache layout and frame-image resizing (§15.6):

    data/images/species/<scientific-name-slug>/original<ext>
    data/images/species/<scientific-name-slug>/optimized_1920x1080.jpg
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def species_slug(scientific_name: str) -> str:
    return _SLUG_RE.sub("-", scientific_name.strip().lower()).strip("-")


def species_cache_dir(images_root: Path, scientific_name: str) -> Path:
    return images_root / "species" / species_slug(scientific_name)


def original_image_path(images_root: Path, scientific_name: str, extension: str) -> Path:
    return species_cache_dir(images_root, scientific_name) / f"original{extension}"


def optimized_image_path(images_root: Path, scientific_name: str) -> Path:
    return species_cache_dir(images_root, scientific_name) / "optimized_1920x1080.jpg"


def build_optimized_frame_image(content: bytes, target_width: int = 1920, target_height: int = 1080) -> bytes:
    """Cover-fit crop+resize to the frame's target resolution (§16.4:
    "Resize without distortion. Crop intelligently."). No text overlay
    here — that's the Phase 5 slideshow renderer's job; this is just a
    clean base image, also used directly as the dashboard's thumbnail
    until Phase 5 exists.
    """
    with Image.open(io.BytesIO(content)) as img:
        img = img.convert("RGB")
        width, height = img.size
        target_ratio = target_width / target_height
        current_ratio = width / height

        if current_ratio > target_ratio:
            new_width = int(height * target_ratio)
            left = (width - new_width) // 2
            img = img.crop((left, 0, left + new_width, height))
        else:
            new_height = int(width / target_ratio)
            top = (height - new_height) // 2
            img = img.crop((0, top, width, top + new_height))

        img = img.resize((target_width, target_height), Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=88)
        return buffer.getvalue()
