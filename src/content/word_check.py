"""Consistency checks for the ``word_of_the_day`` dataset.

The page publishes a lemma, its part of speech and a rewritten definition. Three
things can go wrong without any generic check noticing: the lemma is not the
canonical form, the declared part of speech contradicts the word's shape, or the
source link points at a lexicographic entry that is not this lemma.

The last one is the reason this module exists. Treccani answers **200** for a
lemma it does not have and redirects to its homepage, so 75 invented entry URLs
passed the first audit as "reachable" — including a misspelling
(``distoglere`` for ``distogliere``) that a working link would have exposed
immediately. :mod:`src.content.source_audit` now treats a redirect-to-root as a
soft 404; this module checks that the slug in the URL still corresponds to the
lemma, or to a form it may legitimately be filed under.

Etymologies are **never** synthesised. 958 of the 1.000 entries carry
``etymology: null``, which is the correct value for an etymology nobody has
verified — an invented plausible origin would be worse than none.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

ERROR = "error"
WARNING = "warning"

#: Parts of speech the dataset is allowed to declare.
PARTS_OF_SPEECH = frozenset({
    "sostantivo maschile", "sostantivo femminile", "aggettivo",
    "verbo transitivo", "verbo intransitivo", "verbo riflessivo",
    "avverbio", "locuzione",
})

_VERB_ENDINGS = ("are", "ere", "ire", "arsi", "ersi", "irsi", "rre")


@dataclass(frozen=True)
class WordIssue:
    rule: str
    severity: str
    message: str
    excerpt: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "excerpt": self.excerpt}


def _slug(value: str) -> str:
    norm = unicodedata.normalize("NFKD", value.lower())
    ascii_only = "".join(c for c in norm if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", ascii_only)


def _stem(lemma: str) -> str:
    """Enough of the lemma to recognise an inflected form of it in a sentence.

    The example sentence uses the word, not the citation form: "calco" appears
    as "calchi", "arenarsi" as "si arenò", "ravvedersi" as "si ravvide". Cutting
    the inflectional ending and keeping four characters matches all of those
    without matching unrelated words.
    """
    base = _slug(lemma)
    for ending in ("arsi", "ersi", "irsi", "are", "ere", "ire", "mente"):
        if base.endswith(ending) and len(base) > len(ending) + 3:
            base = base[: -len(ending)]
            break
    return base[:4]


def _slug_matches_lemma(slug: str, lemma: str) -> bool:
    """Is this entry slug a plausible home for this lemma?

    Accepts the lemma itself, a homograph suffix (``scempio1``), the base form
    of a pronominal verb (``assopirsi`` → ``assopire``) and the verb behind a
    participial adjective (``sferzante`` → ``sferzare``).
    """
    slug = re.sub(r"\d+$", "", _slug(slug))
    base = _slug(lemma)
    if slug == base:
        return True
    for suffix, replacement in (("arsi", "are"), ("ersi", "ere"), ("irsi", "ire"),
                                ("ante", "are"), ("ente", "ere"), ("ente", "ire"),
                                ("ato", "are"), ("ito", "ire"), ("uto", "ere"),
                                ("so", "ere"), ("so", "dere"),
                                ("mente", "")):
        if base.endswith(suffix) and slug == base[: -len(suffix)] + replacement:
            return True
    # An adverb may be filed under its adjective, with either ending.
    if base.endswith("mente"):
        stem = base[:-5]
        if slug in (stem, stem[:-1] + "o" if stem.endswith("a") else stem):
            return True
    return False


def check_word(item: dict) -> list[WordIssue]:
    out: list[WordIssue] = []
    lemma = (item.get("text") or "").strip()
    meta = item.get("metadata") or {}
    pos = str(meta.get("part_of_speech") or "").strip()

    if not lemma:
        out.append(WordIssue("lemma_mancante", ERROR, "il lemma è vuoto"))
        return out
    if lemma != lemma.lower():
        out.append(WordIssue("lemma_maiuscolo", WARNING,
                             f"il lemma «{lemma}» non è in minuscolo"))
    if " " in lemma and pos != "locuzione":
        out.append(WordIssue(
            "lemma_multiparola", ERROR,
            f"«{lemma}» contiene spazi ma non è dichiarato locuzione", lemma))

    if pos not in PARTS_OF_SPEECH:
        out.append(WordIssue("pos_sconosciuta", ERROR,
                             f"parte del discorso non prevista: {pos!r}", pos))
    else:
        low = lemma.lower()
        if pos.startswith("verbo") and not low.endswith(_VERB_ENDINGS):
            out.append(WordIssue(
                "forma_verbale", ERROR,
                f"«{lemma}» è dichiarato {pos} ma non ha una desinenza "
                f"da infinito", lemma))
        if pos == "verbo riflessivo" and not low.endswith(("arsi", "ersi", "irsi")):
            out.append(WordIssue(
                "riflessivo_senza_si", ERROR,
                f"«{lemma}» è dichiarato riflessivo ma non termina in -si", lemma))

    definition = str(meta.get("definition") or "").strip()
    if not definition:
        out.append(WordIssue("definizione_mancante", ERROR, "definizione assente"))
    elif not definition.endswith((".", "!", "?")):
        out.append(WordIssue("definizione_senza_punto", WARNING,
                             "la definizione non termina con un punto"))

    example = str(meta.get("example") or "").strip()
    if not example:
        out.append(WordIssue("esempio_mancante", ERROR, "esempio d'uso assente"))
    elif not _stem(lemma) or _stem(lemma) not in _slug(example):
        out.append(WordIssue(
            "esempio_senza_lemma", WARNING,
            "l'esempio non sembra contenere il lemma", example[:70]))

    # etymology may legitimately be absent; what it must never be is invented,
    # so only its shape is checked when present.
    etymology = meta.get("etymology")
    if etymology is not None and not str(etymology).strip():
        out.append(WordIssue("etimologia_vuota", WARNING,
                             "etymology è una stringa vuota: usa null"))

    url = str(item.get("source_url") or "")
    if url:
        path = urlparse(url).path.strip("/")
        slug = path.rsplit("/", 1)[-1] if path else ""
        if not slug:
            out.append(WordIssue("url_senza_lemma", ERROR,
                                 f"l'URL non contiene un lemma: {url}", url))
        elif not _slug_matches_lemma(slug, lemma):
            out.append(WordIssue(
                "url_lemma_diverso", ERROR,
                f"l'URL punta a «{slug}» ma il lemma è «{lemma}»", url))
    return out


def check_dataset(items: list[dict]) -> dict[int, list[WordIssue]]:
    found: dict[int, list[WordIssue]] = {}
    for i, item in enumerate(items):
        issues = check_word(item)
        if issues:
            found[i] = issues
    return found
