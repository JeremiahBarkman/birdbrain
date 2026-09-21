"""Ties aggregation + images + the renderer together into a real
slideshow (§29 Phase 5's remaining piece). build_daily_slideshow()
refreshes one local day's daily_species_summary (§14), resolves each
qualifying species' approved image, renders a slide for it
(slideshow/renderer.py), and records the result in slideshows/
slideshow_items (§12.7/12.8) — the piece nothing produced before this,
which is why LocalExportAdapter and the dashboard's fullscreen preview
each had to work around its absence (a synthetic manifest for the
former's tests, a live detections query for the latter).
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backyard_bird.aggregation.service import aggregate_local_date
from backyard_bird.database.repositories import (
    get_approved_images_by_scientific_name,
    get_daily_species_summary,
    replace_slideshow_items,
    upsert_slideshow,
)
from backyard_bird.frame.manifest import SlideshowManifest, SlideshowManifestItem
from backyard_bird.slideshow.renderer import SlideContent, render_slide, slide_filename

logger = logging.getLogger(__name__)

_README_TEXT = """\
This folder is one day's backyard bird slideshow ({local_date}),
{species_count} species.

To display it on the Euphro WF1561 (or any frame/device that can read
images from removable media):

1. Copy every .jpg file in this folder onto a USB drive or SD card.
2. Insert the drive/card into the frame.
3. Select this folder/album as the active slideshow, per the frame's
   own instructions.

manifest.json describes the slides in machine-readable form (species,
detection counts, display order) if you're inspecting this folder
programmatically rather than by hand.
"""


@dataclass(frozen=True)
class BuildResult:
    manifest: SlideshowManifest
    slideshow_directory: Path
    slideshow_id: int


def _order_qualifying(rows: list[tuple[sqlite3.Row, sqlite3.Row]], order: str) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    """§16.5's ordering modes, applied to (daily_species_summary row,
    bird_images row) pairs — get_daily_species_summary already returns
    its rows ordered by first_detected_at_utc, so "first_detection"
    (the default) is a no-op pass-through.
    """
    if order == "most_recent":
        return sorted(rows, key=lambda pair: pair[0]["last_detected_at_utc"], reverse=True)
    if order == "highest_confidence":
        return sorted(rows, key=lambda pair: pair[0]["highest_confidence"], reverse=True)
    if order == "frequency":
        return sorted(rows, key=lambda pair: pair[0]["detection_count"], reverse=True)
    if order == "alphabetical":
        return sorted(rows, key=lambda pair: pair[0]["common_name"].lower())
    if order == "random":
        shuffled = list(rows)
        random.shuffle(shuffled)
        return shuffled
    return rows  # "first_detection" — already this order


def build_daily_slideshow(
    conn: sqlite3.Connection,
    local_date: str,
    tz_name: str,
    order: str,
    display_mode: str,
    slideshow_minimum_confidence: float,
    output_root: Path,
) -> BuildResult:
    """Builds (or rebuilds) one local day's slideshow.

    Idempotent and safe to call repeatedly, including for a date
    that's already been built (CLAUDE.md rules 8/9) — it starts by
    calling aggregate_local_date() to make sure daily_species_summary
    is current, then fully replaces that date's rendered slides,
    slideshows row, and slideshow_items rows rather than trying to
    patch them in place, the same "recompute from source" approach
    aggregate_local_date() itself uses. Not incremental yet — a future
    optimization could skip re-rendering a species whose underlying
    data hasn't changed since the last build (§29 Phase 5's
    "incremental rebuilding" deliverable), but render_slide() is
    already deterministic per slide, which is what that optimization
    would need to be correct.
    """
    aggregate_local_date(conn, local_date, tz_name)
    summary_rows = get_daily_species_summary(conn, local_date)
    approved_by_name = get_approved_images_by_scientific_name(conn)

    qualifying: list[tuple[sqlite3.Row, sqlite3.Row]] = []
    for row in summary_rows:
        if row["highest_confidence"] < slideshow_minimum_confidence:
            continue
        image_row = approved_by_name.get(row["scientific_name"])
        if image_row is None or not image_row["local_file_path"]:
            continue  # no approved image yet — can't be a slide's representative photograph (§16.2)
        qualifying.append((row, image_row))

    ordered = _order_qualifying(qualifying, order)

    slideshow_dir = output_root / local_date
    slideshow_dir.mkdir(parents=True, exist_ok=True)
    for stale_jpg in slideshow_dir.glob("*.jpg"):
        stale_jpg.unlink()  # species from a previous build of this date that no longer qualify

    tz = ZoneInfo(tz_name)
    manifest_items: list[SlideshowManifestItem] = []
    slideshow_item_dicts: list[dict] = []
    for display_order, (row, image_row) in enumerate(ordered, start=1):
        first_local = datetime.fromisoformat(row["first_detected_at_utc"]).astimezone(tz)
        content = SlideContent(
            common_name=row["common_name"],
            scientific_name=row["scientific_name"],
            first_detected_local=first_local,
            detection_count=row["detection_count"],
            highest_confidence=row["highest_confidence"],
            attribution_text=image_row["attribution_text"],
        )
        output_path = slideshow_dir / slide_filename(display_order, row["scientific_name"])
        render_slide(Path(image_row["local_file_path"]), content, output_path, display_mode)

        manifest_items.append(
            SlideshowManifestItem(
                scientific_name=row["scientific_name"],
                common_name=row["common_name"],
                rendered_file_path=output_path,
                display_order=display_order,
                detection_count=row["detection_count"],
                first_detected_at_utc=row["first_detected_at_utc"],
            )
        )
        detection_summary = f"Detected {row['detection_count']} time{'s' if row['detection_count'] != 1 else ''} today"
        slideshow_item_dicts.append(
            {
                "species_id": row["species_id"],
                "bird_image_id": image_row["id"],
                "display_order": display_order,
                "title_text": row["common_name"],
                "subtitle_text": row["scientific_name"],
                "detection_summary_text": detection_summary,
                "rendered_file_path": str(output_path),
            }
        )

    manifest = SlideshowManifest(local_date=local_date, generated_at_utc=datetime.now(timezone.utc), items=manifest_items)
    manifest_path = slideshow_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_json_dict(), indent=2))
    (slideshow_dir / "README.txt").write_text(
        _README_TEXT.format(local_date=local_date, species_count=len(manifest_items))
    )

    with conn:
        slideshow_id = upsert_slideshow(
            conn,
            local_date=local_date,
            status="generated",
            output_directory=str(slideshow_dir),
            manifest_path=str(manifest_path),
            species_count=len(manifest_items),
            image_count=len(manifest_items),
            generated_at_utc=manifest.generated_at_utc.isoformat(),
        )
        replace_slideshow_items(conn, slideshow_id, slideshow_item_dicts)

    logger.info(
        "slideshow_built",
        extra={"event": "slideshow_built", "local_date": local_date, "species_count": len(manifest_items)},
    )
    return BuildResult(manifest=manifest, slideshow_directory=slideshow_dir, slideshow_id=slideshow_id)
