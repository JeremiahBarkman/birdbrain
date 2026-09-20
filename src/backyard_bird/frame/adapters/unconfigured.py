"""The safe default adapter (§17.4): used when photo_frame.adapter
names something this codebase doesn't implement, or isn't set at all.

Every operation reports failure/not-configured rather than raising or
silently pretending to succeed — a typo in config.yaml's
photo_frame.adapter should show up clearly in `frame test`/`frame
inspect` output, and once a delivery scheduler exists to call
publish_slideshow() on a schedule, it must never crash the scheduler
either (§20.1: frame failure must never interrupt detection).
"""
from __future__ import annotations

from pathlib import Path

from backyard_bird.frame.base import CleanupResult, ConnectionResult, FrameStatus, PhotoFrameAdapter, PublishResult
from backyard_bird.frame.manifest import SlideshowManifest


class UnconfiguredFrameAdapter(PhotoFrameAdapter):
    def __init__(self, requested_adapter_name: str | None = None) -> None:
        # None means photo_frame.adapter was never set at all; a
        # non-None value that still ends up here means it was set to
        # something service.py's adapter registry doesn't recognize —
        # the two get slightly different messages so a misconfigured
        # name doesn't look identical to a missing one.
        self.requested_adapter_name = requested_adapter_name

    def _reason(self) -> str:
        if self.requested_adapter_name:
            return f"Unknown photo_frame.adapter: {self.requested_adapter_name!r}"
        return "No photo_frame.adapter configured"

    def test_connection(self) -> ConnectionResult:
        return ConnectionResult(ok=False, message=self._reason())

    def publish_slideshow(self, slideshow_directory: Path, manifest: SlideshowManifest) -> PublishResult:
        return PublishResult(ok=False, files_attempted=0, files_delivered=0, message=self._reason())

    def remove_old_slideshows(self, retention_days: int) -> CleanupResult:
        return CleanupResult(ok=False, removed_count=0, message=self._reason())

    def get_status(self) -> FrameStatus:
        return FrameStatus(adapter_name="unconfigured", configured=False, detail=self._reason())
