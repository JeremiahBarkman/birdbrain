"""Parameterized SQL access to the detection timeline (§13). This is
the only module that should write raw SQL — everything else gets
typed values in and typed rows/dataclasses back.

Convention: write functions here execute but do NOT commit — callers
control the transaction boundary (typically `with conn:`), so a
multi-step write (e.g. "insert every detection for this segment, then
mark it completed") either lands atomically or not at all. This is
what makes the analyzer's crash recovery correct: a segment stuck in
'processing' after a crash is guaranteed to have no partial detections
sitting behind it. Read-only functions need no transaction and don't
commit either.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- species ---------------------------------------------------------------


def get_or_create_species(conn: sqlite3.Connection, scientific_name: str, common_name: str) -> int:
    row = conn.execute(
        "SELECT id, common_name FROM species WHERE scientific_name = ?", (scientific_name,)
    ).fetchone()
    now = _now_iso()
    if row is None:
        cursor = conn.execute(
            """
            INSERT INTO species (scientific_name, common_name, created_at_utc, updated_at_utc)
            VALUES (?, ?, ?, ?)
            """,
            (scientific_name, common_name, now, now),
        )
        return cursor.lastrowid
    if row["common_name"] != common_name:
        conn.execute(
            "UPDATE species SET common_name = ?, updated_at_utc = ? WHERE id = ?",
            (common_name, now, row["id"]),
        )
    return row["id"]


# -- audio_segments ----------------------------------------------------------


def insert_audio_segment(
    conn: sqlite3.Connection,
    microphone_id: str,
    file_path: str,
    recording_started_at_utc: datetime,
    recording_ended_at_utc: datetime,
    duration_seconds: float,
    sample_rate: int,
    channels: int,
    file_size_bytes: int,
    sha256: str,
) -> int:
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO audio_segments (
            microphone_id, file_path, recording_started_at_utc, recording_ended_at_utc,
            duration_seconds, sample_rate, channels, file_size_bytes, sha256,
            processing_status, processing_attempts, processing_started_at_utc, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', 1, ?, ?)
        """,
        (
            microphone_id,
            file_path,
            recording_started_at_utc.isoformat(),
            recording_ended_at_utc.isoformat(),
            duration_seconds,
            sample_rate,
            channels,
            file_size_bytes,
            sha256,
            now,
            now,
        ),
    )
    return cursor.lastrowid


def find_audio_segment_by_file_path(conn: sqlite3.Connection, file_path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM audio_segments WHERE file_path = ?", (file_path,)).fetchone()


def mark_audio_segment_completed(conn: sqlite3.Connection, segment_id: int, birdnet_version: str) -> None:
    conn.execute(
        """
        UPDATE audio_segments
        SET processing_status = 'completed', processing_completed_at_utc = ?, birdnet_version = ?
        WHERE id = ?
        """,
        (_now_iso(), birdnet_version, segment_id),
    )


def mark_audio_segment_failed(conn: sqlite3.Connection, segment_id: int, error_message: str) -> None:
    conn.execute(
        """
        UPDATE audio_segments
        SET processing_status = 'failed', processing_completed_at_utc = ?, error_message = ?
        WHERE id = ?
        """,
        (_now_iso(), error_message[:2000], segment_id),
    )


def reset_audio_segment_to_pending(conn: sqlite3.Connection, segment_id: int) -> None:
    conn.execute("UPDATE audio_segments SET processing_status = 'pending' WHERE id = ?", (segment_id,))


# -- detections --------------------------------------------------------------


def get_recent_detections_for_species(
    conn: sqlite3.Connection,
    species_id: int,
    microphone_id: str,
    since_utc: datetime,
) -> list[sqlite3.Row]:
    """Non-duplicate detections of this species on this mic since since_utc,
    oldest first — candidates for the deduplicator to compare against.
    microphone_id isn't stored on detections directly, so this joins
    through audio_segments to reach it.
    """
    return conn.execute(
        """
        SELECT d.* FROM detections d
        JOIN audio_segments a ON a.id = d.audio_segment_id
        WHERE d.species_id = ?
          AND a.microphone_id = ?
          AND d.detected_at_utc >= ?
          AND d.is_duplicate = 0
        ORDER BY d.detected_at_utc ASC
        """,
        (species_id, microphone_id, since_utc.isoformat()),
    ).fetchall()


def insert_detection(
    conn: sqlite3.Connection,
    audio_segment_id: int,
    species_id: int,
    detected_at_utc: datetime,
    segment_offset_start_seconds: float,
    segment_offset_end_seconds: float,
    confidence: float,
    sensitivity: float | None,
    latitude: float | None,
    longitude: float | None,
    is_duplicate: bool,
    duplicate_of_detection_id: int | None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO detections (
            audio_segment_id, species_id, detected_at_utc,
            segment_offset_start_seconds, segment_offset_end_seconds,
            confidence, sensitivity, latitude, longitude,
            is_duplicate, duplicate_of_detection_id, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audio_segment_id,
            species_id,
            detected_at_utc.isoformat(),
            segment_offset_start_seconds,
            segment_offset_end_seconds,
            confidence,
            sensitivity,
            latitude,
            longitude,
            int(is_duplicate),
            duplicate_of_detection_id,
            _now_iso(),
        ),
    )
    return cursor.lastrowid


# -- timeline queries (§13) ---------------------------------------------------


@dataclass(frozen=True)
class DetectionRow:
    id: int
    detected_at_utc: str
    scientific_name: str
    common_name: str
    confidence: float
    is_duplicate: bool
    audio_segment_id: int


_DETECTION_SELECT = """
    SELECT d.id, d.detected_at_utc, s.scientific_name, s.common_name,
           d.confidence, d.is_duplicate, d.audio_segment_id
    FROM detections d
    JOIN species s ON s.id = d.species_id
"""


def _row_to_detection(row: sqlite3.Row) -> DetectionRow:
    return DetectionRow(
        id=row["id"],
        detected_at_utc=row["detected_at_utc"],
        scientific_name=row["scientific_name"],
        common_name=row["common_name"],
        confidence=row["confidence"],
        is_duplicate=bool(row["is_duplicate"]),
        audio_segment_id=row["audio_segment_id"],
    )


def list_detections(
    conn: sqlite3.Connection,
    since_utc: datetime | None = None,
    until_utc: datetime | None = None,
    species_query: str | None = None,
    include_duplicates: bool = False,
    limit: int = 100,
) -> list[DetectionRow]:
    clauses = []
    params: list[object] = []
    if since_utc is not None:
        clauses.append("d.detected_at_utc >= ?")
        params.append(since_utc.isoformat())
    if until_utc is not None:
        clauses.append("d.detected_at_utc < ?")
        params.append(until_utc.isoformat())
    if species_query:
        clauses.append("(s.common_name LIKE ? OR s.scientific_name LIKE ?)")
        like = f"%{species_query}%"
        params.extend([like, like])
    if not include_duplicates:
        clauses.append("d.is_duplicate = 0")
    clauses.append("(d.review_status IS NULL OR d.review_status != 'rejected')")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"{_DETECTION_SELECT} {where} ORDER BY d.detected_at_utc DESC LIMIT ?", params
    ).fetchall()
    return [_row_to_detection(r) for r in rows]


@dataclass(frozen=True)
class SpeciesSummaryRow:
    scientific_name: str
    common_name: str
    detection_count: int
    highest_confidence: float
    first_detected_at_utc: str
    last_detected_at_utc: str


def list_species_summary(
    conn: sqlite3.Connection,
    since_utc: datetime | None = None,
    until_utc: datetime | None = None,
    min_confidence: float | None = None,
) -> list[SpeciesSummaryRow]:
    # Filters narrow which underlying detections feed the aggregation
    # (count/highest/first/last), the same clause-building shape as
    # list_detections above — so e.g. a species with zero detections
    # meeting the filter just doesn't appear, rather than showing a
    # zero row, and count/highest/first-seen/last-seen all reflect only
    # the filtered detections rather than the species' full history.
    #
    # The rejected-detection exclusion is what makes reject_species_
    # detections() (below) actually remove a species from this table:
    # this query is entirely detection-driven (no species-level
    # "hidden" flag exists), so a species with every detection rejected
    # naturally produces zero grouped rows for it, same mechanism as a
    # species with zero detections at all.
    clauses = ["d.is_duplicate = 0", "(d.review_status IS NULL OR d.review_status != 'rejected')"]
    params: list[object] = []
    if since_utc is not None:
        clauses.append("d.detected_at_utc >= ?")
        params.append(since_utc.isoformat())
    if until_utc is not None:
        clauses.append("d.detected_at_utc < ?")
        params.append(until_utc.isoformat())
    if min_confidence is not None:
        clauses.append("d.confidence >= ?")
        params.append(min_confidence)

    where = f"WHERE {' AND '.join(clauses)}"
    rows = conn.execute(
        f"""
        SELECT s.scientific_name, s.common_name,
               COUNT(*) AS detection_count,
               MAX(d.confidence) AS highest_confidence,
               MIN(d.detected_at_utc) AS first_detected_at_utc,
               MAX(d.detected_at_utc) AS last_detected_at_utc
        FROM detections d
        JOIN species s ON s.id = d.species_id
        {where}
        GROUP BY s.id
        ORDER BY last_detected_at_utc DESC
        """,
        params,
    ).fetchall()
    return [
        SpeciesSummaryRow(
            scientific_name=r["scientific_name"],
            common_name=r["common_name"],
            detection_count=r["detection_count"],
            highest_confidence=r["highest_confidence"],
            first_detected_at_utc=r["first_detected_at_utc"],
            last_detected_at_utc=r["last_detected_at_utc"],
        )
        for r in rows
    ]


@dataclass(frozen=True)
class DetectionTimestampRow:
    scientific_name: str
    common_name: str
    detected_at_utc: str


def list_detection_timestamps(
    conn: sqlite3.Connection,
    since_utc: datetime | None = None,
    until_utc: datetime | None = None,
    min_confidence: float | None = None,
) -> list[DetectionTimestampRow]:
    """Every non-duplicate, non-rejected detection's timestamp and
    species, filtered identically to list_species_summary above — the
    raw material for the dashboard's hour-of-day heatmap (§13's
    "Hour-of-day activity" query). Returned one row per detection
    rather than pre-aggregated, since bucketing into *local* hours
    needs ZoneInfo conversion the caller does in Python, not something
    SQLite can do against an arbitrary IANA zone.
    """
    clauses = ["d.is_duplicate = 0", "(d.review_status IS NULL OR d.review_status != 'rejected')"]
    params: list[object] = []
    if since_utc is not None:
        clauses.append("d.detected_at_utc >= ?")
        params.append(since_utc.isoformat())
    if until_utc is not None:
        clauses.append("d.detected_at_utc < ?")
        params.append(until_utc.isoformat())
    if min_confidence is not None:
        clauses.append("d.confidence >= ?")
        params.append(min_confidence)

    where = f"WHERE {' AND '.join(clauses)}"
    rows = conn.execute(
        f"""
        SELECT s.scientific_name, s.common_name, d.detected_at_utc
        FROM detections d
        JOIN species s ON s.id = d.species_id
        {where}
        """,
        params,
    ).fetchall()
    return [
        DetectionTimestampRow(
            scientific_name=r["scientific_name"],
            common_name=r["common_name"],
            detected_at_utc=r["detected_at_utc"],
        )
        for r in rows
    ]


# -- species moderation: killing a false/erroneous species from the dashboard ---
#
# Two options, both scoped to the whole species (matching the
# dashboard's species-aggregated table — there's no per-detection UI
# to target one specific detection out of a species' many):
#
#   - reject: soft, reversible in principle. Marks every detection
#     reviewed+rejected rather than removing anything; audio/history
#     stays intact. list_species_summary/list_detections/
#     get_overall_stats above all exclude rejected detections, which
#     is what actually makes the species disappear from the dashboard.
#   - delete: hard, irreversible. Physically removes every detection
#     for the species plus its best_recordings row (whose detection_id
#     FK would otherwise dangle) and that row's audio clip file. The
#     species catalog row and any cached bird_images row are
#     deliberately left alone: they're reference data, not detection
#     records, and a real re-detection later shouldn't have to
#     re-fetch/re-validate an image from scratch.
#
# Both act on species_id, not individual detection ids — callers
# resolve scientific_name -> species_id via
# get_species_id_by_scientific_name (below) first.


def reject_species_detections(conn: sqlite3.Connection, species_id: int) -> int:
    """Returns the number of detections marked rejected (0 if this
    species has none — including an unknown/already-empty species_id,
    which callers should have already checked for via
    get_species_id_by_scientific_name before calling this).
    """
    cursor = conn.execute(
        "UPDATE detections SET is_reviewed = 1, review_status = 'rejected' WHERE species_id = ?",
        (species_id,),
    )
    return cursor.rowcount


def delete_species_detections(conn: sqlite3.Connection, species_id: int) -> dict[str, object]:
    """Irreversible — callers must have already confirmed with the
    user before calling this. best_recordings and daily_species_summary
    are deleted first specifically because their detection_id/
    representative_detection_id FKs reference rows this function is
    about to delete; deleting child-before-parent here (rather than
    relying on any ON DELETE CASCADE, which this schema doesn't
    declare) is what keeps this safe under the FK enforcement §11
    requires (PRAGMA foreign_keys = ON).

    daily_species_summary (§12.5, migration 005) is a derived cache
    rebuilt from detections on the next aggregation run regardless
    (aggregation.service.aggregate_local_date()), so deleting its rows
    for this species loses nothing that isn't about to be stale
    anyway — unlike best_recordings' clip_path, there's no file to
    return or clean up on the caller's behalf here.

    Returns {"detections_deleted": int, "clip_path": str | None} —
    the clip file itself isn't touched here (this module never touches
    the filesystem — see clips.py); callers delete it after this
    transaction commits.
    """
    clip_row = conn.execute(
        "SELECT clip_path FROM best_recordings WHERE species_id = ?", (species_id,)
    ).fetchone()
    clip_path = clip_row["clip_path"] if clip_row is not None else None

    conn.execute("DELETE FROM best_recordings WHERE species_id = ?", (species_id,))
    conn.execute("DELETE FROM daily_species_summary WHERE species_id = ?", (species_id,))
    cursor = conn.execute("DELETE FROM detections WHERE species_id = ?", (species_id,))
    return {"detections_deleted": cursor.rowcount, "clip_path": clip_path}


@dataclass(frozen=True)
class OverallStats:
    """Headline numbers for the live dashboard (§22.1)."""

    total_species: int
    total_detections: int
    species_today: int
    detections_today: int
    most_recent: DetectionRow | None


_NOT_REJECTED = "(review_status IS NULL OR review_status != 'rejected')"


def get_overall_stats(conn: sqlite3.Connection, today_start_utc: datetime) -> OverallStats:
    total_species = conn.execute(
        f"SELECT COUNT(DISTINCT species_id) AS c FROM detections WHERE is_duplicate = 0 AND {_NOT_REJECTED}"
    ).fetchone()["c"]
    total_detections = conn.execute(
        f"SELECT COUNT(*) AS c FROM detections WHERE is_duplicate = 0 AND {_NOT_REJECTED}"
    ).fetchone()["c"]
    species_today = conn.execute(
        f"SELECT COUNT(DISTINCT species_id) AS c FROM detections "
        f"WHERE is_duplicate = 0 AND {_NOT_REJECTED} AND detected_at_utc >= ?",
        (today_start_utc.isoformat(),),
    ).fetchone()["c"]
    detections_today = conn.execute(
        f"SELECT COUNT(*) AS c FROM detections "
        f"WHERE is_duplicate = 0 AND {_NOT_REJECTED} AND detected_at_utc >= ?",
        (today_start_utc.isoformat(),),
    ).fetchone()["c"]
    recent = list_detections(conn, limit=1)

    return OverallStats(
        total_species=total_species,
        total_detections=total_detections,
        species_today=species_today,
        detections_today=detections_today,
        most_recent=recent[0] if recent else None,
    )


# -- bird_images (§12.6, §15) -------------------------------------------------


def insert_bird_image(
    conn: sqlite3.Connection,
    species_id: int,
    source_provider: str,
    original_image_url: str,
    status: str,
    source_page_url: str | None = None,
    local_file_path: str | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    mime_type: str | None = None,
    sha256: str | None = None,
    photographer_name: str | None = None,
    license_name: str | None = None,
    license_url: str | None = None,
    attribution_text: str | None = None,
    search_query: str | None = None,
    suitability_score: float | None = None,
) -> int:
    """source_provider/original_image_url are NOT NULL per §12.6 even
    for 'unavailable' rows recorded when no candidate was ever found —
    callers pass sentinel values ('none' / '') in that case so the
    schema stays exactly as specified rather than being loosened.
    """
    now = _now_iso()
    downloaded_at = now if status == "approved" else None
    cursor = conn.execute(
        """
        INSERT INTO bird_images (
            species_id, source_provider, source_page_url, original_image_url,
            local_file_path, image_width, image_height, mime_type, sha256,
            photographer_name, license_name, license_url, attribution_text,
            search_query, suitability_score, status,
            downloaded_at_utc, last_verified_at_utc, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            species_id,
            source_provider,
            source_page_url,
            original_image_url,
            local_file_path,
            image_width,
            image_height,
            mime_type,
            sha256,
            photographer_name,
            license_name,
            license_url,
            attribution_text,
            search_query,
            suitability_score,
            status,
            downloaded_at,
            now,
            now,
        ),
    )
    return cursor.lastrowid


def list_species_needing_image_search(
    conn: sqlite3.Connection, refresh_after_days: int
) -> list[sqlite3.Row]:
    """§15.1: species with no fresh approved image and no recent
    'unavailable' verdict — the set that needs searching, whether
    triggered by `images fetch-missing`/`refresh` or the automatic
    `images watch` service.

    A species marked 'unavailable' isn't retried immediately (avoids
    hammering the same failed search back-to-back) but — unlike an
    earlier version of this query — isn't stuck that way forever
    either: it becomes eligible again after the same refresh_after_days
    window as an approved image's rotation, since an 'unavailable'
    verdict can just as easily have been a transient failure (a
    provider rate-limit, a network blip) as a genuine no-image-exists
    case. `images refresh` bypasses this window entirely for an
    explicit, immediate retry.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=refresh_after_days)).isoformat()
    return conn.execute(
        """
        SELECT s.id, s.scientific_name, s.common_name
        FROM species s
        WHERE NOT EXISTS (
            SELECT 1 FROM bird_images bi
            WHERE bi.species_id = s.id
              AND bi.status IN ('approved', 'unavailable')
              AND bi.last_verified_at_utc >= ?
        )
        ORDER BY s.id
        """,
        (cutoff,),
    ).fetchall()


def get_approved_images_by_scientific_name(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """One approved image per species (most recently downloaded first),
    keyed by scientific name — the stable species identifier (§12.3) —
    so the dashboard can look images up without an N+1 query per species.
    """
    rows = conn.execute(
        """
        SELECT bi.*, s.scientific_name
        FROM bird_images bi
        JOIN species s ON s.id = bi.species_id
        WHERE bi.status = 'approved'
        ORDER BY bi.downloaded_at_utc DESC
        """
    ).fetchall()
    result: dict[str, sqlite3.Row] = {}
    for row in rows:
        result.setdefault(row["scientific_name"], row)
    return result


# -- best_recordings (human-review audio clips) ------------------------------


def get_species_id_by_scientific_name(conn: sqlite3.Connection, scientific_name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM species WHERE scientific_name = ?", (scientific_name,)
    ).fetchone()
    return row["id"] if row is not None else None


def get_best_recording_confidence(conn: sqlite3.Connection, species_id: int) -> float | None:
    """Cheap read used to decide *before* extracting a clip whether a
    new detection would even become the best one — extraction touches
    the filesystem, so callers (worker.py) skip it entirely unless this
    says the detection would actually win.
    """
    row = conn.execute(
        "SELECT confidence FROM best_recordings WHERE species_id = ?", (species_id,)
    ).fetchone()
    return row["confidence"] if row is not None else None


def upsert_best_recording(
    conn: sqlite3.Connection,
    species_id: int,
    detection_id: int,
    confidence: float,
    clip_path: Path | str,
) -> None:
    """Unconditional — callers are expected to have already checked
    get_best_recording_confidence() and only call this when
    `confidence` beats it (or nothing exists yet). is_approved and
    highpass_hz both reset to their off state: clip_path now points at
    a different physical recording than whatever was starred/filtered
    before, and that recording hasn't been reviewed at any setting yet.
    """
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO best_recordings (
            species_id, detection_id, confidence, clip_path,
            is_approved, approved_at_utc, highpass_hz, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, 0, NULL, 0, ?, ?)
        ON CONFLICT(species_id) DO UPDATE SET
            detection_id = excluded.detection_id,
            confidence = excluded.confidence,
            clip_path = excluded.clip_path,
            is_approved = 0,
            approved_at_utc = NULL,
            highpass_hz = 0,
            updated_at_utc = excluded.updated_at_utc
        """,
        (species_id, detection_id, confidence, str(clip_path), now, now),
    )


def set_best_recording_approved(conn: sqlite3.Connection, species_id: int, approved: bool) -> bool:
    """The star toggle. Returns False if this species has no best
    recording yet — nothing for a human to have listened to and
    approved."""
    cursor = conn.execute(
        "UPDATE best_recordings SET is_approved = ?, approved_at_utc = ? WHERE species_id = ?",
        (int(approved), _now_iso() if approved else None, species_id),
    )
    return cursor.rowcount > 0


def set_best_recording_highpass(conn: sqlite3.Connection, species_id: int, highpass_hz: float) -> bool:
    """The recording modal's high-pass filter selection (user request,
    2026-09-19), persisted per species alongside is_approved. Returns
    False if this species has no best recording yet — same "nothing to
    set this on" semantics as set_best_recording_approved above.
    """
    cursor = conn.execute(
        "UPDATE best_recordings SET highpass_hz = ? WHERE species_id = ?",
        (highpass_hz, species_id),
    )
    return cursor.rowcount > 0


def get_best_recordings_by_scientific_name(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Keyed by scientific name, same shape/reasoning as
    get_approved_images_by_scientific_name above — species_id is an
    internal id the dashboard's JSON layer never otherwise deals in.
    """
    rows = conn.execute(
        """
        SELECT br.*, s.scientific_name
        FROM best_recordings br
        JOIN species s ON s.id = br.species_id
        """
    ).fetchall()
    return {row["scientific_name"]: row for row in rows}


# -- daily_species_summary (§12.5, §14) ---------------------------------------
#
# One row per (local_date, species_id), recomputed from scratch on
# every aggregation run rather than incrementally accumulated —
# detections is the authoritative record (CLAUDE.md rule 21), this
# table is a derived cache of it. That's what makes the aggregation
# job idempotent and restart-safe (CLAUDE.md rule 9/10): re-running it
# for a date it already covered just recomputes the same answer, and a
# detection that gets rejected after its day was first summarized
# naturally drops out on the next run instead of leaving stale state
# behind. See backyard_bird.aggregation.service for the job itself.


@dataclass(frozen=True)
class DailyDetectionAggregateRow:
    species_id: int
    first_detected_at_utc: str
    last_detected_at_utc: str
    detection_count: int
    highest_confidence: float
    representative_detection_id: int


def list_daily_detection_aggregates(
    conn: sqlite3.Connection, start_utc: datetime, end_utc: datetime
) -> list[DailyDetectionAggregateRow]:
    """One row per species with at least one qualifying detection in
    [start_utc, end_utc) — the same is_duplicate/review_status
    exclusion as list_species_summary/list_detections above.
    representative_detection_id is the highest-confidence detection in
    range for that species, ties broken by earliest detection time
    then lowest id, for a fully deterministic pick.
    """
    rows = conn.execute(
        """
        SELECT species_id, id AS representative_detection_id,
               first_detected_at_utc, last_detected_at_utc,
               detection_count, highest_confidence
        FROM (
            SELECT
                d.species_id AS species_id,
                d.id AS id,
                MIN(d.detected_at_utc) OVER (PARTITION BY d.species_id) AS first_detected_at_utc,
                MAX(d.detected_at_utc) OVER (PARTITION BY d.species_id) AS last_detected_at_utc,
                COUNT(*) OVER (PARTITION BY d.species_id) AS detection_count,
                MAX(d.confidence) OVER (PARTITION BY d.species_id) AS highest_confidence,
                ROW_NUMBER() OVER (
                    PARTITION BY d.species_id
                    ORDER BY d.confidence DESC, d.detected_at_utc ASC, d.id ASC
                ) AS rn
            FROM detections d
            WHERE d.is_duplicate = 0
              AND (d.review_status IS NULL OR d.review_status != 'rejected')
              AND d.detected_at_utc >= ? AND d.detected_at_utc < ?
        )
        WHERE rn = 1
        ORDER BY species_id
        """,
        (start_utc.isoformat(), end_utc.isoformat()),
    ).fetchall()
    return [
        DailyDetectionAggregateRow(
            species_id=r["species_id"],
            first_detected_at_utc=r["first_detected_at_utc"],
            last_detected_at_utc=r["last_detected_at_utc"],
            detection_count=r["detection_count"],
            highest_confidence=r["highest_confidence"],
            representative_detection_id=r["representative_detection_id"],
        )
        for r in rows
    ]


def upsert_daily_species_summary(
    conn: sqlite3.Connection,
    local_date: str,
    species_id: int,
    first_detected_at_utc: str,
    last_detected_at_utc: str,
    detection_count: int,
    highest_confidence: float,
    representative_detection_id: int | None,
) -> None:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO daily_species_summary (
            local_date, species_id, first_detected_at_utc, last_detected_at_utc,
            detection_count, highest_confidence, representative_detection_id, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(local_date, species_id) DO UPDATE SET
            first_detected_at_utc = excluded.first_detected_at_utc,
            last_detected_at_utc = excluded.last_detected_at_utc,
            detection_count = excluded.detection_count,
            highest_confidence = excluded.highest_confidence,
            representative_detection_id = excluded.representative_detection_id,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            local_date,
            species_id,
            first_detected_at_utc,
            last_detected_at_utc,
            detection_count,
            highest_confidence,
            representative_detection_id,
            now,
        ),
    )


def delete_daily_species_summary_species_not_in(
    conn: sqlite3.Connection, local_date: str, keep_species_ids: list[int]
) -> None:
    """Prunes stale rows for local_date — species that had a summary
    from a previous run but no longer qualify (e.g. their only
    detection was rejected since). Called before the upserts above so
    a full recompute for a date never leaves orphaned rows behind.
    """
    if not keep_species_ids:
        conn.execute("DELETE FROM daily_species_summary WHERE local_date = ?", (local_date,))
        return
    placeholders = ",".join("?" for _ in keep_species_ids)
    conn.execute(
        f"DELETE FROM daily_species_summary WHERE local_date = ? AND species_id NOT IN ({placeholders})",
        (local_date, *keep_species_ids),
    )


def get_daily_species_summary(conn: sqlite3.Connection, local_date: str) -> list[sqlite3.Row]:
    """All summary rows for one local date, joined with species for
    display — ordered by first detection time (§16.5's default
    slideshow ordering), left to callers that need a different order
    to re-sort.
    """
    return conn.execute(
        """
        SELECT dss.*, s.scientific_name, s.common_name
        FROM daily_species_summary dss
        JOIN species s ON s.id = dss.species_id
        WHERE dss.local_date = ?
        ORDER BY dss.first_detected_at_utc ASC
        """,
        (local_date,),
    ).fetchall()


# -- slideshows / slideshow_items (§12.7/12.8, §29 Phase 5's builder) --------
#
# One slideshows row per local_date (UNIQUE), replaced in place on
# every (re)build — same "recompute from source, not accumulate"
# reasoning as daily_species_summary above: slideshow/builder.py is
# safe to call repeatedly for the same date, and each call's
# slideshow_items fully replace the previous set rather than piling up
# stale rows next to fresh ones.


def upsert_slideshow(
    conn: sqlite3.Connection,
    local_date: str,
    status: str,
    output_directory: str,
    manifest_path: str,
    species_count: int,
    image_count: int,
    generated_at_utc: str,
) -> int:
    """Returns the slideshow's id, whether this was an insert or an
    update — callers need it either way to attach slideshow_items.
    """
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO slideshows (
            local_date, status, output_directory, manifest_path,
            species_count, image_count, generated_at_utc, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(local_date) DO UPDATE SET
            status = excluded.status,
            output_directory = excluded.output_directory,
            manifest_path = excluded.manifest_path,
            species_count = excluded.species_count,
            image_count = excluded.image_count,
            generated_at_utc = excluded.generated_at_utc,
            updated_at_utc = excluded.updated_at_utc
        """,
        (local_date, status, output_directory, manifest_path, species_count, image_count, generated_at_utc, now, now),
    )
    return conn.execute("SELECT id FROM slideshows WHERE local_date = ?", (local_date,)).fetchone()["id"]


def replace_slideshow_items(
    conn: sqlite3.Connection,
    slideshow_id: int,
    items: list[dict],
) -> None:
    """Each dict: species_id, bird_image_id, display_order, title_text,
    subtitle_text, detection_summary_text, rendered_file_path. Deletes
    every existing row for slideshow_id first — a rebuild's item set
    fully replaces the previous one rather than merging with it, since
    display_order and which species qualify can both change between
    builds for the same date.
    """
    conn.execute("DELETE FROM slideshow_items WHERE slideshow_id = ?", (slideshow_id,))
    now = _now_iso()
    conn.executemany(
        """
        INSERT INTO slideshow_items (
            slideshow_id, species_id, bird_image_id, display_order,
            title_text, subtitle_text, detection_summary_text, rendered_file_path, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                slideshow_id,
                item["species_id"],
                item["bird_image_id"],
                item["display_order"],
                item["title_text"],
                item["subtitle_text"],
                item["detection_summary_text"],
                item["rendered_file_path"],
                now,
            )
            for item in items
        ],
    )


def get_slideshow_by_date(conn: sqlite3.Connection, local_date: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM slideshows WHERE local_date = ?", (local_date,)).fetchone()
