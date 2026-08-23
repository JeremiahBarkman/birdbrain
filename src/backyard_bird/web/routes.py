"""HTTP routes for the local status dashboard (§22).

Every route opens its own short-lived SQLite connection rather than
sharing one across requests — sqlite3 connections aren't safe to share
across threads, and Flask's dev server may handle requests on
different threads. For a page polled every few seconds by at most a
couple of browser tabs, that's negligible overhead and keeps this
correct without any connection-pooling machinery.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory, url_for

from backyard_bird.database.connection import get_connection
from backyard_bird.database.repositories import (
    DetectionRow,
    SpeciesSummaryRow,
    get_approved_images_by_scientific_name,
    get_best_recordings_by_scientific_name,
    get_overall_stats,
    get_species_id_by_scientific_name,
    list_detections,
    list_species_summary,
    set_best_recording_approved,
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


def _recording_json(scientific_name: str, best_recordings_by_name: dict) -> dict | None:
    row = best_recordings_by_name.get(scientific_name)
    if row is None:
        return None
    slug = species_slug(scientific_name)
    return {
        "url": url_for("dashboard.species_clip", slug=slug),
        "download_url": url_for("dashboard.species_clip", slug=slug, download="1"),
        "confidence": row["confidence"],
        "is_approved": bool(row["is_approved"]),
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
