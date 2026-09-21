"""The 1920x1080 slide renderer (§16.2/16.3/16.4).

Composites text over an already-cropped base photo. Cropping and
resizing to 1920x1080 are images/cache.py's build_optimized_frame_image
job (§15.6) — every approved bird_images row already has that file
cached — so this module only draws the overlay and saves the result;
it does no cropping of its own beyond a defensive fit if it's ever
handed something the wrong size.

Fonts: DejaVu Sans (regular/bold/oblique), bundled under assets/fonts/
rather than added as a package dependency or relying on whatever fonts
happen to be installed on the host OS (§30 rule 5 avoids new
dependencies for a reason, and this project already treats per-OS
resource availability as something to pin down explicitly rather than
assume, e.g. §31.1's audio device naming notes). DejaVu's license
(assets/fonts/LICENSE_DEJAVU.txt) permits redistribution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from backyard_bird.images.cache import species_slug

logger = logging.getLogger(__name__)

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080

_FONTS_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"
_FONT_REGULAR = _FONTS_DIR / "DejaVuSans.ttf"
_FONT_BOLD = _FONTS_DIR / "DejaVuSans-Bold.ttf"
_FONT_OBLIQUE = _FONTS_DIR / "DejaVuSans-Oblique.ttf"

# Safe margins for text (§16.4) — kept clear of the frame's physical
# bezel/overscan on all four sides.
_MARGIN_X = int(FRAME_WIDTH * 0.06)
_MARGIN_BOTTOM = int(FRAME_HEIGHT * 0.07)

# The scrim is taller in informational mode since it has more lines to
# sit behind; both are generous enough for the common case (a one-line
# common name, one-line scientific name, two or three info lines) —
# an unusually long name can still push text to the scrim's edge
# rather than being clipped, since nothing here hard-crops text.
_SCRIM_HEIGHT_INFORMATIONAL = int(FRAME_HEIGHT * 0.40)
_SCRIM_HEIGHT_CLEAN = int(FRAME_HEIGHT * 0.24)
_SCRIM_MAX_ALPHA = 190

_WHITE = (255, 255, 255, 255)
_OFF_WHITE = (225, 225, 225, 255)
_ATTRIBUTION_GRAY = (200, 200, 200, 220)


@dataclass(frozen=True)
class SlideContent:
    """Everything render_slide needs about one species/day (§16.2) to
    draw the text overlay. Producing these values — picking the
    representative image, aggregating the day's detections, choosing
    which species qualify — is the not-yet-written Phase 5 builder's
    job, not this module's.
    """

    common_name: str
    scientific_name: str
    first_detected_local: datetime
    detection_count: int
    highest_confidence: float | None = None
    attribution_text: str | None = None


def slide_filename(display_order: int, scientific_name: str) -> str:
    """A safe filename (§16.4) for one slide, prefixed with its
    display order so a plain directory listing already sorts into
    slideshow order without reading the manifest.
    """
    return f"{display_order:02d}_{species_slug(scientific_name)}.jpg"


@lru_cache(maxsize=None)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _bottom_scrim(width: int, height: int, max_alpha: int) -> Image.Image:
    """A black gradient, transparent at its top edge and max_alpha at
    the bottom — composited under the text block so it stays readable
    over any base photo without a hard-edged bar across it.
    """
    alpha_column = np.linspace(0, max_alpha, height).astype(np.uint8)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 3] = np.tile(alpha_column.reshape(height, 1), (1, width))
    return Image.fromarray(rgba, mode="RGBA")


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines = [words[0]]
    for word in words[1:]:
        candidate = f"{lines[-1]} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    x: int,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    line_height: int,
) -> int:
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _format_time_of_day(moment: datetime) -> str:
    return moment.strftime("%-I:%M %p")


def _format_detection_count(count: int) -> str:
    return f"Detected {count} time{'s' if count != 1 else ''} today"


def render_slide(
    base_image_path: Path,
    content: SlideContent,
    output_path: Path,
    display_mode: Literal["clean", "informational"] = "informational",
) -> Path:
    """Composites one slide and saves it to output_path.

    Deterministic given the same inputs — safe to call repeatedly. A
    slide can always be rebuilt from its source image and content,
    which is what makes a builder's "only re-render newly-qualified
    species" incremental-rebuild logic (§29 Phase 5) correct on top of
    this: this function itself has no notion of "incremental", it just
    always produces the same output for the same input.
    """
    with Image.open(base_image_path) as opened:
        base = opened.convert("RGB")
    if base.size != (FRAME_WIDTH, FRAME_HEIGHT):
        # Defensive only — images/cache.py's build_optimized_frame_image
        # is what's supposed to guarantee this size; a mismatch here
        # means a caller handed this function something unexpected.
        logger.warning(
            "slide_base_image_wrong_size",
            extra={"event": "slide_base_image_wrong_size", "path": str(base_image_path), "size": base.size},
        )
        base = base.resize((FRAME_WIDTH, FRAME_HEIGHT), Image.LANCZOS)

    canvas = base.convert("RGBA")
    scrim_height = _SCRIM_HEIGHT_INFORMATIONAL if display_mode == "informational" else _SCRIM_HEIGHT_CLEAN
    canvas.alpha_composite(
        _bottom_scrim(FRAME_WIDTH, scrim_height, _SCRIM_MAX_ALPHA), dest=(0, FRAME_HEIGHT - scrim_height)
    )

    draw = ImageDraw.Draw(canvas)
    max_text_width = FRAME_WIDTH - 2 * _MARGIN_X

    title_font = _font(str(_FONT_BOLD), 66)
    subtitle_font = _font(str(_FONT_OBLIQUE), 38)
    info_font = _font(str(_FONT_REGULAR), 32)
    attribution_font = _font(str(_FONT_REGULAR), 22)

    y = FRAME_HEIGHT - scrim_height + int(scrim_height * 0.12)
    title_lines = _wrap_text(draw, content.common_name, title_font, max_text_width)
    y = _draw_lines(draw, title_lines, _MARGIN_X, y, title_font, _WHITE, line_height=78)

    subtitle_lines = _wrap_text(draw, content.scientific_name, subtitle_font, max_text_width)
    y = _draw_lines(draw, subtitle_lines, _MARGIN_X, y, subtitle_font, _OFF_WHITE, line_height=46)

    if display_mode == "informational":
        info_lines = [
            f"First heard: {_format_time_of_day(content.first_detected_local)}",
            _format_detection_count(content.detection_count),
        ]
        if content.highest_confidence is not None:
            info_lines.append(f"Highest confidence: {content.highest_confidence * 100:.0f}%")
        _draw_lines(draw, info_lines, _MARGIN_X, y + 10, info_font, _OFF_WHITE, line_height=40)

        if content.attribution_text:
            # Placed unobtrusively (§16.4) in the bottom-right corner,
            # smaller and dimmer than everything else on the slide.
            attribution_lines = _wrap_text(draw, content.attribution_text, attribution_font, max_text_width)
            attribution_y = FRAME_HEIGHT - _MARGIN_BOTTOM - len(attribution_lines) * 28
            for line in attribution_lines:
                line_width = draw.textlength(line, font=attribution_font)
                draw.text(
                    (FRAME_WIDTH - _MARGIN_X - line_width, attribution_y),
                    line,
                    font=attribution_font,
                    fill=_ATTRIBUTION_GRAY,
                )
                attribution_y += 28

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="JPEG", quality=92, optimize=True)
    return output_path
