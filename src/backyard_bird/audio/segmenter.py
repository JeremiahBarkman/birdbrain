"""Assembles a continuous stream of audio chunks into fixed-length,
overlapping segments (§8.2). Pure and device-free — no sounddevice
import here — so it's fully unit-testable without a microphone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

# §8.3 naming convention: YYYY-MM-DDTHH-MM-SS_<microphone_id>_<sequence>.wav
_FILENAME_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})_(?P<microphone_id>.+)_(?P<sequence>\d{6})\.wav$"
)


def format_segment_filename(started_at_utc: datetime, microphone_id: str, sequence: int) -> str:
    """§8.3 naming. The inverse of parse_segment_filename — kept here as
    the single source of truth so the writer (capture_service) and the
    reader (the Phase 3 analyzer) can never drift apart on format.
    """
    return f"{started_at_utc.strftime('%Y-%m-%dT%H-%M-%S')}_{microphone_id}_{sequence:06d}.wav"


@dataclass(frozen=True)
class SegmentFileInfo:
    recording_started_at_utc: datetime
    microphone_id: str
    sequence: int


def parse_segment_filename(filename: str) -> SegmentFileInfo:
    """Recover a segment's identity from its filename alone — this is
    the only record of it that survives between the capture process
    writing it and a later, separate analyzer process picking it up.
    Second-resolution only (§8.3's format has no sub-second component),
    which is fine for the documented 30s/3s default.
    """
    match = _FILENAME_RE.match(filename)
    if not match:
        raise ValueError(f"Filename doesn't match the §8.3 segment naming convention: {filename!r}")
    started_at = datetime.strptime(match["timestamp"], "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
    return SegmentFileInfo(
        recording_started_at_utc=started_at,
        microphone_id=match["microphone_id"],
        sequence=int(match["sequence"]),
    )


@dataclass(frozen=True)
class Segment:
    """One completed, ready-to-write audio segment."""

    samples: np.ndarray  # shape (n, channels), dtype int16
    sequence: int
    started_at_utc: datetime
    sample_rate: int


class AudioSegmenter:
    """Buffers int16 PCM chunks and yields fixed-length overlapping segments.

    Segment N and N+1 overlap by `overlap_seconds` (§8.2) so a bird call
    spanning a segment boundary isn't clipped out of both files: segment
    starts advance by `stride_seconds = segment_seconds - overlap_seconds`
    rather than by the full segment length.
    """

    def __init__(
        self,
        sample_rate: int,
        channels: int,
        segment_seconds: float,
        overlap_seconds: float,
        stream_started_at_utc: datetime,
        start_sequence: int = 0,
    ) -> None:
        if overlap_seconds >= segment_seconds:
            raise ValueError("overlap_seconds must be less than segment_seconds")
        self.sample_rate = sample_rate
        self.channels = channels
        self.segment_samples = int(round(segment_seconds * sample_rate))
        self.overlap_samples = int(round(overlap_seconds * sample_rate))
        self.stride_samples = self.segment_samples - self.overlap_samples
        self._stream_started_at_utc = stream_started_at_utc
        self._buffer = np.empty((0, channels), dtype=np.int16)
        self._next_sequence = start_sequence
        # Absolute sample index (since stream start) of buffer[0].
        self._buffer_start_sample = 0

    def push(self, chunk: np.ndarray) -> list[Segment]:
        """Feed newly captured samples in; returns any segments now complete.

        Any samples left over when the stream ends (fewer than a full
        segment) are simply never emitted — capped at one segment's
        worth of trailing audio lost per disconnect/stop, which is an
        accepted tradeoff for keeping segments fixed-length.
        """
        if chunk.ndim == 1:
            chunk = chunk.reshape(-1, self.channels)
        self._buffer = np.concatenate([self._buffer, chunk], axis=0)

        completed: list[Segment] = []
        while len(self._buffer) >= self.segment_samples:
            segment_samples = self._buffer[: self.segment_samples]
            started_at = self._stream_started_at_utc + timedelta(
                seconds=self._buffer_start_sample / self.sample_rate
            )
            completed.append(
                Segment(
                    samples=segment_samples.copy(),
                    sequence=self._next_sequence,
                    started_at_utc=started_at,
                    sample_rate=self.sample_rate,
                )
            )
            self._next_sequence += 1
            self._buffer = self._buffer[self.stride_samples :]
            self._buffer_start_sample += self.stride_samples

        return completed
