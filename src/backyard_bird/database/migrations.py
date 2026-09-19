"""Schema migrations (§11, CLAUDE.md rule 9: every schema change needs
one). Migrations are plain `<version>_<description>.sql` files under
migrations/, applied in version order, each exactly once, tracked in
schema_migrations.

Migration SQL must use `CREATE TABLE/INDEX IF NOT EXISTS` — see the
comment in apply_migrations() for why that's what makes this safe to
re-run after a crash. `ALTER TABLE ... ADD COLUMN` has no such clause
in SQLite; apply_migrations() tolerates the one error that specific
statement can raise on a crash-recovery re-run instead (see below).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATION_FILENAME_RE = re.compile(r"^(?P<version>\d+)_(?P<description>.+)\.sql$")


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at_utc TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def discover_migrations(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    """Return (version, description, path) tuples sorted by version."""
    found = []
    for path in migrations_dir.glob("*.sql"):
        match = _MIGRATION_FILENAME_RE.match(path.name)
        if not match:
            continue
        found.append((int(match["version"]), match["description"], path))
    return sorted(found, key=lambda item: item[0])


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> list[int]:
    """Apply any not-yet-applied migrations, in order. Returns versions applied.

    Idempotent — safe to call on every startup. sqlite3's
    executescript() implicitly commits before running, so a migration
    can't be wrapped in one atomic transaction together with its
    schema_migrations row; instead each migration's SQL is required to
    be idempotent (IF NOT EXISTS) so that if the process crashes after
    the schema applies but before the version row commits, the next
    run's re-application is a harmless no-op.
    """
    _ensure_schema_migrations_table(conn)
    already_applied = _applied_versions(conn)
    applied_now: list[int] = []

    for version, description, path in discover_migrations(migrations_dir):
        if version in already_applied:
            continue
        try:
            conn.executescript(path.read_text())
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):
                raise
            # Only reachable via the exact crash window this module's
            # docstring describes, for a migration whose DDL is an
            # ALTER TABLE ADD COLUMN: the column already landed on a
            # prior run that crashed before the schema_migrations row
            # below could commit. Recognizing that one specific error
            # and treating it as already-done is what keeps this
            # migration idempotent under that window, the same way
            # CREATE TABLE/INDEX IF NOT EXISTS keeps every other
            # migration idempotent under it.
            logger.warning(
                "migration_add_column_already_applied",
                extra={"event": "migration_add_column_already_applied", "version": version, "error": str(exc)},
            )
        conn.execute(
            "INSERT INTO schema_migrations (version, description, applied_at_utc) VALUES (?, ?, ?)",
            (version, description, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info(
            "migration_applied",
            extra={"event": "migration_applied", "version": version, "description": description},
        )
        applied_now.append(version)

    return applied_now
