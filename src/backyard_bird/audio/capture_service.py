"""Continuous, restart-safe microphone capture (§8, §9).

Opens the configured input device, segments the stream into
overlapping WAV files per §8.2, and writes them atomically into
data/audio/incoming/ per §8.4/§9 (write to a .partial file, rename on
completion — the analyzer, built in Phase 3, ignores .partial files).
Reconnects automatically if the device drops. This must never depend
on network access (§20.1) — it only touches PortAudio and the local
filesystem.

The realtime PortAudio callback (`_on_audio`) only ever copies data
into a queue — all segmenting, file I/O, and retry logic happens on
the calling thread in `run()`, so a slow disk or a full queue can
never block or glitch the audio callback itself.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sounddevice as sd

from backyard_bird.audio.devices import find_input_device
from backyard_bird.audio.retention import enforce_disk_space_floor, sweep_incoming
from backyard_bird.audio.segmenter import AudioSegmenter, Segment, format_segment_filename
from backyard_bird.config import AudioConfig

logger = logging.getLogger(__name__)

_QUEUE_MAX_CHUNKS = 2000  # generous backpressure cap; see _on_audio
_DEVICE_PRESENCE_CHECK_SECONDS = 5.0
_RETENTION_SWEEP_INTERVAL_SECONDS = 3600.0
_RECONNECT_BACKOFF_SECONDS = (2, 5, 10, 30, 60)


class CaptureService:
    """Runs one continuous capture loop until stop() is called."""

    def __init__(
        self,
        audio_config: AudioConfig,
        incoming_dir: Path,
        microphone_id: str | None = None,
    ) -> None:
        self.config = audio_config
        self.incoming_dir = incoming_dir
        self.microphone_id = microphone_id or audio_config.microphone_id
        self._stop_event = threading.Event()
        self._chunk_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        self._last_retention_sweep = 0.0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self, max_segments: int | None = None) -> int:
        """Block, capturing continuously until stop() or max_segments is hit.

        Returns the number of segments written. Pass max_segments=None
        (the default) for production, continuous-until-stopped capture
        (§29 Phase 2 exit condition: 24 hours straight). A small
        max_segments is useful for bounded local smoke tests.
        """
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        segments_written = 0
        attempt = 0

        while not self._stop_event.is_set():
            try:
                device = find_input_device(self.config.device_name)
                if device is None:
                    raise RuntimeError(
                        f"Configured microphone not found: {self.config.device_name!r}"
                    )
                logger.info(
                    "capture_starting",
                    extra={
                        "event": "capture_starting",
                        "device": device.name,
                        "device_index": device.index,
                    },
                )
                segments_written += self._capture_until_error(
                    device.index, max_segments, segments_written
                )
                attempt = 0  # this attempt ran cleanly; reset backoff for any future drop
                if max_segments is not None and segments_written >= max_segments:
                    break
            except Exception as exc:  # noqa: BLE001 — capture must never crash the process
                logger.error(
                    "capture_error",
                    extra={"event": "capture_error", "error": str(exc)},
                    exc_info=True,
                )
                if self._stop_event.is_set():
                    break
                delay = _RECONNECT_BACKOFF_SECONDS[min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)]
                attempt += 1
                logger.info(
                    "capture_reconnect_wait",
                    extra={"event": "capture_reconnect_wait", "delay_seconds": delay, "attempt": attempt},
                )
                self._stop_event.wait(delay)

        logger.info(
            "capture_stopped",
            extra={"event": "capture_stopped", "segments_written": segments_written},
        )
        return segments_written

    # -- internals ---------------------------------------------------

    def _on_audio(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        if status:
            logger.warning(
                "capture_stream_status",
                extra={"event": "capture_stream_status", "status": str(status)},
            )
        try:
            self._chunk_queue.put_nowait(indata.copy())
        except queue.Full:
            # Drop the oldest buffered chunk rather than blocking this
            # realtime callback — blocking here risks further dropouts.
            logger.error("capture_queue_overflow", extra={"event": "capture_queue_overflow"})
            try:
                self._chunk_queue.get_nowait()
                self._chunk_queue.put_nowait(indata.copy())
            except queue.Empty:
                pass

    def _capture_until_error(
        self, device_index: int, max_segments: int | None, already_written: int
    ) -> int:
        stream_started_at = datetime.now(timezone.utc)
        segmenter = AudioSegmenter(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            segment_seconds=self.config.segment_seconds,
            overlap_seconds=self.config.overlap_seconds,
            stream_started_at_utc=stream_started_at,
            start_sequence=already_written,  # keep filenames strictly increasing across reconnects
        )
        written = 0
        blocksize = int(self.config.sample_rate * 0.5)  # 0.5s chunks

        with sd.InputStream(
            device=device_index,
            channels=self.config.channels,
            samplerate=self.config.sample_rate,
            dtype="int16",
            blocksize=blocksize,
            callback=self._on_audio,
        ):
            last_presence_check = time.monotonic()
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now - last_presence_check >= _DEVICE_PRESENCE_CHECK_SECONDS:
                    last_presence_check = now
                    if find_input_device(self.config.device_name) is None:
                        raise RuntimeError(
                            f"Microphone disappeared: {self.config.device_name!r}"
                        )
                if now - self._last_retention_sweep >= _RETENTION_SWEEP_INTERVAL_SECONDS:
                    self._last_retention_sweep = now
                    sweep_incoming(self.incoming_dir, self.config.raw_audio_retention_days)

                # Hard backstop behind the age-based sweep above,
                # checked every iteration — cheap (a statvfs syscall)
                # unless free space has actually dropped below the
                # floor. The analyzer being down, or a burst of
                # capture volume, can outrun retention_days; this
                # guarantees capture never fills the disk regardless
                # (§8.1). enforce_disk_space_floor is a no-op above
                # the floor.
                enforce_disk_space_floor(
                    self.incoming_dir, [self.incoming_dir], self.config.min_free_disk_gb
                )

                try:
                    chunk = self._chunk_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                for segment in segmenter.push(chunk):
                    self._write_segment(segment)
                    written += 1
                    if max_segments is not None and already_written + written >= max_segments:
                        return written

        return written

    def _write_segment(self, segment: Segment) -> None:
        filename = format_segment_filename(segment.started_at_utc, self.microphone_id, segment.sequence)
        final_path = self.incoming_dir / filename
        partial_path = final_path.with_suffix(final_path.suffix + ".partial")

        with wave.open(str(partial_path), "wb") as wav_file:
            wav_file.setnchannels(self.config.channels)
            wav_file.setsampwidth(2)  # 16-bit PCM
            wav_file.setframerate(self.config.sample_rate)
            wav_file.writeframes(segment.samples.astype(np.int16).tobytes())

        partial_path.rename(final_path)  # atomic on the same filesystem — §8.4

        logger.info(
            "segment_written",
            extra={
                "event": "segment_written",
                "file_path": str(final_path),
                "duration_seconds": len(segment.samples) / segment.sample_rate,
                "sequence": segment.sequence,
            },
        )
