"""Deterministic content selection for evergreen pages.

Two policies replace the old "pick an approved item never used on this page"
behaviour (which permanently consumed content and depended on ``RANDOM()``):

``cyclic_ordered``
    The page walks its dataset in ``sequence_index`` order, one item per local
    day, and wraps around after ``cycle_length`` days::

        elapsed      = (scheduled_local_date - cycle_anchor_date).days
        sequence     = elapsed % cycle_length
        cycle_number = elapsed // cycle_length

    Same page + same date ⇒ same content, always. Nothing about restarts,
    SQL ordering, later inserts, randomness or retry counts can change it.
    ``cycle_number`` is fed into the background seed, so when the text repeats
    after ``cycle_length`` days the *image* is new.

``calendar_rotating``
    Used by "Oggi nella Storia". Only items whose ``calendar_key`` equals the
    scheduled local date's ``MM-DD`` are eligible — an event is never published
    on the wrong day. Among them the year picks a stable rotation::

        index = (year - anchor_year) % len(candidates)

    so consecutive years show different events for the same calendar day.

Both policies work off the **job's scheduled local date**, never off "now", so
replays, catch-ups and backfills stay reproducible.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date as date_cls

from ..accounts.models import PageConfig

DEFAULT_CYCLE_LENGTH = 1000

#: Policy identifiers accepted by ``content.selection_policy`` in a page YAML.
POLICY_CYCLIC = "cyclic_ordered"
POLICY_CALENDAR = "calendar_rotating"
POLICY_UNUSED = "unused_random"          # legacy behaviour, kept for old pages

SELECTION_POLICIES = (POLICY_CYCLIC, POLICY_CALENDAR, POLICY_UNUSED)


class SelectionError(RuntimeError):
    """Raised when a page cannot produce content for a date (never silent)."""


@dataclass(frozen=True)
class CyclePosition:
    """Where a page stands in its cycle for one specific local date."""

    elapsed_days: int
    sequence_index: int
    cycle_number: int
    cycle_length: int


def parse_date(value: str | date_cls) -> date_cls:
    if isinstance(value, date_cls):
        return value
    return date_cls.fromisoformat(str(value)[:10])


def calendar_key(value: str | date_cls) -> str:
    """``MM-DD`` key used by the calendar-rotating policy (29 February included)."""
    d = parse_date(value)
    return f"{d.month:02d}-{d.day:02d}"


def cycle_position(anchor: str | date_cls, target: str | date_cls,
                   cycle_length: int = DEFAULT_CYCLE_LENGTH) -> CyclePosition:
    """Compute the cycle position of ``target`` relative to ``anchor``.

    Dates before the anchor are handled with Python's floor semantics for ``//``
    and ``%``: ``sequence_index`` stays in ``0..cycle_length-1`` and
    ``cycle_number`` simply goes negative. That keeps backfills deterministic
    instead of raising.
    """
    if cycle_length <= 0:
        raise SelectionError(f"cycle_length must be > 0, got {cycle_length}")
    elapsed = (parse_date(target) - parse_date(anchor)).days
    return CyclePosition(
        elapsed_days=elapsed,
        sequence_index=elapsed % cycle_length,
        cycle_number=elapsed // cycle_length,
        cycle_length=cycle_length,
    )


def page_policy(page: PageConfig) -> str:
    return getattr(page.content, "selection_policy", POLICY_UNUSED) or POLICY_UNUSED


def page_cycle_length(page: PageConfig) -> int:
    return int(getattr(page.content, "cycle_length", DEFAULT_CYCLE_LENGTH)
               or DEFAULT_CYCLE_LENGTH)


def page_anchor(page: PageConfig) -> date_cls:
    raw = getattr(page.content, "cycle_anchor_date", None)
    if not raw:
        raise SelectionError(
            f"page {page.page_id!r}: content.cycle_anchor_date è obbligatorio "
            f"per la policy {page_policy(page)!r}"
        )
    return parse_date(raw)


@dataclass(frozen=True)
class Selection:
    """The resolved content for one page/date, plus its rotation metadata."""

    content: dict
    policy: str
    local_date: str
    sequence_index: int | None = None
    cycle_number: int = 0
    calendar_key: str | None = None

    @property
    def content_id(self) -> int:
        return int(self.content["id"])


def background_seed(*, page_id: str, content_id: int, scheduled_date: str,
                    cycle_number: int, media_type: str, attempt: int = 0) -> int:
    """Deterministic 31-bit seed for the day's background.

    Derived from page, content, scheduled date, cycle number and media type — so
    the same day always regenerates the same image, but the *next* cycle
    (1.000 days later) produces a different one for the same text.
    """
    key = (f"{page_id}|{content_id}|{scheduled_date}|{cycle_number}|"
           f"{media_type}|{attempt}")
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16) % (2**31)


# --------------------------------------------------------------------------
def select_for_date(db, page: PageConfig, local_date: str | date_cls) -> Selection:
    """Resolve the content a page must publish on ``local_date``.

    Raises :class:`SelectionError` when nothing valid exists — the caller routes
    the day to ``NEEDS_REVIEW`` rather than publishing something arbitrary.
    """
    day = parse_date(local_date)
    iso = day.isoformat()
    policy = page_policy(page)

    if policy == POLICY_CYCLIC:
        return _select_cyclic(db, page, day, iso)
    if policy == POLICY_CALENDAR:
        return _select_calendar(db, page, day, iso)
    return _select_unused(db, page, iso)


def _select_cyclic(db, page: PageConfig, day: date_cls, iso: str) -> Selection:
    length = page_cycle_length(page)
    pos = cycle_position(page_anchor(page), day, length)
    row = db.content_by_sequence(page.content_type, pos.sequence_index,
                                 min_quality=page.content.minimum_quality_score)
    if row is None:
        raise SelectionError(
            f"page {page.page_id!r}: nessun contenuto approvato con "
            f"sequence_index={pos.sequence_index} per il tipo "
            f"{page.content_type!r} (data {iso})"
        )
    return Selection(content=row, policy=POLICY_CYCLIC, local_date=iso,
                     sequence_index=pos.sequence_index,
                     cycle_number=pos.cycle_number)


def _select_calendar(db, page: PageConfig, day: date_cls, iso: str) -> Selection:
    key = calendar_key(day)
    rows = db.contents_by_calendar_key(
        page.content_type, key, min_quality=page.content.minimum_quality_score)
    if not rows:
        raise SelectionError(
            f"page {page.page_id!r}: nessun evento approvato per calendar_key="
            f"{key} (data {iso})"
        )
    anchor_year = page_anchor(page).year
    idx = (day.year - anchor_year) % len(rows)
    row = rows[idx]
    if (row.get("calendar_key") or "") != key:      # defensive: never off-day
        raise SelectionError(
            f"page {page.page_id!r}: contenuto {row.get('id')} ha calendar_key "
            f"{row.get('calendar_key')!r} ma la data è {key}"
        )
    return Selection(content=row, policy=POLICY_CALENDAR, local_date=iso,
                     sequence_index=row.get("sequence_index"),
                     cycle_number=max(0, day.year - anchor_year),
                     calendar_key=key)


def _select_unused(db, page: PageConfig, iso: str) -> Selection:
    row = db.pick_unused_content(
        page_id=page.page_id, content_type=page.content_type,
        min_quality=page.content.minimum_quality_score)
    if row is None:
        raise SelectionError(
            f"page {page.page_id!r}: nessun contenuto approvato non ancora usato")
    return Selection(content=row, policy=POLICY_UNUSED, local_date=iso)
