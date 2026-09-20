"""The one delivery method the requirements guarantee will always
exist (§17.5; §30 rule 29: "Preserve a manual export path even after
automated delivery is implemented"). Copies a rendered slideshow into
data/frame-export/current/, ready to copy onto a USB drive or SD card
by hand — no network, no account, no frame-specific protocol. This is
what makes frame delivery work on day one, independent of whatever
happens with the Euphro WF1561's actual Uhale integration.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from backyard_bird.config import PhotoFrameConfig
from backyard_bird.frame.base import CleanupResult, ConnectionResult, FrameStatus, PhotoFrameAdapter, PublishResult
from backyard_bird.frame.manifest import SlideshowManifest

_README_TEXT = """\
This folder is a ready-to-copy export of the current backyard bird
slideshow.

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


class LocalExportAdapter(PhotoFrameAdapter):
    def __init__(self, config: PhotoFrameConfig) -> None:
        self.export_directory = config.export_directory

    def test_connection(self) -> ConnectionResult:
        # No remote endpoint to reach for a plain filesystem export —
        # "connection" here just means "can I actually write to where
        # the export goes."
        try:
            self.export_directory.parent.mkdir(parents=True, exist_ok=True)
            probe = self.export_directory.parent / ".birdbrain-write-test"
            probe.write_text("")
            probe.unlink()
        except OSError as exc:
            return ConnectionResult(ok=False, message=f"Cannot write to {self.export_directory.parent}: {exc}")
        return ConnectionResult(ok=True, message=f"Export directory is writable: {self.export_directory}")

    def publish_slideshow(self, slideshow_directory: Path, manifest: SlideshowManifest) -> PublishResult:
        """Copies every image the manifest lists from slideshow_directory
        into a freshly built replacement for self.export_directory, then
        swaps it into place — the same "build alongside, then replace"
        pattern used elsewhere in this project (§8.4's segment files,
        audio/spectrogram.py's PNGs) so a reader (or a crash mid-copy)
        never sees a half-written export. A directory can't be swapped
        in one atomic syscall the way a single file can, so this uses
        two renames (old -> .previous, new -> current) instead of one —
        each rename is individually atomic, and a crash between them
        leaves the previous export recoverable under the .previous name
        rather than losing it, per §17.6's "keep the last successful
        slideshow active."
        """
        image_paths = [slideshow_directory / item.rendered_file_path.name for item in manifest.items]
        attempted = len(image_paths)

        building_dir = self.export_directory.with_name(self.export_directory.name + ".building")
        if building_dir.exists():
            shutil.rmtree(building_dir)  # a previous build crashed mid-way; start clean
        building_dir.mkdir(parents=True)

        try:
            delivered = 0
            for image_path in image_paths:
                if not image_path.is_file():
                    continue  # missing slide — skip it rather than fail the whole export over one bad file
                shutil.copy2(image_path, building_dir / image_path.name)
                delivered += 1

            (building_dir / "manifest.json").write_text(json.dumps(manifest.to_json_dict(), indent=2))
            (building_dir / "README.txt").write_text(_README_TEXT)

            previous_dir = self.export_directory.with_name(self.export_directory.name + ".previous")
            if previous_dir.exists():
                shutil.rmtree(previous_dir)
            if self.export_directory.exists():
                self.export_directory.rename(previous_dir)
            building_dir.rename(self.export_directory)
            if previous_dir.exists():
                shutil.rmtree(previous_dir)
        except OSError as exc:
            shutil.rmtree(building_dir, ignore_errors=True)
            return PublishResult(
                ok=False, files_attempted=attempted, files_delivered=0, message=f"Export failed: {exc}"
            )

        return PublishResult(
            ok=delivered == attempted,
            files_attempted=attempted,
            files_delivered=delivered,
            message=f"Exported {delivered}/{attempted} slide(s) to {self.export_directory}",
        )

    def remove_old_slideshows(self, retention_days: int) -> CleanupResult:
        # This adapter keeps exactly one export (§17.5's "current"
        # directory, replaced in place on every publish) — there's no
        # dated history under it to prune. A per-day historical
        # archive, if the slideshow builder ever keeps one under
        # data/slideshows/, is that module's own retention job, not
        # this delivery adapter's.
        return CleanupResult(ok=True, removed_count=0, message="local_export keeps no dated history to remove")

    def get_status(self) -> FrameStatus:
        manifest_path = self.export_directory / "manifest.json"
        if not manifest_path.exists():
            return FrameStatus(
                adapter_name="local_export", configured=True, detail=f"No export yet at {self.export_directory}"
            )
        try:
            manifest_data = json.loads(manifest_path.read_text())
            detail = (
                f"{manifest_data.get('species_count', '?')} slide(s) for "
                f"{manifest_data.get('local_date', '?')}, generated {manifest_data.get('generated_at_utc', '?')}"
            )
        except (OSError, ValueError):
            detail = f"manifest.json at {manifest_path} is unreadable"
        return FrameStatus(adapter_name="local_export", configured=True, detail=detail)
