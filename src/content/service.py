"""Content selection for a page.

Two layers:

* :func:`select_content_for_date` — the deterministic entry point used by the
  worker. It dispatches on the page's ``content.selection_policy`` and always
  works off the **scheduled local date** of the job.
* :func:`select_content_for_page` — legacy "pick an approved, unused item"
  helper kept for pages still on ``unused_random`` and for the CLI preview.
"""
from __future__ import annotations

from datetime import date as date_cls

from ..accounts.models import PageConfig
from ..database import Database
from .selection import Selection, SelectionError, select_for_date

__all__ = [
    "select_content_for_date",
    "select_content_for_page",
    "approve_content",
    "reject_content",
    "SelectionError",
    "Selection",
]


def select_content_for_date(db: Database, page: PageConfig,
                            local_date: str | date_cls) -> Selection:
    """Resolve the content this page must publish on ``local_date``.

    Deterministic: same page + same date ⇒ same content, regardless of
    restarts, SQL ordering, later imports, randomness or retry counts.
    """
    return select_for_date(db, page, local_date)


def select_content_for_page(db: Database, page: PageConfig) -> dict | None:
    """Legacy selection: best approved content not yet published for this page."""
    return db.pick_unused_content(
        page_id=page.page_id,
        content_type=page.content_type,
        min_quality=page.content.minimum_quality_score,
    )


def approve_content(db: Database, content_id: int) -> None:
    from ..core.enums import ContentStatus

    db.update_content(content_id, status=ContentStatus.APPROVED_FOR_PUBLICATION)


def reject_content(db: Database, content_id: int) -> None:
    from ..core.enums import ContentStatus

    db.update_content(content_id, status=ContentStatus.REJECTED)
