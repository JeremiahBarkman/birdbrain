"""The photo-frame delivery adapter interface (§17.4).

Every delivery method — local export, removable media, a future Uhale
integration if one ever proves feasible — implements this same
interface, so nothing that calls an adapter (a future delivery
scheduler, or the `frame inspect`/`frame test` CLI commands) needs to
know which one is actually configured (§30 rule 3: keep photo-frame
integration behind a dedicated adapter).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from backyard_bird.frame.manifest import SlideshowManifest


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    message: str


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    files_attempted: int
    files_delivered: int
    message: str


@dataclass(frozen=True)
class CleanupResult:
    ok: bool
    removed_count: int
    message: str


@dataclass(frozen=True)
class FrameStatus:
    adapter_name: str
    configured: bool
    detail: str


class PhotoFrameAdapter(ABC):
    @abstractmethod
    def test_connection(self) -> ConnectionResult: ...

    @abstractmethod
    def publish_slideshow(self, slideshow_directory: Path, manifest: SlideshowManifest) -> PublishResult: ...

    @abstractmethod
    def remove_old_slideshows(self, retention_days: int) -> CleanupResult: ...

    @abstractmethod
    def get_status(self) -> FrameStatus: ...
