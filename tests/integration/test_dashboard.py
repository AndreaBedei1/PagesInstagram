"""Dashboard route + auth + action tests (FastAPI TestClient)."""
from __future__ import annotations

import pytest

from src.core.enums import ContentStatus
from src.core.settings import load_settings
from src.database import Database

pytestmark = pytest.mark.integration


def _settings(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    s.database.path = str(tmp_path / "dash.sqlite")
    return s


def test_dashboard_auth_and_routes(tmp_path, project_paths, monkeypatch):
    monkeypatch.setenv("ICE_DASHBOARD_TOKEN", "local")
    s = _settings(tmp_path, project_paths)
    db = Database.open(s.db_path())
    cid, _ = db.insert_content(dict(
        content_type="famous_quote", text="Una citazione da rivedere.",
        normalized_text="x", content_hash="dash-1", author="Anonimo",
        caption="...", quality_score=0.7, status=ContentStatus.NEEDS_REVIEW))
    db.close()

    from fastapi.testclient import TestClient

    from src.dashboard.app import create_app
    client = TestClient(create_app(s))

    # auth gate
    assert client.get("/").status_code == 403
    # pages
    for path in ("/?token=local", "/review?token=local", "/jobs?token=local",
                 "/media?token=local"):
        assert client.get(path).status_code == 200

    # approve action mutates status
    r = client.post(f"/content/{cid}/approve?token=local", follow_redirects=False)
    assert r.status_code == 303
    db2 = Database.open(s.db_path())
    assert db2.get_content(cid)["status"] == ContentStatus.APPROVED_FOR_PUBLICATION
    db2.close()


def test_dashboard_pause_page(tmp_path, project_paths, monkeypatch):
    monkeypatch.setenv("ICE_DASHBOARD_TOKEN", "local")
    s = _settings(tmp_path, project_paths)
    from fastapi.testclient import TestClient

    from src.dashboard.app import create_app
    client = TestClient(create_app(s))  # startup registers pages from YAML

    db = Database.open(s.db_path())
    assert db.is_page_paused("pensiero_essenziale_it") is False
    db.close()

    r = client.post("/page/pensiero_essenziale_it/toggle?token=local", follow_redirects=False)
    assert r.status_code == 303
    db = Database.open(s.db_path())
    assert db.is_page_paused("pensiero_essenziale_it") is True
    db.close()
