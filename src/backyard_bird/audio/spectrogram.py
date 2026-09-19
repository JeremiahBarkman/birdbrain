"""PNG spectrogram generation for best-recordings clips (§32's "Live
spectrogram dashboard" future-expansion note, brought forward as a
static per-species visualization rather than a live feed).

Deliberately built on numpy + Pillow rather than adding matplotlib or
scipy: both are already direct project dependencies (numpy is used
throughout audio/, Pillow throughout images/), so this needs no new
dependency (§30 rule 5 / CLAUDE.md rule 5) to compute an STFT and
colorize it.

Same one-file-per-species convention as clips.py: species_spectrogram_
path always resolves to the same path for a given species, and
generate_spectrogram() overwrites it in place, so it never accumulates
history any more than clip.wav does.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from PIL import Image

_FFT_SIZE = 1024
_HOP_SIZE = 256
_MIN_DB = -80.0
_MAX_DB = 0.0

# The dashboard displays this at 220x56 CSS pixels (style.css's
# .spectrogram-wrap) — rendered at 2x that for a sharp look on retina
# displays, then downsampled from the STFT's native resolution (which
# scales with clip length: a 5s clip at these settings is ~940x513).
# Fixing the output size keeps every species' file small and constant
# regardless of clip length, which matters on the 4 GB Pi profile
# (§7.1/§19's resource-conscious quality goal) serving ~100+ of these
# on one dashboard page.
_OUTPUT_WIDTH = 440
_OUTPUT_HEIGHT = 112

# A small "magma"-like gradient (dark purple -> orange -> pale yellow)
# sampled at five stops and linearly interpolated per-channel below —
# a reasonable approximation without pulling in matplotlib for one
# colormap.
_COLORMAP_STOPS = np.array(
    [
        [0.00, 0, 0, 4],
        [0.25, 81, 18, 124],
        [0.50, 183, 55, 121],
        [0.75, 252, 137, 97],
        [1.00, 252, 253, 191],
    ],
    dtype=np.float32,
)


def _colorize(normalized: np.ndarray) -> np.ndarray:
    """normalized: float array in [0, 1], any shape. Returns the same
    shape plus a trailing RGB axis, uint8."""
    positions = _COLORMAP_STOPS[:, 0]
    channels = [np.interp(normalized, positions, _COLORMAP_STOPS[:, i]) for i in (1, 2, 3)]
    return np.stack(channels, axis=-1).astype(np.uint8)


def generate_spectrogram(wav_path: Path, dest_path: Path) -> None:
    """Render a PNG spectrogram of wav_path to dest_path.

    Raises on an unreadable or non-16-bit-PCM source file — callers
    (worker.py) treat spectrogram generation as best-effort and catch
    around this, the same way image/frame failures elsewhere in this
    codebase never block detection recording.
    """
    with wave.open(str(wav_path), "rb") as src:
        sample_width = src.getsampwidth()
        channels = src.getnchannels()
        frames = src.readframes(src.getnframes())

    if sample_width != 2:
        raise ValueError(f"unsupported sample width: {sample_width} bytes (expected 16-bit PCM)")

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if samples.size < _FFT_SIZE:
        samples = np.pad(samples, (0, _FFT_SIZE - samples.size))

    window = np.hanning(_FFT_SIZE)
    n_frames = 1 + (samples.size - _FFT_SIZE) // _HOP_SIZE
    magnitude = np.empty((_FFT_SIZE // 2 + 1, n_frames), dtype=np.float32)
    for i in range(n_frames):
        start = i * _HOP_SIZE
        magnitude[:, i] = np.abs(np.fft.rfft(samples[start : start + _FFT_SIZE] * window))

    reference = magnitude.max()
    if reference <= 0:
        reference = 1.0
    db = 20.0 * np.log10(np.maximum(magnitude / reference, 10 ** (_MIN_DB / 20.0)))
    normalized = np.clip((db - _MIN_DB) / (_MAX_DB - _MIN_DB), 0.0, 1.0)

    # Flip vertically: FFT bin 0 (lowest frequency) belongs at the
    # bottom of the image, but row 0 of a Pillow array is the top.
    pixels = _colorize(normalized)[::-1, :, :]
    image = Image.fromarray(pixels, mode="RGB").resize((_OUTPUT_WIDTH, _OUTPUT_HEIGHT), Image.LANCZOS)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".png.tmp")
    image.save(tmp_path, format="PNG")
    tmp_path.replace(dest_path)  # atomic on the same filesystem
