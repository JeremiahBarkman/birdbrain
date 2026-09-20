"""The slideshow manifest shape frame adapters operate on (§16.4:
"Produce a manifest describing the slideshow").

The daily slideshow builder that actually produces one of these
(§29 Phase 5) doesn't exist yet. This module defines only the shape,
matching what §17.4's PhotoFrameAdapter.publish_slideshow() interface
already commits to taking, so the adapter package (Phase 6, this
module's siblings) can be built and tested independently of the
builder. When Phase 5 lands, it should produce exactly this shape —
revisit this module alongside it if that turns out not to fit, rather
than letting the adapters silently drift out of sync with whatever the
builder ends up producing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SlideshowManifestItem:
    """One slide. Carries the fields an adapter or a human inspecting
    manifest.json actually needs to identify and locate a slide —
    on-slide display copy ("Detected 18 times today", §16.2) is the
    renderer's job when it builds the JPEG, not the manifest's.
    """

    scientific_name: str
    common_name: str
    rendered_file_path: Path
    display_order: int
    detection_count: int
    first_detected_at_utc: str


@dataclass(frozen=True)
class SlideshowManifest:
    """Describes one local calendar day's slideshow (§16.1)."""

    local_date: str  # YYYY-MM-DD
    generated_at_utc: datetime
    items: list[SlideshowManifestItem] = field(default_factory=list)

    @property
    def species_count(self) -> int:
        return len(self.items)

    def to_json_dict(self) -> dict:
        """The exact shape written to manifest.json (§17.5) — file
        paths are recorded as filenames only (`.name`), not full
        source paths, since an adapter copies slides into its own
        destination directory and the manifest describes that
        destination's contents, not wherever the builder originally
        rendered them.
        """
        return {
            "local_date": self.local_date,
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "species_count": self.species_count,
            "slides": [
                {
                    "scientific_name": item.scientific_name,
                    "common_name": item.common_name,
                    "file_name": item.rendered_file_path.name,
                    "display_order": item.display_order,
                    "detection_count": item.detection_count,
                    "first_detected_at_utc": item.first_detected_at_utc,
                }
                for item in sorted(self.items, key=lambda i: i.display_order)
            ],
        }
