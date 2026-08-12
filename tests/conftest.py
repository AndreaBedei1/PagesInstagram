"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable as `src.*` regardless of CWD.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.paths import Paths  # noqa: E402
from src.database import Database  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _isolate_from_the_operators_env():
    """A test run must not depend on whether this machine is configured.

    ``load_settings`` calls ``load_dotenv`` by default, so the moment a real
    ``.env`` exists any test that goes through the CLI pulls the operator's
    credentials into ``os.environ`` — and every test that runs afterwards sees
    them. That is exactly what happened the first time credentials were filled
    in for a go-live: five tests turned red on a machine where nothing had
    changed but a file the suite is supposed to ignore.

    So dotenv is neutered for the whole session and the credential variables
    are cleared once at the start. A test that wants credentials sets them
    itself; every other test gets the same empty environment CI has.
    """
    import dotenv

    from src.core import settings as settings_mod

    for key in [k for k in os.environ
                if k.startswith(("ICE_", "META_")) and k != "ICE_MODE"]:
        del os.environ[key]

    original = dotenv.load_dotenv
    dotenv.load_dotenv = lambda *a, **k: False
    try:
        yield
    finally:
        dotenv.load_dotenv = original
        del settings_mod  # imported only to fail loudly if the module moves


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
