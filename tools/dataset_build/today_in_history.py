"""Dataset builder — Oggi nella Storia (``today_in_history``).

Editorial rules:

* every event carries a ``calendar_key`` in ``MM-DD`` form, and the engine only
  ever publishes an item whose key equals the local date — an event can never
  appear on the wrong day;
* every event carries a source name, URL and check date;
* descriptions stay factual and balanced, with no celebratory framing;
* the 366 possible keys are all covered, each with at least two events, so the
  yearly rotation always has an alternative.

Each authored entry is
``(calendar_key, year, title, description, explanation, category, source_url)``.
"""
from __future__ import annotations

from .common import VERIFIED_AT, hashtags, pick, slugify
from .data.history import EVENTS

CONTENT_TYPE = "today_in_history"
SOURCE_NAME_DEFAULT = "Wikipedia in italiano"

CATEGORY_TAGS: dict[str, list[str]] = {
    "scienza": ["#scienza"],
    "esplorazione": ["#esplorazione"],
    "arte": ["#arte"],
    "politica": ["#storia"],
    "societa": ["#società"],
    "tecnologia": ["#tecnologia"],
    "invenzioni": ["#invenzioni"],
    "sport": ["#sport"],
    "cultura": ["#cultura"],
    "natura": ["#natura"],
    "spazio": ["#spazio"],
}

BACKGROUND_PROMPTS = [
    "archival abstract texture in muted amber and faded umber, calm centre",
    "soft parchment grain with a gentle vignette, desaturated",
    "layered faded film grain in warm sepia, very low detail",
    "abstract weathered plaster in soft ochre, quiet and empty",
    "muted amber gradient with a faint horizontal band, minimal",
    "soft aged paper fibre texture in warm neutral tones",
]

EXTRA_TAGS = [
    "#ogginellastoria", "#storia", "#accaddeoggi", "#memoria",
    "#anniversario", "#divulgazione", "#passato", "#dateimportanti",
]

CALLS_TO_ACTION = [
    "Lo ricordavi?",
    "Che effetto ti fa, riletto oggi?",
    "Salva il post per ricordartelo.",
    "Sapevi come andò a finire?",
    "Fonte nel testo: approfondisci pure.",
]


def source_name_for(url: str) -> str:
    if "treccani.it" in url:
        return "Treccani — Enciclopedia"
    if "unesco.org" in url:
        return "UNESCO World Heritage Centre"
    if "nasa.gov" in url:
        return "NASA"
    if "esa.int" in url:
        return "ESA"
    return SOURCE_NAME_DEFAULT


def build() -> list[dict]:
    items: list[dict] = []
    for i, entry in enumerate(EVENTS):
        key, year, title, description, explanation, category, url = entry
        src_name = source_name_for(url)
        items.append({
            "id": f"th-{key}-{year}-{slugify(title)[:28]}",
            "sequence_index": i,
            "calendar_key": key,
            "text": title,
            "category": category,
            "mood": "reflective",
            "explanation": explanation,
            "caption": f"{explanation} Fonte: {src_name}.",
            "call_to_action": pick(CALLS_TO_ACTION, i),
            "hashtags": hashtags(CATEGORY_TAGS[category], EXTRA_TAGS, i, total=6),
            "background_prompt": pick(BACKGROUND_PROMPTS, i // 4),
            "status": "approved_for_publication",
            "verification_status": "verified",
            "source_name": src_name,
            "source_url": url,
            "verified_at": VERIFIED_AT,
            "metadata": {
                "year": str(year),
                "description": description,
                "calendar_key": key,
                "category": category,
            },
        })
    return items
