"""Continuous, restart-safe microphone capture (§8, §9).

Opens the configured input device, segments the stream into
overlapping WAV files per §8.2, and writes them atomically into
data/audio/incoming/ per §8.4/§9 (write to a .partial file, rename on
completion — the analyzer, built in Phase 3, ignores .partial files).
Reconnects automatically if the device drops. This must never depend
on network access (§20.1) — it only touches PortAudio and the local
filesystem.

The realtime PortAudio callback (`_on_audio`) only ever copies data
into a queue — all segmenting and retry logic happens on the calling
thread in `run()`, so a slow disk or a full queue can never block or
glitch the audio callback itself. Segment *file I/O* itself is handed
off to a dedicated writer thread (`_segment_writer_loop`) rather than
run inline in that same loop: writing a completed segment is a
multi-megabyte synchronous disk write, and it used to happen on the
exact thread that also broadcasts chunks to the "Listen Live" relay —
so an occasional slow SD-card write (confirmed live: Raspberry Pi SD
cards routinely spike from ~10ms to well over a second under
wear-leveling/GC) stalled that thread and produced an audible dropout
in Listen Live every time a ~27s segment happened to complete during
one. Moving the write off that thread means Listen Live keeps flowing
even when a write stalls; `run()` still blocks until every queued
write has completed (or failed-and-logged) before returning, so
callers and tests see exactly the same "N segments in, N files on
disk" guarantee as before.

Also writes a live mic-status/level file (audio/levels.py) roughly
once a second, for the dashboard's live level meter (user request,
2026-09-14) — status_path is optional so this is a no-op for any
caller/test that doesn't pass one, same behavior as before this
existed. Same optionality for the live-monitor audio relay
(audio/live_monitor.py, same request): live_monitor_port is optional,
and broadcasting to it is wrapped so a failure there can never affect
capture itself.
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

from backyard_bird.audio.device_control import read_device_control
from backyard_bird.audio.devices import find_input_device
from backyard_bird.audio.gain import apply_gain, clamp_gain, read_gain_control
from backyard_bird.audio.levels import MicLevel, compute_level, write_mic_status
from backyard_bird.audio.live_monitor import LiveMonitorServer, start_live_monitor_server
from backyard_bird.audio.retention import enforce_disk_space_floor, sweep_incoming
from backyard_bird.audio.segmenter import AudioSegmenter, Segment, format_segment_filename
from backyard_bird.config import AudioConfig

logger = logging.getLogger(__name__)

_QUEUE_MAX_CHUNKS = 2000  # generous backpressure cap; see _on_audio
_DEVICE_PRESENCE_CHECK_SECONDS = 5.0
_RETENTION_SWEEP_INTERVAL_SECONDS = 3600.0
_RECONNECT_BACKOFF_SECONDS = (2, 5, 10, 30, 60)
_STATUS_WRITE_INTERVAL_SECONDS = 1.0  # matches the dashboard's ~1x/second poll (user request)
_GAIN_CHECK_INTERVAL_SECONDS = 1.0  # how often to notice a dashboard-adjusted gain (user request)


class CaptureService:
    """Runs one continuous capture loop until stop() is called."""

    def __init__(
        self,
        audio_config: AudioConfig,
        incoming_dir: Path,
        microphone_id: str | None = None,
        status_path: Path | None = None,
        live_monitor_port: int | None = None,
        gain_control_path: Path | None = None,
        device_control_path: Path | None = None,
    ) -> None:
        self.config = audio_config
        self.incoming_dir = incoming_dir
        self.microphone_id = microphone_id or audio_config.microphone_id
        # Optional: existing callers/tests that don't pass status_path
        # just get no live-status reporting, same behavior as before
        # this feature existed.
        self.status_path = status_path
        # Same optionality for the live-monitor relay — None (the
        # default, and what a disabled audio.enable_live_monitor
        # resolves to at the CLI layer) means this feature simply
        # doesn't exist for this instance.
        self.live_monitor_port = live_monitor_port
        # Same optionality again: no gain_control_path means gain is
        # fixed at audio_config.gain for the process lifetime (e.g. in
        # tests), rather than polled live from the dashboard.
        self.gain_control_path = gain_control_path
        # Same optionality again: no device_control_path means the
        # configured device is fixed for the process lifetime, rather
        # than switchable live from the dashboard.
        self.device_control_path = device_control_path
        self._live_monitor_server: LiveMonitorServer | None = None
        self._stop_event = threading.Event()
        self._chunk_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        # Unbounded, deliberately unlike _chunk_queue above — a raw
        # chunk not yet segmented is fine to drop under backpressure
        # (there's more where it came from a moment later), but a
        # completed Segment queued here represents 27-30s of audio
        # that will never exist again if it isn't written. Writes are
        # normally near-instant (see module docstring), so this only
        # ever holds more than one item during an actual slow-disk
        # stall.
        self._segment_write_queue: queue.Queue[Segment | None] = queue.Queue()
        self._segment_writer_thread: threading.Thread | None = None
        self._last_retention_sweep = 0.0
        self._last_status_write = 0.0
        self._last_gain_check = 0.0
        self._current_gain = clamp_gain(audio_config.gain)
        # The device currently in use (or being sought) — distinct from
        # self.config.device_name, which stays whatever the process
        # started with, so a dashboard-requested switch doesn't need to
        # mutate the (otherwise immutable-for-this-run) config object.
        self._current_device_name = audio_config.device_name

    def _write_status(self, status: str, level: MicLevel | None = None, error_message: str | None = None) -> None:
        if self.status_path is None:
            return
        try:
            write_mic_status(
                self.status_path,
                device_name=self._current_device_name,
                status=status,
                level=level,
                error_message=error_message,
            )
        except OSError as exc:  # noqa: BLE001 — status reporting must never affect capture itself
            logger.warning("mic_status_write_failed", extra={"event": "mic_status_write_failed", "error": str(exc)})

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

        # Started once for the service's whole lifetime, not per
        # reconnect attempt below — see module docstring for why
        # segment writes happen here rather than inline in
        # _capture_until_error's loop.
        self._segment_writer_thread = threading.Thread(
            target=self._segment_writer_loop, name="segment-writer", daemon=True
        )
        self._segment_writer_thread.start()

        # Started once for the service's whole lifetime, not per
        # reconnect attempt below — it's independent of any particular
        # PortAudio stream instance, just relaying whatever chunks
        # happen to flow through. Best-effort: None on failure (e.g.
        # the port's already in use) silently disables this feature
        # without affecting capture itself (see live_monitor.py).
        if self.live_monitor_port is not None:
            self._live_monitor_server = start_live_monitor_server(self.live_monitor_port)

        while not self._stop_event.is_set():
            try:
                device = find_input_device(self._current_device_name)
                if device is None:
                    raise RuntimeError(
                        f"Configured microphone not found: {self._current_device_name!r}"
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
                    device.index, self._current_device_name, max_segments, segments_written
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
                self._write_status("error", error_message=str(exc))
                if self._stop_event.is_set():
                    break
                delay = _RECONNECT_BACKOFF_SECONDS[min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)]
                attempt += 1
                logger.info(
                    "capture_reconnect_wait",
                    extra={"event": "capture_reconnect_wait", "delay_seconds": delay, "attempt": attempt},
                )
                self._stop_event.wait(delay)

        if self._live_monitor_server is not None:
            self._live_monitor_server.shutdown_clients()
            self._live_monitor_server.shutdown()
            self._live_monitor_server.server_close()

        # Sentinel + join: block until every segment queued during the
        # loop above has actually been written (or failed-and-logged),
        # so callers/tests see the same "N segments in, N files on
        # disk, nothing left as .partial" guarantee run() always gave
        # before writes moved off this thread.
        self._segment_write_queue.put(None)
        self._segment_writer_thread.join()

        logger.info(
            "capture_stopped",
            extra={"event": "capture_stopped", "segments_written": segments_written},
        )
        self._write_status("stopped")
        return segments_written

    # -- internals ---------------------------------------------------

    def _segment_writer_loop(self) -> None:
        """Runs on its own thread for the lifetime of run() — see
        module docstring. A single worker draining a FIFO queue, so
        segments are still written in the order they were produced;
        just no longer on the same thread that also has to keep
        pulling chunks off _chunk_queue and broadcasting them live.
        """
        while True:
            segment = self._segment_write_queue.get()
            if segment is None:  # sentinel: run() is shutting down
                return
            try:
                self._write_segment(segment)
            except Exception as exc:  # noqa: BLE001 — a write failure must not kill this thread or capture
                logger.error(
                    "segment_write_failed",
                    extra={"event": "segment_write_failed", "sequence": segment.sequence, "error": str(exc)},
                    exc_info=True,
                )

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
        self, device_index: int, device_name: str, max_segments: int | None, already_written: int
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
            self._write_status("capturing")
            last_presence_check = time.monotonic()
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now - last_presence_check >= _DEVICE_PRESENCE_CHECK_SECONDS:
                    last_presence_check = now
                    if self.device_control_path is not None:
                        desired = read_device_control(self.device_control_path, default=device_name)
                        if desired != device_name:
                            # A deliberate dashboard-requested switch,
                            # not a failure — clean return (not raise)
                            # so run()'s loop reopens on the new device
                            # immediately, with no backoff wait and no
                            # "error" status written for what isn't one.
                            logger.info(
                                "capture_device_switch_requested",
                                extra={
                                    "event": "capture_device_switch_requested",
                                    "from_device": device_name,
                                    "to_device": desired,
                                },
                            )
                            self._current_device_name = desired
                            return written
                    if find_input_device(device_name) is None:
                        raise RuntimeError(f"Microphone disappeared: {device_name!r}")
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

                if self.gain_control_path is not None:
                    gain_check_time = time.monotonic()
                    if gain_check_time - self._last_gain_check >= _GAIN_CHECK_INTERVAL_SECONDS:
                        self._last_gain_check = gain_check_time
                        self._current_gain = read_gain_control(self.gain_control_path, default=self._current_gain)
                # Applied here, once, ahead of every downstream use of
                # `chunk` below (the level meter, the live-monitor
                # relay, and the segments actually written to disk) —
                # so raising it from the dashboard boosts all three
                # consistently rather than just the ones a caller
                # remembers to apply it to individually.
                chunk = apply_gain(chunk, self._current_gain)

                if now - self._last_status_write >= _STATUS_WRITE_INTERVAL_SECONDS:
                    self._last_status_write = now
                    self._write_status("capturing", level=compute_level(chunk))

                if self._live_monitor_server is not None and self._live_monitor_server.has_clients():
                    try:
                        self._live_monitor_server.broadcast(chunk.astype(np.int16).tobytes())
                    except Exception as exc:  # noqa: BLE001 — live monitoring must never affect capture
                        logger.warning(
                            "live_monitor_broadcast_failed",
                            extra={"event": "live_monitor_broadcast_failed", "error": str(exc)},
                        )

                for segment in segmenter.push(chunk):
                    # Handed to the writer thread rather than written
                    # inline — see module docstring. `written` counts
                    # segments *produced* (queued for writing), same as
                    # it always has; run() doesn't return until every
                    # one of them is actually flushed to disk.
                    self._segment_write_queue.put(segment)
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
