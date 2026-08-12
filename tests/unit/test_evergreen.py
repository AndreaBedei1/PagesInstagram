"""Requirements coverage for the five evergreen pages.

Each test maps to one of the numbered acceptance requirements: five live pages,
1.000 items per dataset, gap-free cyclic indexes, cycle wrap, restart stability,
a new background on the next cycle, calendar-correct history (29 February
included), no fact-checked content approved without a source, three-layer
deduplication, the five render templates, the rolling buffer, media retention,
secret hygiene and the dry-run job counts.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from PIL import Image

from src.accounts import load_pages
from src.accounts.registry import AccountRegistry
from src.content.dataset_validation import ALL_CALENDAR_KEYS, validate_dataset
from src.content.importer import (CALENDAR_TYPES, CYCLIC_TYPES,
                                  FACT_CHECKED_TYPES, import_dataset)
from src.content.selection import (SelectionError, background_seed,
                                   cycle_position, select_for_date)
from src.core.enums import ContentStatus, JobStatus, Mode
from src.core.settings import load_settings
from src.database import Database
from src.rendering.templates import TEMPLATES, build_fields

FIVE_PAGES = {
    "pensiero_essenziale_it", "curiosita_mondo_it", "parola_giorno_it",
    "oggi_nella_storia_it", "domanda_giorno_it",
}
DATASETS = {
    "philosophical_thoughts_it.json": "philosophical_thought",
    "world_curiosities_it.json": "world_curiosity",
    "words_of_the_day_it.json": "word_of_the_day",
    "today_in_history_it.json": "today_in_history",
    "daily_questions_it.json": "daily_question",
}


def _dataset(paths, name) -> dict:
    return json.loads((paths.datasets / name).read_text(encoding="utf-8"))


# ---- 1/2: pages ----------------------------------------------------------
def test_five_yaml_pages_load(project_paths):
    reg = load_pages(project_paths)
    assert set(reg.ids()) == FIVE_PAGES
    for page in reg.all():
        # pensiero_essenziale_it publishes through a Cloudflare Quick
        # Tunnel: resumable is documented for Facebook Login but answers
        # ProcessingFailedError on that account, so hosted_url is the
        # method that was actually validated end to end. The other four
        # keep the default until each has been through the same proof.
        if page.page_id == "pensiero_essenziale_it":
            assert page.publishing.upload_method == "hosted_url"
            assert (page.publishing.hosted_url_provider
                    == "cloudflare_quick_tunnel")
        else:
            assert page.publishing.upload_method == "resumable"
        assert page.publishing.feed_media_type == "REELS"
        assert page.publishing.share_reel_to_feed is True


def test_exactly_five_active_pages(project_paths):
    assert len(load_pages(project_paths).enabled()) == 5


# ---- 3/4: dataset sizes --------------------------------------------------
@pytest.mark.parametrize("filename,content_type", sorted(DATASETS.items()))
def test_dataset_has_exactly_1000_items(project_paths, filename, content_type):
    data = _dataset(project_paths, filename)
    assert data["content_type"] == content_type
    assert len(data["items"]) == 1000


def test_five_datasets_total_5000_items(project_paths):
    total = sum(len(_dataset(project_paths, f)["items"]) for f in DATASETS)
    assert total == 5000


# ---- 5: gap-free cyclic indexes -----------------------------------------
@pytest.mark.parametrize("filename", sorted(DATASETS))
def test_sequence_indexes_cover_0_to_999(project_paths, filename):
    items = _dataset(project_paths, filename)["items"]
    assert sorted(i["sequence_index"] for i in items) == list(range(1000))


# ---- helpers for DB-backed selection tests ------------------------------
def _seed_cyclic(db: Database, content_type: str, n: int = 1000) -> None:
    for i in range(n):
        db.insert_content(dict(
            content_type=content_type, text=f"Testo numero {i} di prova.",
            normalized_text=f"testo numero {i} di prova", content_hash=f"{content_type}-{i}",
            sequence_index=i, quality_score=0.95, caption="Didascalia di prova.",
            status=ContentStatus.APPROVED_FOR_PUBLICATION))


def _seed_calendar(db: Database, per_day: int = 2) -> None:
    idx = 0
    for key in ALL_CALENDAR_KEYS:
        for k in range(per_day):
            db.insert_content(dict(
                content_type="today_in_history", text=f"Evento {key} numero {k}",
                normalized_text=f"evento {key} {k}", content_hash=f"th-{key}-{k}",
                calendar_key=key, sequence_index=idx, quality_score=0.9,
                caption="Didascalia storica di prova.",
                metadata_json={"year": str(1900 + k), "description": "d"},
                status=ContentStatus.APPROVED_FOR_PUBLICATION))
            idx += 1


def _page(project_paths, page_id):
    return load_pages(project_paths).get(page_id)


# ---- 6: wrap from day 999 to day 0 --------------------------------------
def test_cycle_wraps_from_999_to_0(tmp_db, project_paths):
    _seed_cyclic(tmp_db, "philosophical_thought")
    page = _page(project_paths, "pensiero_essenziale_it")
    anchor = date.fromisoformat(page.content.cycle_anchor_date)
    last = select_for_date(tmp_db, page, anchor + timedelta(days=999))
    first = select_for_date(tmp_db, page, anchor + timedelta(days=1000))
    assert last.sequence_index == 999 and last.cycle_number == 0
    assert first.sequence_index == 0 and first.cycle_number == 1
    assert first.content["id"] == select_for_date(tmp_db, page, anchor).content["id"]


def test_cycle_position_is_pure_arithmetic():
    pos = cycle_position("2026-01-01", "2028-09-27", 1000)
    assert (pos.sequence_index, pos.cycle_number) == (0, 1)
    assert cycle_position("2026-01-01", "2026-01-01").sequence_index == 0


# ---- 7: stability across restarts / inserts / retries -------------------
def test_selection_is_stable_across_restart_and_inserts(tmp_path, project_paths):
    db_path = tmp_path / "stable.sqlite"
    page = _page(project_paths, "pensiero_essenziale_it")
    with Database.open(db_path) as db:
        _seed_cyclic(db, "philosophical_thought")
        first = select_for_date(db, page, "2026-08-04").content["id"]
    with Database.open(db_path) as db:          # simulated restart
        again = select_for_date(db, page, "2026-08-04").content["id"]
        # later inserts must not shift the mapping
        db.insert_content(dict(
            content_type="philosophical_thought", text="Aggiunto dopo.",
            normalized_text="aggiunto dopo", content_hash="later-1",
            sequence_index=5000, quality_score=0.99,
            status=ContentStatus.APPROVED_FOR_PUBLICATION))
        third = select_for_date(db, page, "2026-08-04").content["id"]
    assert first == again == third


# ---- 8: new background on the next cycle --------------------------------
def test_background_seed_changes_with_cycle_number():
    common = dict(page_id="pensiero_essenziale_it", content_id=7,
                  scheduled_date="2026-08-04", media_type="reel")
    assert background_seed(cycle_number=0, **common) != background_seed(
        cycle_number=1, **common)
    assert background_seed(cycle_number=0, **common) == background_seed(
        cycle_number=0, **common)


# ---- 9/10/11: calendar policy -------------------------------------------
def test_history_selection_matches_month_and_day(tmp_db, project_paths):
    _seed_calendar(tmp_db)
    page = _page(project_paths, "oggi_nella_storia_it")
    for iso, key in (("2026-08-04", "08-04"), ("2026-01-01", "01-01"),
                     ("2026-12-31", "12-31")):
        sel = select_for_date(tmp_db, page, iso)
        assert sel.content["calendar_key"] == key


def test_history_supports_29_february(tmp_db, project_paths):
    _seed_calendar(tmp_db)
    page = _page(project_paths, "oggi_nella_storia_it")
    sel = select_for_date(tmp_db, page, "2028-02-29")
    assert sel.content["calendar_key"] == "02-29"


def test_history_rotates_between_years(tmp_db, project_paths):
    _seed_calendar(tmp_db)
    page = _page(project_paths, "oggi_nella_storia_it")
    a = select_for_date(tmp_db, page, "2026-08-04").content["id"]
    b = select_for_date(tmp_db, page, "2027-08-04").content["id"]
    assert a != b


def test_every_calendar_key_has_at_least_two_events(project_paths):
    items = _dataset(project_paths, "today_in_history_it.json")["items"]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["calendar_key"]] = counts.get(it["calendar_key"], 0) + 1
    assert set(counts) == set(ALL_CALENDAR_KEYS)
    assert min(counts.values()) >= 2
    assert counts["02-29"] >= 2


def test_missing_calendar_day_raises_instead_of_publishing_wrong_event(
        tmp_db, project_paths):
    page = _page(project_paths, "oggi_nella_storia_it")
    with pytest.raises(SelectionError):
        select_for_date(tmp_db, page, "2026-08-04")


# ---- 12: nothing factual approved without a source ----------------------
@pytest.mark.parametrize("filename", [
    "world_curiosities_it.json", "words_of_the_day_it.json",
    "today_in_history_it.json"])
def test_fact_checked_datasets_always_carry_a_source(project_paths, filename):
    items = _dataset(project_paths, filename)["items"]
    for it in items:
        assert it["verification_status"] == "verified"
        assert it["source_name"] and it["source_url"].startswith("https://")
        assert it["verified_at"]


def test_unsourced_fact_is_never_auto_approved(tmp_db, tmp_path):
    payload = {"schema_version": 1, "content_type": "world_curiosity",
               "language": "it", "items": [{
                   "text": "Un fatto senza alcuna fonte verificabile alle spalle.",
                   "caption": "Didascalia di prova sufficientemente lunga.",
                   "sequence_index": 0, "hashtags": ["#a", "#b", "#c"],
                   "background_prompt": "x", "status": "approved_for_publication"}]}
    path = tmp_path / "unsourced.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rep = import_dataset(tmp_db, path)
    assert rep.approved == 0 and rep.needs_review == 1
    assert tmp_db.list_contents(content_type="world_curiosity")[0]["status"] == \
        ContentStatus.NEEDS_REVIEW


# ---- 13: exact / fuzzy / semantic dedup ---------------------------------
def test_import_rejects_exact_fuzzy_and_semantic_duplicates(tmp_db, tmp_path):
    base = "La costanza quotidiana trasforma le piccole azioni in risultati solidi."
    items = [
        {"text": base, "caption": "Prima didascalia di prova, abbastanza lunga."},
        {"text": base, "caption": "Duplicato esatto della frase precedente qui."},
        {"text": base.replace("solidi", "solidi ."),
         "caption": "Variante quasi identica della stessa frase di prova."},
        {"text": "In risultati solidi trasforma le piccole azioni la costanza quotidiana.",
         "caption": "Stessa idea con le parole rimescolate, per il fuzzy."},
    ]
    for i, it in enumerate(items):
        it.update(sequence_index=i, hashtags=["#a", "#b", "#c"],
                  background_prompt="x")
    path = tmp_path / "dups.json"
    path.write_text(json.dumps({"schema_version": 1, "content_type": "motivational",
                                "language": "it", "items": items},
                               ensure_ascii=False), encoding="utf-8")
    rep = import_dataset(tmp_db, path)
    assert rep.added == 1
    assert rep.exact_duplicates + rep.near_duplicates == 3


# ---- 14/15: the five render templates -----------------------------------
RENDER_SAMPLES = {
    "philosophical_thought": {
        "id": 1, "text": "Chi decide in fretta sceglie quasi sempre ciò che teme di meno."},
    "world_curiosity": {
        "id": 2, "text": "Il lago Bajkal contiene circa un quinto dell'acqua dolce del pianeta.",
        "category": "geografia",
        "metadata_json": json.dumps({"location": "Siberia, Russia"})},
    "word_of_the_day": {
        "id": 3, "text": "crepuscolo", "metadata_json": json.dumps(
            {"part_of_speech": "sostantivo maschile",
             "definition": "La luce incerta che precede l'alba o segue il tramonto."})},
    "today_in_history": {
        "id": 4, "text": "Viene inaugurato il Canale di Panama", "calendar_key": "08-15",
        "metadata_json": json.dumps({"year": "1914", "description": "La prima nave attraversa il canale."})},
    "daily_question": {
        "id": 5, "sequence_index": 41,
        "text": "Quale abitudine hai smesso di mettere in discussione?"},
}


@pytest.mark.parametrize("template_id", sorted(RENDER_SAMPLES))
def test_each_template_renders_and_passes_quality(tmp_path, project_paths, template_id):
    from src.quality.validator import MediaValidator
    from src.rendering.renderer import Renderer

    s = load_settings(project_paths, load_dotenv=False)
    bg = tmp_path / "bg.png"
    Image.new("RGB", (900, 1600), (238, 234, 226)).save(bg)
    renderer, validator = Renderer(s), MediaValidator(s)
    content = RENDER_SAMPLES[template_id]
    fields = build_fields(template_id, content)

    def render_fn(opts):
        return renderer.render(
            background_path=bg, out_path=tmp_path / f"{template_id}.png",
            text=content["text"], content_type=template_id, aspect="reel",
            template_id=template_id, fields=fields, logo_text="@prova", options=opts)

    res, val, _ = validator.render_until_valid(render_fn)
    assert val.passed, val.issues
    assert res.size == tuple(s.rendering.story_size)
    assert val.contrast >= s.quality.min_contrast_ratio
    assert res.font_size >= s.rendering.min_font_px
    assert len(res.lines) <= s.rendering.max_lines
    assert res.metadata["template"] == template_id


def test_templates_are_structurally_distinct():
    ids = ["philosophical_thought", "world_curiosity", "word_of_the_day",
           "today_in_history", "daily_question"]
    shapes = {tid: tuple((b.key, b.kind, round(b.scale, 3))
                         for b in TEMPLATES[tid].blocks) for tid in ids}
    assert len(set(shapes.values())) == len(ids)


# ---- 16/17: ComfyUI family + no silent fallback in production -----------
def test_sdxl_graph_is_built_for_the_configured_family(project_paths):
    from src.comfyui.workflow import build_graph, family_dims

    s = load_settings(project_paths, load_dotenv=False)
    assert s.comfyui.model_family == "sdxl"
    w, h = family_dims("sdxl", "reel")
    graph = build_graph("sdxl", prompt="p", negative="n", width=w, height=h,
                        seed=1, checkpoint=s.comfyui.checkpoint,
                        steps=None, cfg=None, sampler_name=None, scheduler=None)
    ckpt = next(n for n in graph.values()
                if n["class_type"] == "CheckpointLoaderSimple")
    latent = next(n for n in graph.values()
                  if n["class_type"] == "EmptyLatentImage")
    assert ckpt["inputs"]["ckpt_name"] == s.comfyui.checkpoint
    assert (latent["inputs"]["width"], latent["inputs"]["height"]) == (w, h)
    assert h > w                                   # vertical 9:16 bucket


def test_production_never_falls_back_silently(tmp_db, project_paths):
    from src.core.errors import ComfyUIError
    from src.comfyui.backgrounds import BackgroundGenerator

    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.PRODUCTION
    s.comfyui.url = "http://127.0.0.1:1"           # unreachable
    assert s.comfyui_fallback_allowed() is False
    with pytest.raises(ComfyUIError):
        BackgroundGenerator(s).generate(
            out_path=s.paths.backgrounds / "never.png", background_prompt=None,
            mood=None, profile="philosophy_warm_minimal", aspect="reel",
            allow_fallback=s.comfyui_fallback_allowed(), try_comfyui=True)


# ---- 26: no secrets ------------------------------------------------------
def test_secret_scanner_finds_nothing_in_tracked_files(project_paths):
    from src.security import scan_repository

    res = scan_repository(project_paths.root)
    assert res.gitignore_ok, res.gitignore_issues
    assert res.tracked_env_files == []
    assert res.findings == [], [f.as_dict() for f in res.findings]


def test_secret_scanner_detects_a_planted_token():
    from src.security import scan_text

    token = "EAA" + "b" * 40
    findings = scan_text(f"ICE_X_ACCESS_TOKEN={token}")
    assert findings
    # the finding locates the secret without ever echoing it in full
    assert all(token not in f.excerpt for f in findings)


def test_env_example_contains_all_five_pages_and_no_values(project_paths):
    body = (project_paths.root / ".env.example").read_text(encoding="utf-8")
    for page_id in FIVE_PAGES:
        prefix = "ICE_" + page_id.upper()
        assert f"{prefix}_IG_USER_ID=\n" in body
        assert f"{prefix}_ACCESS_TOKEN=\n" in body
    assert "META_APP_ID=\n" in body and "META_APP_SECRET=\n" in body


# ---- dataset validator is a real gate ------------------------------------
def test_validator_fails_on_a_gap_in_the_sequence(tmp_path):
    items = [{"text": f"Frase di prova numero {i} abbastanza lunga.",
              "caption": "Didascalia di prova sufficientemente lunga.",
              "sequence_index": i if i < 3 else i + 1,
              "hashtags": ["#a", "#b", "#c"], "background_prompt": "x"}
             for i in range(5)]
    path = tmp_path / "gap.json"
    path.write_text(json.dumps({"schema_version": 1, "content_type": "daily_question",
                                "language": "it", "items": items},
                               ensure_ascii=False), encoding="utf-8")
    rep = validate_dataset(path, expected_items=5, check_quality=False)
    assert not rep.ok
    assert any("sequence_index" in e for e in rep.errors)


def test_content_type_sets_are_consistent():
    assert CYCLIC_TYPES.isdisjoint(CALENDAR_TYPES)
    assert "today_in_history" in FACT_CHECKED_TYPES
