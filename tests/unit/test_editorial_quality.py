"""The checks that turn this audit's findings into permanent guardrails.

Each test here corresponds to something the audit actually found. A regression
in any of them is the same defect coming back, so they are written against the
real datasets rather than fixtures — a fixture cannot tell you that the shipped
content drifted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.content.editorial import (EditorialStatus, SourceAuditStatus,
                                   is_production_ready,
                                   source_status_from_check)
from src.content.editorial_stats import (MAX_SAME_CATEGORY_RUN,
                                         MAX_SAME_MOOD_RUN, analyse_dataset,
                                         publication_order, stratified_sample)
from src.content.history_check import ERROR as H_ERROR
from src.content.history_check import calendar_coverage
from src.content.history_check import check_dataset as check_history
from src.content.language_check import ERROR as L_ERROR
from src.content.language_check import check_item, check_text
from src.content.source_audit import (BROKEN, REACHABLE, _redirects_to_root,
                                      _wikipedia_title)
from src.content.word_check import ERROR as W_ERROR
from src.content.word_check import check_dataset as check_words

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "datasets"

CYCLIC = ("philosophical_thoughts_it.json", "world_curiosities_it.json",
          "words_of_the_day_it.json", "daily_questions_it.json")
ALL_DATASETS = CYCLIC + ("today_in_history_it.json",)


def load(name: str) -> dict:
    return json.loads((DATASETS / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ALL_DATASETS)
def test_no_language_errors_in_any_dataset(name):
    data = load(name)
    bad = []
    for i, item in enumerate(data["items"]):
        for issue in check_item(item, content_type=data["content_type"]):
            if issue.severity == L_ERROR:
                bad.append(f"#{i} {issue.rule}: {issue.message}")
    assert not bad, f"{name}: " + "; ".join(bad[:8])


def test_article_before_numeral_rule():
    # The defect that shipped: "supera gli 10.900 metri".
    issues = check_text("Il punto più profondo supera gli 10.900 metri.")
    assert any(i.rule == "articolo_numerale" for i in issues)
    # …and the cases where "gli" is right must not be flagged.
    for ok in ("Superano gli 80 metri.", "Restarono gli 11 superstiti.",
               "Passarono gli 8 anni previsti."):
        assert not [i for i in check_text(ok) if i.rule == "articolo_numerale"], ok
    # …and "i" before an eight is wrong the other way round.
    assert any(i.rule == "articolo_numerale"
               for i in check_text("Superano i 80 metri."))


def test_transitive_verb_without_object_rule():
    assert any(i.rule == "verbo_senza_oggetto" for i in
               check_text("Il desiderio di piacere a tutti finisce per rendere illeggibili."))
    # An enclitic pronoun or a following object makes it correct.
    for ok in ("finisce per renderci illeggibili.",
               "possono rendere illeggibili archivi recenti."):
        assert not [i for i in check_text(ok) if i.rule == "verbo_senza_oggetto"], ok


def test_impura_article_rule():
    assert any(i.rule == "articolo_s_impura"
               for i in check_text("Il pneumatico gonfiabile fu sviluppato."))
    assert not [i for i in check_text("Lo pneumatico gonfiabile fu sviluppato.")
                if i.rule == "articolo_s_impura"]


def test_url_never_appears_in_displayed_text():
    for name in ALL_DATASETS:
        for item in load(name)["items"]:
            assert "http" not in (item.get("text") or ""), name


# ---------------------------------------------------------------------------
# Source audit
# ---------------------------------------------------------------------------
def test_redirect_to_root_is_a_soft_404():
    # Treccani answers 200 and redirects to its homepage for a lemma it does
    # not have. 75 dead links passed the first audit exactly this way.
    assert _redirects_to_root("https://www.treccani.it/vocabolario/abbrivio/",
                              "https://www.treccani.it/")
    assert not _redirects_to_root("https://www.treccani.it/vocabolario/scempio/",
                                  "https://www.treccani.it/vocabolario/scempio1/")
    # A genuine cross-host redirect is not this rule's business.
    assert not _redirects_to_root("https://it.wikipedia.org/wiki/X",
                                  "https://en.wikipedia.org/")


def test_wikipedia_title_extraction():
    assert _wikipedia_title(
        "https://it.wikipedia.org/wiki/Scattering_di_Rayleigh") == "Scattering di Rayleigh"
    assert _wikipedia_title("https://www.treccani.it/vocabolario/mite/") is None


def test_unreachable_source_never_asserts_the_fact_is_false():
    # Transient failures block publication; they do not condemn the content.
    for transient in ("timeout", "server_error", "error", "forbidden"):
        assert source_status_from_check(transient) == SourceAuditStatus.NEEDS_REVIEW
    for broken in BROKEN:
        assert source_status_from_check(broken) == SourceAuditStatus.BROKEN_SOURCE
    for good in REACHABLE:
        # Reachable is never promoted to a human verdict.
        assert source_status_from_check(good) == SourceAuditStatus.REACHABLE


# ---------------------------------------------------------------------------
# Editorial gate
# ---------------------------------------------------------------------------
def test_a_url_alone_does_not_make_a_fact_publishable():
    ok, reasons = is_production_ready(
        "world_curiosity", status="approved_for_publication",
        verification_status="verified",
        source_audit_status=SourceAuditStatus.REACHABLE,
        editorial_status=EditorialStatus.APPROVED)
    assert not ok
    assert any("source_audit_status" in r for r in reasons)


def test_original_types_need_only_an_editorial_signoff():
    ok, _ = is_production_ready(
        "philosophical_thought", status="approved_for_publication",
        verification_status=None, source_audit_status=None,
        editorial_status=EditorialStatus.APPROVED)
    assert ok


def test_unreviewed_content_is_never_production_ready():
    ok, reasons = is_production_ready(
        "today_in_history", status="approved_for_publication",
        verification_status="verified",
        source_audit_status=SourceAuditStatus.NOT_CHECKED,
        editorial_status=EditorialStatus.NOT_CHECKED)
    assert not ok
    assert len(reasons) >= 2


@pytest.mark.parametrize("name", ALL_DATASETS)
def test_no_publishable_item_has_an_unverified_source(name):
    data = load(name)
    ctype = data["content_type"]
    leaking = [
        i for i, item in enumerate(data["items"])
        if (item.get("source_audit_status") or "") in
           ("broken_source", "unsupported", "not_checked")
        and is_production_ready(
            ctype, status=item.get("status") or "",
            verification_status=item.get("verification_status"),
            source_audit_status=item.get("source_audit_status"),
            editorial_status=item.get("editorial_status"))[0]]
    assert not leaking, f"{name}: elementi pubblicabili senza fonte: {leaking[:5]}"


def test_manual_verification_is_a_minority_and_is_declared():
    """The honest-numbers test: nothing may claim everything was verified."""
    total = verified = 0
    for name in ALL_DATASETS:
        items = load(name)["items"]
        total += len(items)
        verified += sum(1 for i in items
                        if i.get("source_audit_status") == "manually_verified")
    assert total == 5000
    # 100 per fact-checked dataset were read. If this ever equals `total`,
    # somebody batch-approved the corpus and the number stopped meaning anything.
    assert 0 < verified < total


# ---------------------------------------------------------------------------
# Repetition and distribution
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ALL_DATASETS)
def test_no_repetition_inside_the_measured_windows(name):
    stats = analyse_dataset(DATASETS / name)
    assert not stats.findings, (
        f"{name}: " + "; ".join(f["kind"] + ":" + f["value"][:40]
                                for f in stats.findings[:5]))


@pytest.mark.parametrize("name", ALL_DATASETS)
def test_category_and_mood_do_not_run(name):
    stats = analyse_dataset(DATASETS / name)
    assert stats.longest_category_run <= MAX_SAME_CATEGORY_RUN, name
    assert stats.longest_mood_run <= MAX_SAME_MOOD_RUN, name


@pytest.mark.parametrize("name", ALL_DATASETS)
def test_thirty_consecutive_days_are_varied(name):
    """A month of posts must not look like a template with the text swapped."""
    data = load(name)
    ordered = publication_order(data["items"], data["content_type"])
    for start in range(0, len(ordered) - 30, 37):
        window = ordered[start:start + 30]
        ctas = {(i.get("call_to_action") or "").strip() for i in window}
        prompts = {(i.get("background_prompt") or "").strip() for i in window}
        tagsets = {" ".join(sorted(i.get("hashtags") or [])) for i in window}
        assert len(ctas) >= 12, f"{name}@{start}: solo {len(ctas)} CTA distinte"
        assert len(prompts) >= 12, f"{name}@{start}: solo {len(prompts)} prompt"
        assert len(tagsets) >= 5, f"{name}@{start}: solo {len(tagsets)} set di tag"


def test_no_call_to_action_promises_to_read_every_comment():
    """The pages run unattended; that promise cannot be kept."""
    for name in ALL_DATASETS:
        for item in load(name)["items"]:
            cta = (item.get("call_to_action") or "").lower()
            assert "leggo tutto" not in cta, name


def test_sober_pages_leave_some_days_without_a_call_to_action():
    for name in ("philosophical_thoughts_it.json", "daily_questions_it.json"):
        items = load(name)["items"]
        empty = sum(1 for i in items if not (i.get("call_to_action") or "").strip())
        assert empty > 50, f"{name}: solo {empty} giorni senza CTA"


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
def test_stratified_sample_is_reproducible_and_spread():
    items = load("world_curiosities_it.json")["items"]
    a = stratified_sample(items, count=100, seed=20260805)
    b = stratified_sample(items, count=100, seed=20260805)
    assert a == b, "lo stesso seed deve produrre lo stesso campione"
    assert len(a) == 100
    # The bug this guards: a batch stratum of size one made the "stratified"
    # sample 100 consecutive items.
    assert max(a) - min(a) > 800, f"campione concentrato: {min(a)}..{max(a)}"
    assert len(set(a)) == 100


def test_a_different_seed_gives_a_different_sample():
    items = load("daily_questions_it.json")["items"]
    a = stratified_sample(items, count=100, seed=1)
    b = stratified_sample(items, count=100, seed=2)
    assert a != b


# ---------------------------------------------------------------------------
# Historical events
# ---------------------------------------------------------------------------
def test_history_has_no_date_or_year_inconsistencies():
    items = load("today_in_history_it.json")["items"]
    errors = [f"#{i} {issue.rule}: {issue.message}"
              for i, issues in check_history(items).items()
              for issue in issues if issue.severity == H_ERROR]
    assert not errors, "; ".join(errors[:6])


def test_every_calendar_day_is_covered_including_29_february():
    coverage = calendar_coverage(load("today_in_history_it.json")["items"])
    assert len(coverage) == 366
    assert coverage.get("02-29", 0) >= 2
    assert min(coverage.values()) >= 2


def test_pre_gregorian_events_declare_their_calendar_convention():
    """The February Revolution is filed on 8 March; that has to be stated."""
    items = load("today_in_history_it.json")["items"]
    revolution = [i for i in items
                  if "Pietrogrado" in (i.get("text") or "")]
    assert revolution, "evento di riferimento assente dal dataset"
    for item in revolution:
        assert (item.get("metadata") or {}).get("calendar_note")


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------
def test_words_are_consistent_with_their_source_and_part_of_speech():
    items = load("words_of_the_day_it.json")["items"]
    errors = [f"#{i} {issue.rule}: {issue.message}"
              for i, issues in check_words(items).items()
              for issue in issues if issue.severity == W_ERROR]
    assert not errors, "; ".join(errors[:6])


def test_unverified_etymologies_are_null_not_invented():
    items = load("words_of_the_day_it.json")["items"]
    for item in items:
        etymology = (item.get("metadata") or {}).get("etymology")
        assert etymology is None or str(etymology).strip(), item.get("text")
    with_etymology = sum(1 for i in items
                         if (i.get("metadata") or {}).get("etymology"))
    # Most entries have none, and that is the correct state: an approximated
    # origin would be worse than an absent one.
    assert with_etymology < len(items) // 2
