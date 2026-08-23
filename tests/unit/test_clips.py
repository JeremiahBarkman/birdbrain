import wave
from pathlib import Path

from backyard_bird.audio.clips import extract_clip, species_clip_path


def _write_wav(path: Path, seconds: float, sample_rate: int = 1000) -> None:
    # sample_rate=1000 makes "frame N" == "millisecond N", so tests can
    # reason about exact slice boundaries without huge byte counts.
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytes(range(256)) * 100  # distinguishable, non-silent content
        wav_file.writeframes(frames[: int(seconds * sample_rate) * 2])


def _frame_count(path: Path) -> int:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnframes()


def test_species_clip_path_is_stable_per_species(tmp_path: Path) -> None:
    root = tmp_path / "best_clips"
    a = species_clip_path(root, "Poecile atricapillus")
    b = species_clip_path(root, "Poecile atricapillus")
    assert a == b
    assert a.name == "clip.wav"
    assert "poecile-atricapillus" in str(a)


def test_extract_clip_includes_padding(tmp_path: Path) -> None:
    source = tmp_path / "segment.wav"
    _write_wav(source, seconds=10.0)
    dest = tmp_path / "out" / "clip.wav"

    extract_clip(source, start_seconds=4.0, end_seconds=6.0, padding_seconds=1.0, dest_path=dest)

    assert dest.exists()
    # [3s, 7s] at 1000 frames/sec = 4000 frames.
    assert _frame_count(dest) == 4000


def test_extract_clip_clamps_to_source_bounds(tmp_path: Path) -> None:
    source = tmp_path / "segment.wav"
    _write_wav(source, seconds=3.0)
    dest = tmp_path / "clip.wav"

    # Padding would push this to [-1s, 4s]; source is only 3s long.
    extract_clip(source, start_seconds=0.0, end_seconds=3.0, padding_seconds=1.0, dest_path=dest)

    assert _frame_count(dest) == 3000  # clamped to the full (but no more than the) source


def test_extract_clip_handles_offsets_beyond_source_duration(tmp_path: Path) -> None:
    # Defensive case worker.py relies on: a caller passing offsets that
    # don't fit the actual file must not crash extraction.
    source = tmp_path / "segment.wav"
    _write_wav(source, seconds=3.0)
    dest = tmp_path / "clip.wav"

    extract_clip(source, start_seconds=28.0, end_seconds=30.0, padding_seconds=1.0, dest_path=dest)

    assert dest.exists()
    assert _frame_count(dest) == 0  # both bounds clamp to the end of the file


def test_extract_clip_overwrites_existing_file_atomically(tmp_path: Path) -> None:
    source = tmp_path / "segment.wav"
    _write_wav(source, seconds=10.0)
    dest = tmp_path / "clip.wav"

    extract_clip(source, start_seconds=0.0, end_seconds=1.0, padding_seconds=0.0, dest_path=dest)
    assert _frame_count(dest) == 1000

    extract_clip(source, start_seconds=0.0, end_seconds=5.0, padding_seconds=0.0, dest_path=dest)
    assert _frame_count(dest) == 5000  # replaced, not appended to or left as a second file
    assert list(dest.parent.glob("clip*")) == [dest]
