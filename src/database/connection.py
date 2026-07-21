"""SQLite connection factory with WAL, foreign keys and a lock-friendly timeout."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def connect(db_path: str | Path, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open a connection configured for concurrent, robust local use.

    - ``row_factory`` = :class:`sqlite3.Row` (dict-like access).
    - WAL journal so a reader (dashboard) doesn't block the writer (worker).
    - ``foreign_keys=ON`` for referential integrity.
    - ``busy_timeout`` so a briefly-locked DB waits instead of erroring.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=busy_timeout_ms / 1000.0,
        isolation_level="DEFERRED",
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success and rolls back on error."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
