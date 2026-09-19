"""HTTP routes for the local status dashboard (§22).

Every route opens its own short-lived SQLite connection rather than
sharing one across requests — sqlite3 connections aren't safe to share
across threads, and Flask's dev server may handle requests on
different threads. For a page polled every few seconds by at most a
couple of browser tabs, that's negligible overhead and keeps this
correct without any connection-pooling machinery.
"""
from __future__ import annotations

import shutil
import socket
import wave
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_from_directory, url_for

from backyard_bird.audio.device_control import read_device_control, write_device_control
from backyard_bird.audio.devices import list_input_devices
from backyard_bird.audio.gain import GAIN_MAX, GAIN_MIN, read_gain_control, write_gain_control
from backyard_bird.audio.levels import read_mic_status
from backyard_bird.audio.spectrogram import render_spectrogram_bytes, wav_duration_and_rate
from backyard_bird.audio.wav_stream import streaming_wav_header
from backyard_bird.database.connection import get_connection
from backyard_bird.database.repositories import (
    DetectionRow,
    SpeciesSummaryRow,
    delete_species_detections,
    get_approved_images_by_scientific_name,
    get_best_recordings_by_scientific_name,
    get_overall_stats,
    get_species_id_by_scientific_name,
    list_detections,
    list_species_summary,
    reject_species_detections,
    set_best_recording_approved,
    set_best_recording_highpass,
)
from backyard_bird.images.cache import species_slug

bp = Blueprint("dashboard", __name__)


def _connect():
    return get_connection(current_app.config["DB_PATH"])


def _today_start_utc() -> datetime:
    tz = ZoneInfo(current_app.config["TIMEZONE"])
    local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


def _day_range_utc(date_str: str) -> tuple[datetime, datetime]:
    """Local midnight-to-midnight UTC range for one YYYY-MM-DD date, in
    the configured timezone — the arbitrary-date generalization of
    _today_start_utc() above, for the species table's date filter.
    Raises ValueError on a malformed date_str; callers decide how to
    handle that (here: fall back to unfiltered rather than 500).
    """
    tz = ZoneInfo(current_app.config["TIMEZONE"])
    day = date.fromisoformat(date_str)
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _image_url(scientific_name: str, approved_by_name: dict) -> str | None:
    image_row = approved_by_name.get(scientific_name)
    if image_row is None or not image_row["local_file_path"]:
        return None
    filename = Path(image_row["local_file_path"]).name
    return url_for("dashboard.species_image", slug=species_slug(scientific_name), filename=filename)


def _spectrogram_url(slug: str) -> str | None:
    path = Path(current_app.config["AUDIO_CLIPS_ROOT"]) / slug / "spectrogram.png"
    if not path.exists():
        # Generation is best-effort (worker.py) — a missing file means
        # it failed or hasn't run yet, not that the recording is bad.
        return None
    return url_for("dashboard.species_spectrogram", slug=slug)


def _recording_json(scientific_name: str, best_recordings_by_name: dict) -> dict | None:
    row = best_recordings_by_name.get(scientific_name)
    if row is None:
        return None
    slug = species_slug(scientific_name)

    duration_seconds = sample_rate = None
    try:
        duration_seconds, sample_rate = wav_duration_and_rate(Path(row["clip_path"]))
    except (OSError, wave.Error):
        pass  # axis labels just won't render in the modal — never worth failing the whole row over

    return {
        "url": url_for("dashboard.species_clip", slug=slug),
        "download_url": url_for("dashboard.species_clip", slug=slug, download="1"),
        "spectrogram_url": _spectrogram_url(slug),
        "confidence": row["confidence"],
        "is_approved": bool(row["is_approved"]),
        "duration_seconds": duration_seconds,
        "sample_rate": sample_rate,
        "highpass_hz": row["highpass_hz"],
    }


def _detection_json(row: DetectionRow, approved_by_name: dict | None = None) -> dict:
    payload = {
        "detected_at_utc": row.detected_at_utc,
        "common_name": row.common_name,
        "scientific_name": row.scientific_name,
        "confidence": row.confidence,
    }
    if approved_by_name is not None:
        payload["image_url"] = _image_url(row.scientific_name, approved_by_name)
    return payload


def _species_json(row: SpeciesSummaryRow, approved_by_name: dict, best_recordings_by_name: dict) -> dict:
    duration_seen_seconds = None
    try:
        first = datetime.fromisoformat(row.first_detected_at_utc)
        last = datetime.fromisoformat(row.last_detected_at_utc)
        duration_seen_seconds = (last - first).total_seconds()
    except (TypeError, ValueError):
        pass  # malformed/missing timestamp — leave duration unknown rather than guess

    return {
        "common_name": row.common_name,
        "scientific_name": row.scientific_name,
        "detection_count": row.detection_count,
        "confidence": row.avg_confidence,
        "first_detected_at_utc": row.first_detected_at_utc,
        "last_detected_at_utc": row.last_detected_at_utc,
        "duration_seen_seconds": duration_seen_seconds,
        "image_url": _image_url(row.scientific_name, approved_by_name),
        "recording": _recording_json(row.scientific_name, best_recordings_by_name),
    }


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/media/species/<slug>/<filename>")
def species_image(slug: str, filename: str):
    directory = Path(current_app.config["IMAGES_ROOT"]) / "species" / slug
    return send_from_directory(directory, filename)


@bp.route("/media/audio/<slug>/clip.wav")
def species_clip(slug: str):
    # ?download=1 sets Content-Disposition: attachment — the "save the
    # recording" button just links here with that param rather than
    # needing a second route for what's otherwise the same file.
    directory = Path(current_app.config["AUDIO_CLIPS_ROOT"]) / slug
    as_attachment = request.args.get("download") == "1"
    return send_from_directory(
        directory,
        "clip.wav",
        as_attachment=as_attachment,
        download_name=f"{slug}.wav" if as_attachment else None,
    )


@bp.route("/media/audio/<slug>/spectrogram.png")
def species_spectrogram(slug: str):
    directory = Path(current_app.config["AUDIO_CLIPS_ROOT"]) / slug
    highpass = request.args.get("highpass", type=float)
    if highpass:
        # A highpass preview is rendered on demand rather than cached
        # as another file per cutoff — clips are short (a few seconds
        # at most), so recomputing the STFT per request is cheap, and
        # this is a display option a user is actively adjusting, not
        # something worth accumulating variants of on disk for.
        try:
            png_bytes = render_spectrogram_bytes(directory / "clip.wav", highpass_hz=highpass)
        except (OSError, ValueError, wave.Error):
            return send_from_directory(directory, "spectrogram.png")  # fall back to the cached default
        return Response(png_bytes, mimetype="image/png", headers={"Cache-Control": "no-store"})
    return send_from_directory(directory, "spectrogram.png")


@bp.route("/api/mic-devices")
def api_mic_devices():
    """Available input devices for the dashboard's device dropdown
    (user request: "in case there is more than one mic"). Querying
    device metadata doesn't require holding the microphone open, so
    this is safe to call from the dashboard process even while
    capture already has a device open (see audio/devices.py — the
    ALSA one-process-per-device exclusivity only applies to actually
    opening a stream, not to listing what's available).
    """
    try:
        devices = list_input_devices()
    except Exception as exc:  # noqa: BLE001 — device enumeration must never crash the dashboard
        return jsonify({"error": f"Could not list audio devices: {exc}", "devices": [], "current": None})

    current = read_device_control(
        Path(current_app.config["DEVICE_CONTROL_PATH"]), default=current_app.config["DEVICE_DEFAULT"]
    )
    return jsonify(
        {
            "devices": [{"name": d.name, "channels": d.max_input_channels} for d in devices],
            "current": current,
        }
    )


@bp.route("/api/mic-device", methods=["POST"])
def api_set_mic_device():
    """Selects a new capture device (user request). Writes the live
    control file capture_service.py polls — effective within about 5
    seconds, without restarting capture, the same way a gain change
    takes effect (see audio/device_control.py) — and, when the running
    config's on-disk path is known, also persists the choice into
    config.yaml's audio.device_name so a later full restart (e.g.
    after a reboot) keeps using it rather than reverting.
    """
    from backyard_bird.setup_wizard import set_config_value

    payload = request.get_json(silent=True) or {}
    device_name = payload.get("device_name")
    if not isinstance(device_name, str) or not device_name.strip():
        return jsonify({"error": "device_name must be a non-empty string"}), 400

    write_device_control(Path(current_app.config["DEVICE_CONTROL_PATH"]), device_name)

    persisted = False
    config_path = current_app.config.get("CONFIG_PATH")
    if config_path is not None:
        persisted = set_config_value(Path(config_path), "audio", "device_name", device_name)

    return jsonify({"device_name": device_name, "persisted": persisted})


@bp.route("/api/species/<path:scientific_name>/recording/star", methods=["POST"])
def star_recording(scientific_name: str):
    """The star (human-approval) toggle. The client sends the desired
    end state explicitly ({"approved": true/false}) rather than this
    being a stateless flip — avoids a double-click/double-poll race
    landing on the wrong value.
    """
    payload = request.get_json(silent=True) or {}
    approved = bool(payload.get("approved", True))

    conn = _connect()
    try:
        species_id = get_species_id_by_scientific_name(conn, scientific_name)
        if species_id is None:
            return jsonify({"error": "unknown species"}), 404
        with conn:
            updated = set_best_recording_approved(conn, species_id, approved)
        if not updated:
            return jsonify({"error": "no recording to approve for this species"}), 404
    finally:
        conn.close()

    return jsonify({"scientific_name": scientific_name, "is_approved": approved})


@bp.route("/api/species/<path:scientific_name>/recording/highpass", methods=["POST"])
def set_recording_highpass(scientific_name: str):
    """Persists the recording modal's high-pass filter selection (user
    request, 2026-09-19) so reopening a species' recording later
    remembers the setting instead of resetting to "Off" every time —
    same per-species, one-row-per-species home (best_recordings) and
    "client sends the desired end state" pattern as the star toggle
    above.
    """
    payload = request.get_json(silent=True) or {}
    try:
        highpass_hz = float(payload.get("highpass_hz", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "highpass_hz must be a number"}), 400

    conn = _connect()
    try:
        species_id = get_species_id_by_scientific_name(conn, scientific_name)
        if species_id is None:
            return jsonify({"error": "unknown species"}), 404
        with conn:
            updated = set_best_recording_highpass(conn, species_id, highpass_hz)
        if not updated:
            return jsonify({"error": "no recording to set this on for this species"}), 404
    finally:
        conn.close()

    return jsonify({"scientific_name": scientific_name, "highpass_hz": highpass_hz})


@bp.route("/api/species/<path:scientific_name>/reject", methods=["POST"])
def reject_species(scientific_name: str):
    """Soft "kill a false detection" (dashboard action, added at the
    user's request): marks every detection for this species reviewed +
    rejected rather than removing anything — reversible in principle,
    audio/history stays intact. The species then drops out of the
    dashboard (list_species_summary/get_overall_stats/list_detections
    all exclude rejected detections) without any data actually being
    destroyed. The client is expected to have already confirmed with
    the user before calling this.
    """
    conn = _connect()
    try:
        species_id = get_species_id_by_scientific_name(conn, scientific_name)
        if species_id is None:
            return jsonify({"error": "unknown species"}), 404
        with conn:
            affected = reject_species_detections(conn, species_id)
    finally:
        conn.close()

    return jsonify({"scientific_name": scientific_name, "rejected_detections": affected})


@bp.route("/api/species/<path:scientific_name>", methods=["DELETE"])
def delete_species(scientific_name: str):
    """Hard "kill a false detection" (dashboard action, added at the
    user's request): permanently removes every detection for this
    species and its best-recording clip. Irreversible — the client is
    expected to have already confirmed with the user, with wording
    that makes the permanence clear, before calling this.
    """
    conn = _connect()
    try:
        species_id = get_species_id_by_scientific_name(conn, scientific_name)
        if species_id is None:
            return jsonify({"error": "unknown species"}), 404
        with conn:
            result = delete_species_detections(conn, species_id)
    finally:
        conn.close()

    clip_path = result["clip_path"]
    if clip_path:
        # Removes the whole species directory, not just clip.wav — it
        # also holds spectrogram.png (see audio/spectrogram.py), and
        # "delete" is meant to leave nothing behind for this species.
        shutil.rmtree(Path(clip_path).parent, ignore_errors=True)

    return jsonify({"scientific_name": scientific_name, "detections_deleted": result["detections_deleted"]})


@bp.route("/api/mic-status")
def api_mic_status():
    """Live mic device/level info for the dashboard's meter (user
    request, added 2026-09-14) — polled independently from /api/stats
    at a faster ~1x/second cadence, since it's a much smaller, cheaper
    payload (a status file read, not a SQLite query) than the full
    stats/species payload.
    """
    payload = read_mic_status(Path(current_app.config["MIC_STATUS_PATH"]))
    if payload is None:
        # Missing, malformed, or stale (capture not running / crashed
        # / never started) all collapse to the same "unknown" shape —
        # the dashboard shouldn't have to distinguish those to render
        # "no live data" correctly.
        return jsonify(
            {"status": "unknown", "device_name": None, "peak_percent": None, "peak_dbfs": None, "error_message": None}
        )
    return jsonify(
        {
            "status": payload.get("status"),
            "device_name": payload.get("device_name"),
            "peak_percent": payload.get("peak_percent"),
            "peak_dbfs": payload.get("peak_dbfs"),
            "error_message": payload.get("error_message"),
        }
    )


@bp.route("/api/mic-gain", methods=["GET", "POST"])
def api_mic_gain():
    """The software capture gain (user request: the mic defaults quite
    low with no hardware control for it). GET reports the current
    value; POST sets a new one. Both read/write
    data/run/mic_gain.json, which capture_service.py polls roughly
    once a second — so a change here reaches the already-running
    capture process live, without a restart, the same way the live
    level meter already flows data the other direction.
    """
    control_path = Path(current_app.config["GAIN_CONTROL_PATH"])
    default_gain = current_app.config["GAIN_DEFAULT"]

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        try:
            requested = float(payload.get("gain"))
        except (TypeError, ValueError):
            return jsonify({"error": "gain must be a number"}), 400
        applied = write_gain_control(control_path, requested)
    else:
        applied = read_gain_control(control_path, default=default_gain)

    return jsonify({"gain": applied, "min": GAIN_MIN, "max": GAIN_MAX})


@bp.route("/api/monitor/live")
def monitor_live():
    """Real live audio, not a recording — the "listen outside right
    now" dashboard button (user request, 2026-09-14). Proxies the
    local-only relay capture_service.py exposes; see
    audio/live_monitor.py for why a relay is necessary at all (the
    dashboard can't open the microphone itself — ALSA only allows one
    process to hold the device, and capture already does).
    """
    if not current_app.config["LIVE_MONITOR_ENABLED"]:
        return jsonify({"error": "Live monitor is disabled (audio.enable_live_monitor: false)."}), 404

    port = current_app.config["LIVE_MONITOR_PORT"]
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError:
        return jsonify({"error": "Live audio monitor isn't available right now — is capture running?"}), 503

    # Captured before the generator starts, not read from current_app
    # inside it — the generator runs after this view function returns,
    # once outside the request context.
    header = streaming_wav_header(
        sample_rate=current_app.config["AUDIO_SAMPLE_RATE"],
        channels=current_app.config["AUDIO_CHANNELS"],
    )

    def generate():
        try:
            yield header
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                yield data
        finally:
            sock.close()

    return Response(
        generate(),
        mimetype="audio/wav",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@bp.route("/api/stats")
def api_stats():
    # These two filter the species table only (min_confidence, date) —
    # the headline stat cards and "most recent" card above it stay
    # all-time/today-fixed regardless, so the summary numbers never
    # shift under a filter the user might not have noticed they set.
    min_confidence = request.args.get("min_confidence", type=float)
    if min_confidence is not None:
        min_confidence = max(0.0, min(1.0, min_confidence))

    since_utc = until_utc = None
    date_str = request.args.get("date")
    if date_str:
        try:
            since_utc, until_utc = _day_range_utc(date_str)
        except ValueError:
            pass  # malformed date — ignore rather than error a status page over it

    conn = _connect()
    try:
        stats = get_overall_stats(conn, _today_start_utc())
        species = list_species_summary(
            conn, since_utc=since_utc, until_utc=until_utc, min_confidence=min_confidence
        )
        approved_by_name = get_approved_images_by_scientific_name(conn)
        best_recordings_by_name = get_best_recordings_by_scientific_name(conn)
    finally:
        conn.close()

    return jsonify(
        {
            "total_species": stats.total_species,
            "total_detections": stats.total_detections,
            "species_today": stats.species_today,
            "detections_today": stats.detections_today,
            "most_recent": (
                _detection_json(stats.most_recent, approved_by_name) if stats.most_recent else None
            ),
            "species": [_species_json(s, approved_by_name, best_recordings_by_name) for s in species],
        }
    )


@bp.route("/api/detections/recent")
def api_recent_detections():
    conn = _connect()
    try:
        rows = list_detections(conn, limit=20)
    finally:
        conn.close()
    return jsonify([_detection_json(r) for r in rows])
