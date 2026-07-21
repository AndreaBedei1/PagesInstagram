"""Tests for normalization, quality scoring, dedup and the importer."""
from __future__ import annotations

import json

from src.content.dedup import SimilarityIndex
from src.content.importer import import_dataset
from src.content.normalize import content_hash, normalize_text
from src.content.quality import gulpease_index, score_content
from src.core.enums import ContentStatus


# ---- normalize -------------------------------------------------------------
def test_normalize_and_hash_equal_for_visually_equal():
    a = "L'importante è iniziare."
    b = "L’importante è iniziare"      # curly apostrophe, no period
    assert normalize_text(a) == normalize_text(b)
    assert content_hash(a) == content_hash(b)


def test_normalize_preserves_accents():
    assert "è" in normalize_text("Perché è così")


# ---- quality ---------------------------------------------------------------
def test_good_phrase_scores_high():
    r = score_content(
        "Un passo alla volta costruisci il tuo domani.",
        content_type="motivational",
        caption="Ogni piccola azione ripetuta con costanza diventa un risultato concreto nel tempo.",
    )
    assert r.score >= 0.75, (r.score, r.subscores, r.issues)


def test_single_word_junk_is_not_publishable():
    r = score_content("Vai.", content_type="motivational")
    assert r.score < 0.6


def test_caption_that_only_repeats_is_penalized():
    text = "La disciplina è libertà."
    good = score_content(text, caption="La disciplina crea una struttura che, col tempo, ti rende più libero di scegliere.")
    repeat = score_content(text, caption="La disciplina è libertà.")
    assert repeat.score < good.score


def test_gulpease_range():
    g = gulpease_index("Questa è una frase semplice.")
    assert 0 <= g <= 100


# ---- dedup -----------------------------------------------------------------
def test_exact_and_near_duplicate_detection():
    idx = SimilarityIndex()
    idx.add(1, "Un passo alla volta costruisci il tuo domani.")
    dup_exact, m1 = idx.is_duplicate("un passo alla volta costruisci il tuo domani")
    assert dup_exact and m1.kind == "exact"
    dup_near, m2 = idx.is_duplicate("Un passo alla volta costruisci il tuo futuro.")
    assert dup_near, (m2.kind, m2.score)


def test_distinct_phrases_not_duplicate():
    idx = SimilarityIndex()
    idx.add(1, "La disciplina è libertà.")
    dup, m = idx.is_duplicate("Il coraggio nasce dove finisce la paura.")
    assert not dup, (m.kind, m.score)


def test_clustering_groups_similar():
    idx = SimilarityIndex()
    idx.add(10, "Un passo alla volta costruisci il tuo domani.")
    idx.add(11, "Un passo alla volta costruisci il tuo futuro.")
    idx.add(12, "Il coraggio nasce dove finisce la paura.")
    clusters = idx.cluster()
    assert clusters[10] == clusters[11]
    assert clusters[12] != clusters[10]


# ---- importer --------------------------------------------------------------
def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def test_import_motivational_dedup_and_status(tmp_db, tmp_path):
    base = "Un passo alla volta costruisci il tuo domani."
    ds = {
        "schema_version": 1, "content_type": "motivational", "language": "it",
        "items": [
            {"text": base, "category": "costanza", "mood": "determined",
             "caption": "Ogni piccola azione ripetuta con costanza diventa nel tempo un risultato solido e concreto.",
             "hashtags": ["#motivazione", "#costanza"], "background_prompt": "soft gradient"},
            {"text": "Un passo alla volta costruisci il tuo futuro.",  # near-dup
             "category": "costanza", "mood": "determined",
             "caption": "Il futuro si costruisce con gesti quotidiani.", "hashtags": ["#futuro"]},
            {"text": base,  # exact dup
             "category": "costanza", "mood": "determined", "caption": "ripetuto"},
            {"text": "La disciplina trasforma le intenzioni in abitudini durature.",
             "category": "disciplina", "mood": "focused",
             "caption": "Quando un comportamento diventa abitudine smette di pesare e inizia a lavorare per te.",
             "hashtags": ["#disciplina"]},
        ],
    }
    path = _write(tmp_path, "motivational_it.json", ds)
    rep = import_dataset(tmp_db, path, approve_floor=0.80)
    assert rep.added == 2                # base + disciplina phrase
    assert rep.exact_duplicates == 1
    assert rep.near_duplicates == 1
    assert rep.approved >= 1
    assert tmp_db.count_contents(content_type="motivational") == 2


def test_import_quotes_attribution_gating(tmp_db, tmp_path):
    ds = {
        "schema_version": 1, "content_type": "famous_quote", "language": "it",
        "items": [
            {"text": "Ogni cosa a suo tempo, sotto il cielo.", "author": "Seneca",
             "author_display_name": "Lucio Anneo Seneca", "source_work": "Lettere a Lucilio",
             "source_year": "c. 65 d.C.", "source_url": "https://la.wikisource.org/wiki/Epistulae",
             "attribution_confidence": "high", "status": "verified",
             "caption": "Seneca invita a rispettare i tempi naturali delle cose invece di forzarli.",
             "mood": "reflective", "category": "tempo", "hashtags": ["#seneca"]},
            {"text": "Una citazione dall'attribuzione incerta e non verificata.",
             "author": "Anonimo", "attribution_confidence": "low", "status": "needs_review",
             "caption": "Attribuzione non confermata da fonti primarie affidabili.",
             "mood": "calm", "category": "saggezza"},
        ],
    }
    path = _write(tmp_path, "famous_quotes_it.json", ds)
    rep = import_dataset(tmp_db, path)
    assert rep.added == 2
    approved = tmp_db.list_contents(content_type="famous_quote",
                                    status=ContentStatus.APPROVED_FOR_PUBLICATION)
    review = tmp_db.list_contents(content_type="famous_quote",
                                  status=ContentStatus.NEEDS_REVIEW)
    assert len(approved) == 1 and approved[0]["author"] == "Seneca"
    assert len(review) == 1
