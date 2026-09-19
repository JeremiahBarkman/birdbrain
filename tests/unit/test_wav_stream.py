from __future__ import annotations

import wave
from io import BytesIO

from backyard_bird.audio.wav_stream import streaming_wav_header


def test_header_is_44_bytes() -> None:
    # The standard fixed size for a canonical PCM WAV header (no extra
    # chunks) — anything else means the struct packing is wrong.
    assert len(streaming_wav_header(48000, 1)) == 44


def test_header_starts_with_riff_wave_and_data_markers() -> None:
    header = streaming_wav_header(48000, 1)
    assert header[0:4] == b"RIFF"
    assert header[8:12] == b"WAVE"
    assert header[12:16] == b"fmt "
    assert header[36:40] == b"data"


def test_header_is_readable_by_the_stdlib_wave_module() -> None:
    # The real point of this header shape: something that already
    # knows how to parse WAV (here, Python's own `wave` reader — a
    # reasonable proxy for "a real player") must accept it and report
    # the correct format, not just "well-formed bytes we made up."
    header = streaming_wav_header(sample_rate=48000, channels=1, bits_per_sample=16)
    fake_pcm_data = b"\x00\x01" * 100
    buffer = BytesIO(header + fake_pcm_data)

    with wave.open(buffer, "rb") as wav_file:
        assert wav_file.getframerate() == 48000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        frames = wav_file.readframes(100)
        assert frames == fake_pcm_data


def test_header_reflects_stereo_and_different_sample_rate() -> None:
    header = streaming_wav_header(sample_rate=44100, channels=2, bits_per_sample=16)
    buffer = BytesIO(header)
    with wave.open(buffer, "rb") as wav_file:
        assert wav_file.getframerate() == 44100
        assert wav_file.getnchannels() == 2


def test_riff_and_data_sizes_use_the_signed_max_placeholder_not_unsigned_max() -> None:
    # Regression test: 0xFFFFFFFF (unsigned max) made Safari reject the
    # live stream outright with NotSupportedError the instant .play()
    # was called (confirmed live by a real tester, 2026-09-19) — read
    # as a signed 32-bit size by some media pipelines, it's -1, an
    # already-invalid value. 0x7FFFFFFF (signed max) is unambiguous
    # either way signedness is read, and played fine on Safari,
    # Chrome, and Firefox alike.
    header = streaming_wav_header(48000, 1)
    riff_size = int.from_bytes(header[4:8], "little")
    data_size = int.from_bytes(header[40:44], "little")
    assert riff_size == 0x7FFFFFFF
    assert data_size == 0x7FFFFFFF
