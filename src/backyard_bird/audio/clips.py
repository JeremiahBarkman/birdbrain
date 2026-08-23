"""Best-recording clip layout and extraction:

    data/audio/best_clips/<scientific-name-slug>/clip.wav

One file per species (see migration 003 for why), always at this same
path — a replacement is a plain overwrite, not a new file, which is
what keeps this from growing into a "vast collection of recordings"
the way one clip per detection would.
"""
from __future__ import annotations

import wave
from pathlib import Path

from backyard_bird.images.cache import species_slug


def species_clip_path(clips_root: Path, scientific_name: str) -> Path:
    return clips_root / species_slug(scientific_name) / "clip.wav"


def extract_clip(
    source_wav: Path,
    start_seconds: float,
    end_seconds: float,
    padding_seconds: float,
    dest_path: Path,
) -> None:
    """Slice [start_seconds - padding, end_seconds + padding] out of
    source_wav and write it to dest_path as its own small WAV file.

    Both ends are clamped into [0, source duration] — BirdNET's own
    offsets are always within the segment it analyzed, but this also
    has to tolerate whatever a caller passes, so it never assumes
    start/end already fit. Written to a temp file and renamed into
    place so a reader (or a crash mid-write) never sees a half-written
    clip at dest_path.
    """
    with wave.open(str(source_wav), "rb") as src:
        frame_rate = src.getframerate()
        duration_seconds = src.getnframes() / frame_rate
        sample_width = src.getsampwidth()
        channels = src.getnchannels()

        clip_start = max(0.0, min(start_seconds - padding_seconds, duration_seconds))
        clip_end = max(clip_start, min(end_seconds + padding_seconds, duration_seconds))
        start_frame = int(clip_start * frame_rate)
        end_frame = int(clip_end * frame_rate)

        src.setpos(start_frame)
        frames = src.readframes(end_frame - start_frame)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".wav.tmp")
    with wave.open(str(tmp_path), "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(sample_width)
        dst.setframerate(frame_rate)
        dst.writeframes(frames)
    tmp_path.replace(dest_path)  # atomic on the same filesystem
