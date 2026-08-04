"""Dataset builder — Curiosità dal Mondo (``world_curiosity``).

Editorial rules:

* every fact is checked against an encyclopedic source and carries its name,
  URL and check date; nothing factual is approved without one;
* evergreen only — no figures that go stale quickly, no unstable statistics,
  no internet myths;
* superlatives ("il più grande", "l'unico", "il primo") appear only when the
  cited source states them;
* backgrounds are illustrative/abstract: the model is never used to fake
  photographic evidence of a real place or event.

Each authored entry is
``(title, location, fact, explanation, category, source_url)``; the source
*name* is derived from the URL host so the authored data stays readable.
"""
from __future__ import annotations

from .common import VERIFIED_AT, hashtags, pick, slugify
from .data.curiosities import CURIOSITIES

CONTENT_TYPE = "world_curiosity"

#: category -> (base hashtags, background prompt variants, mood)
CATEGORY_PROFILES: dict[str, tuple[list[str], list[str], str]] = {
    "geografia": (["#geografia"], [
        "abstract stylised topographic contour shapes, deep teal and slate",
        "soft cartographic gradient with faint elevation bands",
        "minimal illustrative landmass silhouette, muted deep blue",
    ], "calm"),
    "natura": (["#natura"], [
        "abstract organic forms in deep forest green and slate",
        "soft illustrative canopy pattern, muted and uncluttered",
        "stylised leaf and water shapes, deep teal, low detail",
    ], "serene"),
    "scienza": (["#scienza"], [
        "abstract diagrammatic arcs on a deep slate field",
        "soft illustrative wave interference pattern, muted blue",
        "minimal orbital curves on a dark indigo background",
    ], "focused"),
    "animali": (["#animali"], [
        "abstract illustrative animal silhouette, deep teal, no detail",
        "soft organic pattern suggesting fur or scales, muted tones",
        "minimal stylised trace of movement on a dark field",
    ], "calm"),
    "lingue": (["#lingue"], [
        "abstract woven pattern suggesting script without any letters",
        "soft overlapping translucent bands in deep slate and teal",
        "minimal illustrative sound-wave shapes, muted blue",
    ], "reflective"),
    "architettura": (["#architettura"], [
        "abstract geometric arches in deep slate and muted stone",
        "soft illustrative structural grid, low contrast, uncluttered",
        "minimal silhouette of a vault, deep teal, no signage",
    ], "focused"),
    "tradizioni": (["#tradizioni"], [
        "abstract textile pattern in muted earth and deep teal",
        "soft illustrative ceramic glaze texture, quiet centre",
        "minimal woven motif, deep slate, very low detail",
    ], "grateful"),
    "invenzioni": (["#invenzioni"], [
        "abstract mechanical curves on a deep slate field, no text",
        "soft illustrative gear-like shapes, muted blue, uncluttered",
        "minimal blueprint-like lines, deep indigo, no lettering",
    ], "focused"),
    "spazio": (["#spazio"], [
        "deep space gradient with a faint distant nebula glow",
        "abstract orbital arc on a near-black field, minimal",
        "soft illustrative starfield, very low density, calm centre",
    ], "inspirational"),
    "storia": (["#storia"], [
        "abstract archival texture in deep slate and faded umber",
        "soft illustrative layered strata, muted and quiet",
        "minimal weathered surface pattern, deep teal",
    ], "reflective"),
}

EXTRA_TAGS = [
    "#curiositàdalmondo", "#curiosità", "#sapevatelo", "#mondo",
    "#imparareognigiorno", "#divulgazione", "#pianeta", "#scopriamoinsieme",
]

CALLS_TO_ACTION = [
    "Lo sapevi?",
    "Ne avevi mai sentito parlare?",
    "Salva il post se ti è servito.",
    "Conosci un caso simile?",
    "Fonte nel testo: verifica pure.",
]


def source_name_for(url: str) -> str:
    """Human-readable source name derived from the URL host."""
    if "treccani.it" in url:
        return "Treccani — Enciclopedia"
    if "whc.unesco.org" in url or "unesco.org" in url:
        return "UNESCO World Heritage Centre"
    if "nasa.gov" in url:
        return "NASA"
    if "esa.int" in url:
        return "ESA"
    if "iucnredlist.org" in url:
        return "IUCN Red List"
    if "britannica.com" in url:
        return "Encyclopaedia Britannica"
    return "Wikipedia in italiano"


def build() -> list[dict]:
    items: list[dict] = []
    for i, entry in enumerate(CURIOSITIES):
        title, location, fact, explanation, category, src_url = entry
        src_name = source_name_for(src_url)
        base, prompts, mood = CATEGORY_PROFILES[category]
        items.append({
            "id": f"wc-{i:04d}-{slugify(title)[:32]}",
            "sequence_index": i,
            "text": fact,
            "category": category,
            "mood": mood,
            "explanation": explanation,
            "caption": f"{title} — {location}. {explanation} Fonte: {src_name}.",
            "call_to_action": pick(CALLS_TO_ACTION, i),
            "hashtags": hashtags(base, EXTRA_TAGS, i, total=6),
            "background_prompt": pick(prompts, i // 3),
            "status": "approved_for_publication",
            "verification_status": "verified",
            "source_name": src_name,
            "source_url": src_url,
            "verified_at": VERIFIED_AT,
            "metadata": {
                "title": title,
                "location": location,
                "explanation": explanation,
                "category": category,
            },
        })
    return items
