"""Determinism you can get out of.

The background seed is derived from page, content, date, cycle, media type and
background attempt, and that determinism is worth keeping: the same day must
produce the same image after a reboot, so a machine that restarts mid-buffer
cannot quietly publish something other than what was reviewed.

The cost was that a failed day was failed for good. Attempts 0, 1 and 2 always
recreate the same three backgrounds, so "regenerate it" was a promise the
pipeline could not keep. ``generation_round`` is the way out, and it is stored
rather than random — otherwise the cure would be worse than the disease.
"""
from __future__ import annotations

from src.content.selection import background_seed

BASE = dict(page_id="curiosita_mondo_it", content_id=4252,
            scheduled_date="2026-09-09", cycle_number=0, media_type="reel")


def test_the_same_round_always_gives_the_same_seed():
    """A restart must not change the media of a day already generated."""
    for attempt in range(3):
        first = background_seed(**BASE, attempt=attempt, generation_round=2)
        second = background_seed(**BASE, attempt=attempt, generation_round=2)
        assert first == second


def test_a_new_round_gives_different_seeds():
    old = {background_seed(**BASE, attempt=a, generation_round=0) for a in range(3)}
    new = {background_seed(**BASE, attempt=a, generation_round=1) for a in range(3)}
    assert not (old & new), "un nuovo round deve produrre sfondi diversi"


def test_each_round_is_distinct_from_every_other():
    seeds = [background_seed(**BASE, attempt=0, generation_round=r)
             for r in range(6)]
    assert len(set(seeds)) == len(seeds)


def test_round_zero_keeps_the_seed_it_always_had():
    """No buffer that was already reviewed may shift because of this change."""
    assert (background_seed(**BASE, attempt=1)
            == background_seed(**BASE, attempt=1, generation_round=0))


def test_the_round_does_not_leak_across_days_or_pages():
    a = background_seed(**BASE, attempt=0, generation_round=3)
    other_day = background_seed(**{**BASE, "scheduled_date": "2026-09-10"},
                                attempt=0, generation_round=3)
    other_page = background_seed(**{**BASE, "page_id": "parola_giorno_it"},
                                 attempt=0, generation_round=3)
    assert a != other_day and a != other_page


def test_seeds_stay_inside_the_range_comfyui_accepts():
    for r in range(12):
        for attempt in range(3):
            seed = background_seed(**BASE, attempt=attempt, generation_round=r)
            assert 0 <= seed < 2 ** 31


# ---- persistence -----------------------------------------------------------
def test_generation_round_is_stored_and_survives_a_reopen(tmp_path):
    """Stored, not drawn: closing and reopening the database must not move it."""
    from src.core.enums import ContentStatus
    from src.database import Database

    path = tmp_path / "round.sqlite"
    db = Database.open(path)
    content_id, _ = db.insert_content(dict(
        content_type="world_curiosity", text="Una curiosità.",
        normalized_text="una curiosita", content_hash="h-round",
        caption="c", hashtags=["#a"], mood="calm", quality_score=0.9,
        status=ContentStatus.APPROVED_FOR_PUBLICATION))
    daily_id, created = db.create_daily_content("curiosita_mondo_it", "2026-09-09",
                                                content_id=content_id)
    assert created
    row = db.get_daily_content("curiosita_mondo_it", "2026-09-09")
    assert row["generation_round"] == 0, "una giornata nasce al round zero"
    db.update_daily_content(daily_id, generation_round=3)
    db.close()

    reopened = Database.open(path)
    again = reopened.get_daily_content("curiosita_mondo_it", "2026-09-09")
    assert again["generation_round"] == 3
    reopened.close()


def test_the_pipeline_asks_for_the_round_it_was_given(project_paths, tmp_db,
                                                      monkeypatch):
    """The seed the pipeline computes must carry the round through."""
    from src.accounts import load_pages
    from src.core.settings import load_settings
    from src.scheduling import pipeline as pipeline_mod

    settings = load_settings(project_paths, load_dotenv=False)
    page = load_pages(project_paths).get("curiosita_mondo_it")
    seen: list[int] = []

    def spy(**kwargs):
        seen.append(kwargs["generation_round"])
        return 12345

    monkeypatch.setattr(pipeline_mod, "background_seed", spy)

    class StopAfterSeed(Exception):
        pass

    class Boom:
        def generate(self, **_kw):
            raise StopAfterSeed

    p = pipeline_mod.GenerationPipeline(settings, tmp_db, bg_generator=Boom())
    content = {"id": 4252, "text": "x", "mood": "calm", "background_prompt": "p"}
    try:
        p._generate_one(page, content, "reel", None, try_comfyui=False,
                        allow_fallback=True, scheduled_date="2026-09-09",
                        cycle_number=0, generation_round=7)
    except StopAfterSeed:
        pass
    assert seen == [7]
