"""Tiny forward-only migration runner.

Migrations are ``*.sql`` files in ``src/database/migrations/`` named
``NNNN_description.sql`` and applied in lexicographic order. Applied versions
are tracked in ``schema_migrations`` so re-running is idempotent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..core.errors import MigrationError
from ..core.timeutils import utcnow_iso

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY,"
        " applied_at TEXT NOT NULL)"
    )
    conn.commit()


def current_version(conn: sqlite3.Connection) -> str | None:
    _ensure_table(conn)
    row = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def apply_migrations(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> list[str]:
    """Apply all pending migrations. Returns the list of versions applied now."""
    migrations_dir = migrations_dir or MIGRATIONS_DIR
    if not migrations_dir.exists():
        raise MigrationError(f"migrations dir not found: {migrations_dir}")
    _ensure_table(conn)
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}

    newly: list[str] = []
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        version = sql_file.stem
        if version in applied:
            continue
        sql = sql_file.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, utcnow_iso()),
            )
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise MigrationError(f"migration {version} failed: {e}") from e
        newly.append(version)
    return newly
