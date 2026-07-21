"""Timezone-aware time helpers (Europe/Rome by default, DST-correct via zoneinfo).

On Windows the IANA database is provided by the ``tzdata`` package (a dependency).
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    """UTC timestamp in ISO-8601 with 'Z'. Used for all DB timestamps."""
    return now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepts trailing 'Z'); returns aware UTC-ish."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now_in(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def to_utc(dt: datetime, tz_name: str | None = None) -> datetime:
    """Convert a (possibly naive, local) datetime to aware UTC."""
    if dt.tzinfo is None:
        if not tz_name:
            raise ValueError("naive datetime needs tz_name")
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc)


def local_datetime(
    year: int, month: int, day: int, hour: int, minute: int, tz_name: str
) -> datetime:
    """Build a timezone-aware local datetime (DST handled by zoneinfo)."""
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz_name))
