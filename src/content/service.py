"""Content selection for a page: pick an approved, unused item."""
from __future__ import annotations

from ..accounts.models import PageConfig
from ..database import Database


def select_content_for_page(db: Database, page: PageConfig) -> dict | None:
    """Return the best approved content not yet published for this page.

    Ordering (in SQL) is by quality desc then random, and it excludes anything
    already PUBLISHED for this page — so content is not repeated on a page.
    """
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
