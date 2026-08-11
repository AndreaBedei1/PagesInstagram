"""Evidence-based verification: what makes a factual claim publishable.

The previous model had a label problem. ``manually_verified`` was a string, and a
string can be written by anyone for any reason — including by a script that
never read anything. This module replaces the label with **evidence that can be
re-checked**, and binds that evidence to the exact text it was gathered for.

A verified item carries:

``verification_method``
    *How* it was established. ``original_nonfactual`` for writing that asserts
    nothing about the world; ``authoritative_reference`` when a reference work
    states it; ``structured_official_dataset`` when it comes from a structured
    listing (a Wikipedia day page, a dictionary headword index);
    ``cross_checked_sources`` when two independent sources agree;
    ``primary_source`` / ``institutional_source`` for the strongest cases.

``evidence_summary``
    The passage from the source that supports the claim, trimmed. Not a copy of
    the page — a short quotation of the sentence that matters, so a reader can
    tell at a glance whether the source really says it.

``source_title`` / ``source_url`` / ``source_checked_at``
    What was read, where, and when.

``source_strength``
    Where the source sits in the hierarchy (see docs/DATASET_SOURCES.md).

``verified_content_hash``
    **The anti-cheat.** SHA-256 of the normalised claim text. The gate
    recomputes it: if the text was edited after verification, the hash no longer
    matches and the item stops being publishable. You cannot approve a corpus
    and then change what it says, and you cannot copy a verification from one
    item to another.

``verification_tool_version``
    Which version of the checker produced this. Bumping it invalidates nothing
    by itself, but it makes a corpus verified by an older, weaker checker
    identifiable.

``verification_executor``
    **Who** did it: ``automated_source_first`` when a program fetched the source
    and extracted the passage, ``human`` when a person read it. Calling the
    first one "manually verified" — as an earlier schema did — is the specific
    lie this field exists to prevent.

The gate in :func:`is_publishable` checks all of these. It never looks at a
status string alone.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date as date_cls

#: Bumped whenever the evidence rules change in a way that invalidates old runs.
TOOL_VERSION = "2.0"

#: Who established the verification.
EXECUTOR_AUTOMATED = "automated_source_first"
EXECUTOR_HUMAN = "human"
EXECUTORS = frozenset({EXECUTOR_AUTOMATED, EXECUTOR_HUMAN})

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
#: How a claim was established.
METHOD_ORIGINAL = "original_nonfactual"
METHOD_PRIMARY = "primary_source"
METHOD_INSTITUTIONAL = "institutional_source"
METHOD_AUTHORITATIVE = "authoritative_reference"
METHOD_STRUCTURED = "structured_official_dataset"
METHOD_CROSS_CHECKED = "cross_checked_sources"

VERIFICATION_METHODS = frozenset({
    METHOD_ORIGINAL, METHOD_PRIMARY, METHOD_INSTITUTIONAL,
    METHOD_AUTHORITATIVE, METHOD_STRUCTURED, METHOD_CROSS_CHECKED,
})

#: Methods that require documentary evidence attached to the item.
METHODS_REQUIRING_EVIDENCE = VERIFICATION_METHODS - {METHOD_ORIGINAL}

#: Source strength, strongest first.
STRENGTH_ORDER = ("primary", "institutional", "academic",
                  "authoritative_reference", "general_encyclopedia")
SOURCE_STRENGTHS = frozenset(STRENGTH_ORDER)

#: Host → strength. A host absent from this map cannot back a published claim:
#: an unclassified source is an unassessed source.
HOST_STRENGTH: dict[str, str] = {
    "it.wikipedia.org": "general_encyclopedia",
    "en.wikipedia.org": "general_encyclopedia",
    "www.treccani.it": "authoritative_reference",
    "treccani.it": "authoritative_reference",
    "www.unesco.org": "institutional",
    "whc.unesco.org": "institutional",
    "www.nasa.gov": "institutional",
    "science.nasa.gov": "institutional",
    "www.esa.int": "institutional",
    "www.who.int": "institutional",
    "www.iucnredlist.org": "institutional",
    "www.istat.it": "institutional",
}

#: Content types that assert something about the world.
FACTUAL_TYPES = frozenset({"world_curiosity", "word_of_the_day",
                           "today_in_history"})
#: Content types that are original writing.
ORIGINAL_TYPES = frozenset({"philosophical_thought", "daily_question",
                            "motivational"})

#: Shortest evidence that can count as evidence. A three-word fragment proves
#: nothing; this forces the extractor to capture an actual statement.
MIN_EVIDENCE_CHARS = 40
#: Longest stored, so the dataset holds a citation and not a copy of the page.
MAX_EVIDENCE_CHARS = 320


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")


def normalise_for_hash(text: str) -> str:
    """Casefold, strip accents and collapse whitespace.

    Deliberately insensitive to punctuation and case so that fixing a comma does
    not invalidate a verification, and deliberately sensitive to every word so
    that changing what the claim says does.
    """
    norm = unicodedata.normalize("NFKD", (text or "").strip().casefold())
    ascii_only = "".join(c for c in norm if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^\w\s]", " ", ascii_only)
    return _WS_RE.sub(" ", ascii_only).strip()


def claim_hash(text: str) -> str:
    """SHA-256 of the normalised claim. Binds a verification to its text."""
    return hashlib.sha256(normalise_for_hash(text).encode("utf-8")).hexdigest()


def strength_for_host(host: str) -> str | None:
    return HOST_STRENGTH.get((host or "").lower().lstrip("."))


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:            # pragma: no cover - convenience
        return self.ok


def is_publishable(item: dict, *, content_type: str,
                   today: date_cls | None = None) -> GateResult:
    """May this item be published to a real account?

    Checks evidence, not labels. Every failure is reported, so a report can
    explain the block instead of merely refusing.
    """
    reasons: list[str] = []
    text = item.get("text") or ""

    if not text.strip():
        return GateResult(False, ["testo vuoto"])

    method = (item.get("verification_method") or "").strip()
    if method not in VERIFICATION_METHODS:
        reasons.append(f"verification_method={method!r} non riconosciuto")

    # The hash must match the text as it stands *now*.
    stored_hash = (item.get("verified_content_hash") or "").strip()
    if not stored_hash:
        reasons.append("verified_content_hash assente")
    elif stored_hash != claim_hash(text):
        reasons.append("verified_content_hash non corrisponde al testo attuale "
                       "(il testo è cambiato dopo la verifica)")

    tool = (item.get("verification_tool_version") or "").strip()
    if not tool:
        reasons.append("verification_tool_version assente")

    executor = (item.get("verification_executor") or "").strip()
    if executor not in EXECUTORS:
        reasons.append(
            f"verification_executor={executor!r} non dichiarato: deve dire se "
            f"la verifica è stata automatica o umana")

    if content_type in ORIGINAL_TYPES:
        if method != METHOD_ORIGINAL:
            reasons.append(
                f"un contenuto originale deve usare {METHOD_ORIGINAL!r}, "
                f"non {method!r}")
        return GateResult(not reasons, reasons)

    if content_type in FACTUAL_TYPES:
        if method == METHOD_ORIGINAL:
            reasons.append(
                "un contenuto fattuale non può dichiararsi original_nonfactual")

        evidence = (item.get("evidence_summary") or "").strip()
        if len(evidence) < MIN_EVIDENCE_CHARS:
            reasons.append(
                f"evidence_summary troppo breve ({len(evidence)} caratteri, "
                f"minimo {MIN_EVIDENCE_CHARS}): non dimostra nulla")
        elif len(evidence) > MAX_EVIDENCE_CHARS:
            reasons.append(
                f"evidence_summary troppo lunga ({len(evidence)} caratteri): "
                f"deve essere una citazione, non una copia della pagina")

        if not (item.get("source_url") or "").startswith("https://"):
            reasons.append("source_url assente o non https")
        if not (item.get("source_title") or "").strip():
            reasons.append("source_title assente")

        checked = (item.get("source_checked_at") or "").strip()
        if not checked:
            reasons.append("source_checked_at assente")
        else:
            try:
                date_cls.fromisoformat(checked[:10])
            except ValueError:
                reasons.append(f"source_checked_at non è una data ({checked!r})")

        strength = (item.get("source_strength") or "").strip()
        if strength not in SOURCE_STRENGTHS:
            reasons.append(
                f"source_strength={strength!r} non classificata: una fonte non "
                f"valutata non può sostenere una pubblicazione")

        if (item.get("verification_status") or "") != "verified":
            reasons.append("verification_status != verified")

    return GateResult(not reasons, reasons)


def publishable_items(items: list[dict], content_type: str) -> list[dict]:
    return [i for i in items if is_publishable(i, content_type=content_type).ok]


def blocked_report(items: list[dict], content_type: str) -> list[dict]:
    """Every item that cannot be published, with why."""
    out = []
    for i, item in enumerate(items):
        result = is_publishable(item, content_type=content_type)
        if not result.ok:
            out.append({"index": i,
                        "sequence_index": item.get("sequence_index"),
                        "text": (item.get("text") or "")[:80],
                        "reasons": result.reasons})
    return out


# ---------------------------------------------------------------------------
# Attaching a verification
# ---------------------------------------------------------------------------
def attach_verification(item: dict, *, method: str, evidence: str,
                        source_url: str, source_title: str,
                        source_strength: str, checked_at: str,
                        note: str = "") -> dict:
    """Write a verification onto an item, hashed against its current text.

    Used by the verifier and by nothing else. There is deliberately no helper
    that sets a status without evidence.
    """
    if method not in VERIFICATION_METHODS:
        raise ValueError(f"metodo non valido: {method!r}")
    if method in METHODS_REQUIRING_EVIDENCE:
        if len(evidence.strip()) < MIN_EVIDENCE_CHARS:
            raise ValueError(
                f"prova troppo breve per {method!r}: {evidence[:60]!r}")
        if source_strength not in SOURCE_STRENGTHS:
            raise ValueError(f"source_strength non valida: {source_strength!r}")

    item["verification_status"] = "verified"
    item["verification_method"] = method
    item["verification_tool_version"] = TOOL_VERSION
    item.setdefault("verification_executor", EXECUTOR_AUTOMATED)
    item["verified_content_hash"] = claim_hash(item.get("text") or "")
    if method in METHODS_REQUIRING_EVIDENCE:
        item["evidence_summary"] = _trim_evidence(evidence)
        item["source_url"] = source_url
        item["source_title"] = source_title
        item["source_strength"] = source_strength
        item["source_checked_at"] = checked_at
    if note:
        item["verification_note"] = note
    return item


def _trim_evidence(text: str) -> str:
    """Keep a citation-sized passage, cut on a sentence boundary when possible."""
    clean = _WS_RE.sub(" ", (text or "").strip())
    if len(clean) <= MAX_EVIDENCE_CHARS:
        return clean
    cut = clean[:MAX_EVIDENCE_CHARS]
    for stop in (". ", "; ", ", "):
        idx = cut.rfind(stop)
        if idx > MIN_EVIDENCE_CHARS:
            return cut[:idx + 1].strip()
    return cut.rstrip() + "…"


def mark_original(item: dict) -> dict:
    """Non-factual writing: no source, but still hash-bound to its text."""
    item["verification_status"] = "verified"
    item["verification_method"] = METHOD_ORIGINAL
    item["verification_tool_version"] = TOOL_VERSION
    item.setdefault("verification_executor", EXECUTOR_AUTOMATED)
    item["verified_content_hash"] = claim_hash(item.get("text") or "")
    for stale in ("evidence_summary", "source_url", "source_title",
                  "source_strength", "source_checked_at"):
        item.pop(stale, None)
    return item
