import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from backyard_bird.audio.spectrogram import generate_spectrogram


def _write_wav(
    path: Path, seconds: float = 2.0, sample_rate: int = 48000, sample_width: int = 2, channels: int = 1
) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        n_samples = int(seconds * sample_rate) * channels
        # A real (non-silent) tone, not zeros — exercises the actual
        # FFT/colorize path rather than the reference=0 fallback.
        t = np.arange(n_samples) / sample_rate
        tone = (np.sin(2 * np.pi * 2000 * t) * 20000).astype(np.int16)
        wav_file.writeframes(tone.tobytes())


def test_generate_spectrogram_writes_a_valid_png(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    _write_wav(source)
    dest = tmp_path / "spectrogram.png"

    generate_spectrogram(source, dest)

    assert dest.exists()
    with Image.open(dest) as image:
        image.verify()


def test_generate_spectrogram_output_size_is_fixed_regardless_of_clip_length(tmp_path: Path) -> None:
    # A fixed output size keeps every species' file small and roughly
    # equal, regardless of how long its best-recording clip is — see
    # spectrogram.py's _OUTPUT_WIDTH/_OUTPUT_HEIGHT comment.
    short_source, long_source = tmp_path / "short.wav", tmp_path / "long.wav"
    _write_wav(short_source, seconds=1.0)
    _write_wav(long_source, seconds=8.0)
    short_dest, long_dest = tmp_path / "short.png", tmp_path / "long.png"

    generate_spectrogram(short_source, short_dest)
    generate_spectrogram(long_source, long_dest)

    with Image.open(short_dest) as image:
        assert image.size == (440, 112)
    with Image.open(long_dest) as image:
        assert image.size == (440, 112)


def test_generate_spectrogram_creates_parent_directories(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    _write_wav(source)
    dest = tmp_path / "nested" / "dir" / "spectrogram.png"

    generate_spectrogram(source, dest)

    assert dest.exists()


def test_generate_spectrogram_overwrites_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    _write_wav(source)
    dest = tmp_path / "spectrogram.png"

    generate_spectrogram(source, dest)
    first_bytes = dest.read_bytes()

    _write_wav(source, seconds=4.0)  # different content -> different image
    generate_spectrogram(source, dest)

    assert dest.exists()
    assert list(dest.parent.glob("spectrogram*")) == [dest]  # no stray .tmp/.png.tmp left behind
    with Image.open(dest) as image:
        image.verify()  # still a valid, readable PNG after the overwrite
    assert dest.read_bytes() != first_bytes


def test_generate_spectrogram_handles_silence_without_crashing(tmp_path: Path) -> None:
    source = tmp_path / "silent.wav"
    with wave.open(str(source), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48000)
        wav_file.writeframes(b"\x00\x00" * 48000)  # 1 second of silence
    dest = tmp_path / "spectrogram.png"

    generate_spectrogram(source, dest)

    assert dest.exists()


def test_generate_spectrogram_handles_stereo_input(tmp_path: Path) -> None:
    source = tmp_path / "stereo.wav"
    _write_wav(source, channels=2)
    dest = tmp_path / "spectrogram.png"

    generate_spectrogram(source, dest)

    assert dest.exists()


def test_generate_spectrogram_handles_clip_shorter_than_fft_window(tmp_path: Path) -> None:
    source = tmp_path / "tiny.wav"
    _write_wav(source, seconds=0.005)  # far fewer samples than the 1024-sample FFT window
    dest = tmp_path / "spectrogram.png"

    generate_spectrogram(source, dest)

    assert dest.exists()


def test_generate_spectrogram_rejects_non_16_bit_pcm(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    _write_wav(source, sample_width=1)
    dest = tmp_path / "spectrogram.png"

    with pytest.raises(ValueError):
        generate_spectrogram(source, dest)
