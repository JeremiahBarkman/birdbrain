"""A streaming (indefinite-length) WAV header, for the live-monitor
dashboard button (user request, 2026-09-14).

Python's stdlib `wave` module can't do this: it needs the final byte
count up front (or a seekable file to patch the header on close),
neither of which is available for an HTTP response that just keeps
sending bytes for as long as someone's listening. The standard
workaround — used here — is to write an ordinary 44-byte PCM WAV
header with the RIFF and data chunk sizes set to a placeholder instead
of a real byte count. Most players, browsers included, treat that as
"keep playing until the stream itself ends" rather than erroring on
the mismatch.

That placeholder is 0x7FFFFFFF (2^31 - 1, the max *signed* 32-bit
value), not the more obvious 0xFFFFFFFF (the max *unsigned* value) —
confirmed live with a real tester on Safari/macOS, 2026-09-19: Safari
rejected the stream outright with `NotSupportedError` the instant
`.play()` was called, before any audio data even arrived, while
Chrome/Firefox played it fine either way. 0xFFFFFFFF, read as a signed
32-bit chunk size (which some parsers' internal size fields are, even
though the WAV spec itself defines them as unsigned), is -1 — an
already-invalid, already-suspicious value some media pipelines refuse
outright rather than tolerate. 0x7FFFFFFF is unambiguous either way
signedness is read, and is the placeholder other streaming-WAV servers
(e.g. Icecast/Shoutcast WAV relays) commonly settle on for exactly
this cross-player compatibility reason.
"""
from __future__ import annotations

import struct

_UNKNOWN_LENGTH = 0x7FFFFFFF
_PCM_FORMAT = 1  # WAVE_FORMAT_PCM


def streaming_wav_header(sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    return (
        b"RIFF"
        + struct.pack("<I", _UNKNOWN_LENGTH)
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH",
            16,  # fmt chunk size for PCM
            _PCM_FORMAT,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        )
        + b"data"
        + struct.pack("<I", _UNKNOWN_LENGTH)
    )
