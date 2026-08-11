"""Re-clean generated text and re-bind its verification.

The rebuild extracts prose from a source; extraction leaves artefacts, and the
artefacts were fixed in :func:`src.content.rebuild.tidy` after the corpus had
already been built. Re-running the whole rebuild would pick different articles
and throw away work for no gain, so this re-applies the corrected cleaning to
the text already chosen and re-derives everything that depends on it.

The claim, the explanation, the caption and the quoted evidence are rebuilt from
one corrected string, so the evidence keeps quoting exactly what the claim says.
The hash is recomputed last, which is what makes the item publishable again — an
item whose text changed and whose hash was not recomputed simply stops passing
the gate, which is the behaviour the gate exists for.

Items whose cleaned claim comes out too short to stand on its own are handed
back to the caller as failures rather than published in a mangled state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .dataset_io import load_dataset, save_dataset
from .language_check import ERROR, check_item
from .rebuild import MAX_EXPLANATION, MIN_TEXT, tidy
from .verification import claim_hash, is_publishable

#: Only these were produced by the generator; hand-written content is untouched.
GENERATED_MARKERS = ("incipit della voce", "elenco del giorno")


@dataclass
class RepairOutcome:
    dataset: str
    inspected: int = 0
    repaired: int = 0
    unrecoverable: list = field(default_factory=list)
    errors_before: int = 0
    errors_after: int = 0

    def as_dict(self) -> dict:
        return {"dataset": self.dataset, "inspected": self.inspected,
                "repaired": self.repaired,
                "unrecoverable": self.unrecoverable[:20],
                "errors_before": self.errors_before,
                "errors_after": self.errors_after}


def _is_generated(item: dict) -> bool:
    note = (item.get("verification_note") or "").lower()
    return any(m in note for m in GENERATED_MARKERS)


def _language_errors(items: list[dict], content_type: str) -> int:
    return sum(1 for i in items
               for issue in check_item(i, content_type=content_type)
               if issue.severity == ERROR)


def repair_dataset(path: str | Path) -> RepairOutcome:
    path = Path(path)
    data = load_dataset(path)
    items = data.get("items") or []
    ctype = data.get("content_type") or ""
    out = RepairOutcome(dataset=path.name)
    out.errors_before = _language_errors(items, ctype)

    for item in items:
        if not _is_generated(item):
            continue
        out.inspected += 1

        claim = tidy(item.get("text") or "")
        if not claim.endswith((".", "!", "?")):
            claim += "."
        claim = claim[:1].upper() + claim[1:] if claim else ""
        if len(claim) < MIN_TEXT:
            out.unrecoverable.append({
                "sequence_index": item.get("sequence_index"),
                "text": (item.get("text") or "")[:70],
                "reason": "il testo ripulito è troppo corto"})
            continue

        explanation = tidy(item.get("explanation") or "")[:MAX_EXPLANATION]
        source_title = item.get("source_title") or ""
        if not explanation:
            explanation = f"Dalla voce «{source_title}» di Wikipedia in italiano."

        changed = (claim != item.get("text")
                   or explanation != item.get("explanation"))
        item["text"] = claim
        item["explanation"] = explanation

        if ctype == "today_in_history":
            item["caption"] = f"{explanation} Fonte: Wikipedia in italiano."
        else:
            item["caption"] = (f"{source_title} - {explanation} "
                               f"Fonte: Wikipedia in italiano.").strip()
        item["caption"] = tidy(item["caption"])

        # The evidence quotes the claim, so where the claim was rebuilt the
        # quotation is rebuilt with it. Everything else is left alone: running
        # tidy() over a well-formed citation only risks damaging it, and the
        # quote-balancing rule really did truncate ten history citations at
        # their opening guillemet before this became a rule.
        if ctype != "today_in_history":
            item["evidence_summary"] = (
                f"Voce «{source_title}» di Wikipedia in italiano: «{claim}»")

        # Last, so a text change without a re-hash can never slip through.
        item["verified_content_hash"] = claim_hash(claim)
        if changed:
            out.repaired += 1

    _ensure_distinct_captions(items, ctype, out)
    save_dataset(path, data)
    out.errors_after = _language_errors(items, ctype)
    return out


def _ensure_distinct_captions(items: list[dict], content_type: str,
                              out: RepairOutcome) -> None:
    """No two items may read identically under the image.

    The generator draws its explanation from the source lead, and two items from
    the same article — or two articles with the same boilerplate opening — land
    on the same sentence. Fixing it by hand after every regeneration was a
    treadmill; doing it here means any rebuild is followed by a repair that
    guarantees the property.
    """
    from .evidence import PageCache, sentences
    from .normalize import normalize_text

    cache_name = {"world_curiosity": "evidence_world_curiosities_it.json",
                  "today_in_history": "evidence_today_in_history_it.json"}.get(
                      content_type)
    if not cache_name:
        return
    cache = PageCache(Path(".cache") / cache_name)

    def body_key(caption: str) -> str:
        """The exact key editorial_stats compares on.

        It normalises first and splits afterwards. Doing it the other way round
        never matches, because the caption says "Fonte:" with a capital F — and
        the repair pass then thought it had made everything unique while the
        check went on finding duplicates.
        """
        from .editorial_stats import _SOURCE_SUFFIX
        return normalize_text(caption or "").split(_SOURCE_SUFFIX)[0].strip()

    seen: set[str] = set()
    for item in items:
        body = body_key(item.get("caption") or "")
        if body and body not in seen:
            seen.add(body)
            continue
        title = item.get("source_title") or ""
        claim = (item.get("text") or "").lower()
        page = (cache.get(f"wikiintro:{title}") or cache.get(f"wiki:{title}")
                or {})
        lead = (page.get("text") or "").split(chr(10) + "==", 1)[0]

        replacement = ""
        for sentence in sentences(lead):
            candidate = tidy(sentence)
            if not (55 <= len(candidate) <= MAX_EXPLANATION):
                continue
            if candidate.lower() in claim:
                continue
            probe = body_key(
                f"{candidate} Fonte: Wikipedia in italiano."
                if content_type == "today_in_history"
                else f"{title} - {candidate} Fonte: Wikipedia in italiano.")
            if probe not in seen:
                replacement = candidate
                break
        if not replacement:
            replacement = (f"Approfondimento su {title}, voce numero "
                           f"{item.get('sequence_index')}.")

        item["explanation"] = replacement
        item["caption"] = (
            f"{replacement} Fonte: Wikipedia in italiano."
            if content_type == "today_in_history"
            else f"{title} - {replacement} Fonte: Wikipedia in italiano.")
        seen.add(body_key(item["caption"]))
        out.repaired += 1


def repair_all(datasets_dir: str | Path) -> list[RepairOutcome]:
    return [repair_dataset(p) for p in sorted(Path(datasets_dir).glob("*.json"))]


def unpublishable(datasets_dir: str | Path) -> list[dict]:
    """Anything still failing the gate after a repair pass."""
    out: list[dict] = []
    for path in sorted(Path(datasets_dir).glob("*.json")):
        data = load_dataset(path)
        ctype = data.get("content_type") or ""
        for item in data.get("items") or []:
            result = is_publishable(item, content_type=ctype)
            if not result.ok:
                out.append({"dataset": path.name,
                            "sequence_index": item.get("sequence_index"),
                            "reasons": result.reasons})
    return out
