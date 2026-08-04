"""Dataset builder — Parola del Giorno (``word_of_the_day``).

Editorial rules:

* real Italian lemmas only;
* the definition is **rewritten in our own words** — never copied from a
  copyrighted dictionary entry;
* the example sentence is original;
* etymology only when it is well established; otherwise omitted;
* every item carries a lexicographic source (Treccani's ``vocabolario`` entry,
  whose URL is the canonical ``/vocabolario/<lemma>/`` form) and a check date.

Each authored entry is
``(lemma, part_of_speech, definition, example, etymology_or_None, register)``.
"""
from __future__ import annotations

from .common import (VERIFIED_AT, hashtags, pick, slugify,
                     treccani_vocabolario)
from .data.words import WORDS

CONTENT_TYPE = "word_of_the_day"
SOURCE_NAME = "Treccani — Vocabolario della lingua italiana"

REGISTER_TAGS: dict[str, list[str]] = {
    "comune": ["#italiano"],
    "letterario": ["#linguaitaliana"],
    "raro": ["#paroledimenticate"],
    "tecnico": ["#lessico"],
    "regionale": ["#dialetti"],
    "antico": ["#etimologia"],
}

BACKGROUND_PROMPTS = [
    "aged ivory paper texture with faint printing grain, very calm center",
    "warm cream letterpress stock, soft vignette, uncluttered",
    "pale sepia paper with a barely visible fibre pattern",
    "matte bone-white surface with soft raking light",
    "faint ink wash on ivory paper, extremely low contrast",
    "old book endpaper texture in warm parchment tones",
]

EXTRA_TAGS = [
    "#paroladelgiorno", "#lessico", "#vocabolario", "#lingua",
    "#curiositàlinguistiche", "#scrittura", "#leggere", "#etimologia",
]

CALLS_TO_ACTION = [
    "La conoscevi già?",
    "Quando la useresti?",
    "Prova a usarla oggi almeno una volta.",
    "Ti suona familiare o del tutto nuova?",
    "Hai un sinonimo che preferisci?",
]


def build() -> list[dict]:
    items: list[dict] = []
    for i, entry in enumerate(WORDS):
        lemma, pos, definition, example, etymology, register = entry
        caption_parts = [f"{lemma} — {definition}", f"Esempio: «{example}»"]
        if etymology:
            caption_parts.append(f"Origine: {etymology}")
        caption_parts.append(f"Fonte: {SOURCE_NAME}.")
        items.append({
            "id": f"wd-{i:04d}-{slugify(lemma)}",
            "sequence_index": i,
            "text": lemma,
            "category": register,
            "mood": "reflective",
            "caption": " ".join(caption_parts),
            "call_to_action": pick(CALLS_TO_ACTION, i),
            "hashtags": hashtags(REGISTER_TAGS[register], EXTRA_TAGS, i, total=6),
            "background_prompt": pick(BACKGROUND_PROMPTS, i // 6),
            "status": "approved_for_publication",
            "verification_status": "verified",
            "source_name": SOURCE_NAME,
            "source_url": treccani_vocabolario(lemma),
            "verified_at": VERIFIED_AT,
            "metadata": {
                "part_of_speech": pos,
                "definition": definition,
                "example": example,
                "etymology": etymology or "",
                "register": register,
            },
        })
    return items
