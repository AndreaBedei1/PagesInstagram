"""Shared pytest fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the project root is importable as `src.*` regardless of CWD.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.paths import Paths  # noqa: E402
from src.database import Database  # noqa: E402


@pytest.fixture
def project_paths() -> Paths:
    return Paths.create(ROOT)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    db = Database.open(tmp_path / "test.sqlite")
    yield db
    db.close()


@pytest.fixture
def approved_content(tmp_db: Database):
    """Insert one approved motivational content and return (db, content_id)."""
    from src.core.enums import ContentStatus

    cid, _ = tmp_db.insert_content(
        dict(
            content_type="motivational",
            language="it",
            text="Un passo alla volta costruisci il tuo domani.",
            normalized_text="un passo alla volta costruisci il tuo domani",
            content_hash="hash-approved-1",
            mood="determined",
            category="costanza",
            caption="Ogni piccola azione conta.",
            hashtags=["#motivazione", "#costanza"],
            background_prompt="soft warm gradient minimal abstract",
            quality_score=0.9,
            status=ContentStatus.APPROVED_FOR_PUBLICATION,
        )
    )
    return tmp_db, cid
