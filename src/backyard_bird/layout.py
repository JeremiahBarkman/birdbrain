"""The one definition of where things live under data/ (§24).

Every module that reads or writes under the data directory resolves
its paths through DataLayout rather than spelling out
`data_directory / "audio" / "best_clips"` itself. That is not tidiness:
`doctor`'s required-directory list and the code that actually writes
the files drifted apart exactly once (doctor created `audio/clips`
while every writer used `audio/best_clips`), and nothing caught it
because `mkdir(parents=True, exist_ok=True)` cannot fail on a name
nobody reads. Sharing the definition makes that class of drift
impossible rather than merely unlikely.

`run/` is deliberately absent from required_directories(): unlike the
rest, it holds transient inter-process state (mic status/gain/device
control files) whose own writers create it on demand, so a fresh
install has no reason to pre-create it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataLayout:
    """Absolute paths for one data directory's contents."""

    root: Path

    @classmethod
    def under(cls, data_directory: Path) -> "DataLayout":
        return cls(root=data_directory)

    # -- audio ---------------------------------------------------------------
    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def incoming(self) -> Path:
        return self.audio / "incoming"

    @property
    def processing(self) -> Path:
        return self.audio / "processing"

    @property
    def processed(self) -> Path:
        return self.audio / "processed"

    @property
    def failed(self) -> Path:
        return self.audio / "failed"

    @property
    def best_clips(self) -> Path:
        """One clip per species, not one per detection (migration 003) —
        hence best_clips/, not clips/. See audio/clips.py for the
        per-species layout inside it."""
        return self.audio / "best_clips"

    # -- everything else -----------------------------------------------------
    @property
    def database(self) -> Path:
        return self.root / "database"

    @property
    def database_path(self) -> Path:
        return self.database / "birds.sqlite3"

    @property
    def images(self) -> Path:
        return self.root / "images"

    @property
    def slideshows(self) -> Path:
        return self.root / "slideshows"

    @property
    def frame_export(self) -> Path:
        """The parent of photo_frame.export_directory's default
        (`./data/frame-export/current`). Only the parent is fixed — the
        export directory itself is configurable, so LocalExportAdapter
        reads it from config rather than from here."""
        return self.root / "frame-export"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def temp(self) -> Path:
        return self.root / "temp"

    @property
    def run(self) -> Path:
        """Transient inter-process state — see the module docstring for
        why this is not in required_directories()."""
        return self.root / "run"

    def mic_status_path(self) -> Path:
        return self.run / "mic_status.json"

    def mic_gain_path(self) -> Path:
        return self.run / "mic_gain.json"

    def mic_device_path(self) -> Path:
        return self.run / "mic_device.json"

    def required_directories(self) -> list[Path]:
        """What a working install must have. `doctor` creates these;
        keep this the single source of that list."""
        return [
            self.incoming,
            self.processing,
            self.processed,
            self.failed,
            self.best_clips,
            self.database,
            self.images,
            self.slideshows,
            self.frame_export,
            self.logs,
            self.temp,
        ]
