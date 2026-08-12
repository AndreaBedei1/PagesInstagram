"""Editorial states: what "verified" is actually allowed to mean.

The previous pipeline had a single axis — ``verification_status`` — and a
fact-checked item became ``approved_for_publication`` as soon as it carried a
``verified`` label, a source name and a **formally** well-formed URL. None of
that involves anyone reading the source, or the source existing at all. The
result, "5.000 approved, 0 rejected", measured structural conformity and was
read as factual quality.

Three independent axes replace it:

``verification_status``
    What the *dataset author* claims. ``declared`` means "a source was written
    down"; ``verified`` means the dataset build had a real reference. It is
    self-reported, so on its own it grants nothing.

``source_audit_status``
    What the *audit* found out about the source. ``reachable`` is a machine
    result (:mod:`src.content.source_audit` asked the server).
    ``manually_verified`` is a human result and cannot be produced by any
    automatic step in this repository.

``editorial_status``
    Whether a human read the *content* — language, tone, framing — and approved
    it for a page.

Publication in production requires all three to be at their strongest value.
Anything short of that is renderable in preview and blocked at publish time,
which is the point: a dataset that is honest about what it does not know is
worth more than one that claims 5.000 verified facts.
"""
from __future__ import annotations

from enum import StrEnum


class VerificationStatus(StrEnum):
    """What the dataset itself claims about provenance."""

    DECLARED = "declared"        # a source was written down, nothing checked
    VERIFIED = "verified"        # the dataset build had a real reference
    ORIGINAL = "original"        # original writing, no external source expected
    UNVERIFIED = "unverified"


class SourceAuditStatus(StrEnum):
    """What the source audit established about the cited URL."""

    NOT_CHECKED = "not_checked"
    REACHABLE = "reachable"                  # machine: the page answers
    MANUALLY_VERIFIED = "manually_verified"  # human: the page supports the claim
    NEEDS_REVIEW = "needs_review"            # inconclusive, a human must look
    UNSUPPORTED = "unsupported"              # the page does not support the claim
    BROKEN_SOURCE = "broken_source"          # 404 / soft-404 / wrong host


class EditorialStatus(StrEnum):
    """Whether a human approved the text itself."""

    NOT_CHECKED = "not_checked"
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"
    REJECTED = "rejected"


#: Content types whose publication requires a checked source.
FACT_CHECKED_TYPES = frozenset({"world_curiosity", "word_of_the_day",
                                "today_in_history"})

#: Source-audit states that permanently block publication (the dataset is wrong).
BLOCKING_SOURCE_STATES = frozenset({
    SourceAuditStatus.UNSUPPORTED, SourceAuditStatus.BROKEN_SOURCE,
})

#: Editorial states that permanently block publication.
BLOCKING_EDITORIAL_STATES = frozenset({
    EditorialStatus.REJECTED, EditorialStatus.NEEDS_REVISION,
})


def is_production_ready(content_type: str, *, status: str,
                        verification_status: str | None,
                        source_audit_status: str | None,
                        editorial_status: str | None) -> tuple[bool, list[str]]:
    """Can this item be published in production? Returns ``(ok, reasons)``.

    ``reasons`` lists every unmet requirement, so a report can explain the block
    instead of just refusing.
    """
    reasons: list[str] = []
    if status != "approved_for_publication":
        reasons.append(f"status={status!r} (atteso approved_for_publication)")

    ed = (editorial_status or EditorialStatus.NOT_CHECKED)
    if ed != EditorialStatus.APPROVED:
        reasons.append(f"editorial_status={ed!r} (atteso approved)")

    if content_type in FACT_CHECKED_TYPES:
        ver = (verification_status or VerificationStatus.UNVERIFIED)
        if ver != VerificationStatus.VERIFIED:
            reasons.append(f"verification_status={ver!r} (atteso verified)")
        src = (source_audit_status or SourceAuditStatus.NOT_CHECKED)
        if src != SourceAuditStatus.MANUALLY_VERIFIED:
            reasons.append(f"source_audit_status={src!r} (atteso manually_verified)")

    return (not reasons), reasons


def source_status_from_check(check_status: str) -> str:
    """Map a :mod:`src.content.source_audit` outcome to a source-audit state.

    A page that answers becomes ``reachable`` — never ``manually_verified``: no
    automatic step in this repository can promote an item to a human verdict.
    A page that is definitively absent becomes ``broken_source``. Anything
    transient (timeout, 5xx, 403 anti-bot) becomes ``needs_review``: it blocks
    publication without ever asserting the underlying fact is false.
    """
    from .source_audit import BROKEN, REACHABLE

    if check_status in REACHABLE:
        return SourceAuditStatus.REACHABLE
    if check_status in BROKEN:
        return SourceAuditStatus.BROKEN_SOURCE
    return SourceAuditStatus.NEEDS_REVIEW


#: Source quality tiers, strongest first (see docs/DATASET_SOURCES.md).
SOURCE_TIERS: tuple[str, ...] = (
    "primary",                 # the document/measurement itself
    "institutional",           # ministries, space agencies, museums, UN bodies
    "academic",                # universities, peer-reviewed publications
    "authoritative_reference",  # Treccani, national dictionaries, standard bodies
    "general_encyclopedia",    # Wikipedia and equivalents
    "secondary",               # quality press, specialised magazines
    "weak",                    # everything else
)

#: Host → tier. Used by the audit report to show what the datasets actually lean on.
HOST_TIER: dict[str, str] = {
    "www.treccani.it": "authoritative_reference",
    "treccani.it": "authoritative_reference",
    "it.wikipedia.org": "general_encyclopedia",
    "en.wikipedia.org": "general_encyclopedia",
}


def tier_for_host(host: str) -> str:
    return HOST_TIER.get(host, "weak")
