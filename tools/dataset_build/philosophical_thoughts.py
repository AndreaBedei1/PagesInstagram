"""Dataset builder — Pensiero Essenziale (``philosophical_thought``).

Editorial rules enforced here and re-checked by ``validate-datasets``:

* original thoughts only — **no author, no attribution, no invented quotation**;
* short and memorable: at most ~25 words, so the type stays large on a 9:16 frame;
* reflective but plain-spoken; no aggressive motivational register;
* no reference to current events, brands, statistics or anything datable.

Each authored entry is ``(text, expansion, category, mood)``. ``expansion`` is a
distinct sentence written for that thought and becomes the caption — it is never
a restatement of the text.
"""
from __future__ import annotations

from .common import hashtags, pick, slugify
from .data.thoughts import THOUGHTS

CONTENT_TYPE = "philosophical_thought"

#: category -> (base hashtags, background prompt variants)
CATEGORY_PROFILES: dict[str, tuple[list[str], list[str]]] = {
    "tempo": (
        ["#tempo", "#pensieroessenziale"],
        ["soft warm light falling across an empty room, slow morning atmosphere",
         "faint shadow of a window moving on a bare plaster wall",
         "still surface of water at dawn, almost no ripples",
         "warm sand texture with a single long shadow"],
    ),
    "identita": (
        ["#identità", "#pensieroessenziale"],
        ["soft blurred reflection on brushed metal, warm neutral tones",
         "layered translucent paper sheets in cream and sand",
         "gentle double exposure of two overlapping soft gradients",
         "worn linen fabric folds catching a low warm light"],
    ),
    "conoscenza": (
        ["#conoscenza", "#pensieroessenziale"],
        ["diffused light through frosted glass, quiet and even",
         "soft dust suspended in a warm shaft of light",
         "muted horizon line between two pale fields of colour",
         "faint concentric rings spreading on a still pale surface"],
    ),
    "liberta": (
        ["#libertà", "#pensieroessenziale"],
        ["wide pale sky with a single soft cloud band, minimal",
         "open warm plain fading into haze, very low detail",
         "light breeze suggested by soft blurred grass tones",
         "empty warm gradient with a distant faint opening"],
    ),
    "relazione": (
        ["#relazioni", "#pensieroessenziale"],
        ["two soft overlapping circles of warm light, gentle blend",
         "woven natural fibre texture in cream and clay",
         "two faint shadows meeting on a warm plaster wall",
         "soft gradient where two warm tones slowly merge"],
    ),
    "desiderio": (
        ["#desiderio", "#pensieroessenziale"],
        ["warm glow just beyond a soft blurred edge",
         "peach and amber gradient with a faint distant light",
         "soft candle-like warmth diffused across sand tones",
         "gentle halo of warm light on a matte surface"],
    ),
    "limite": (
        ["#limiti", "#pensieroessenziale"],
        ["soft edge where warm light meets shadow, minimal",
         "matte stone texture with one quiet natural line",
         "pale wall meeting a darker warm floor tone",
         "single soft horizontal band across a cream field"],
    ),
    "linguaggio": (
        ["#parole", "#pensieroessenziale"],
        ["blank warm paper texture with faint fibre detail",
         "soft ink diffusion in water, very pale sepia",
         "quiet folds of thin paper catching low light",
         "pale gradient with a barely visible woven grain"],
    ),
    "natura": (
        ["#natura", "#pensieroessenziale"],
        ["soft out-of-focus foliage in muted warm green",
         "gentle sand ripples under diffused daylight",
         "pale stone and moss tones, very low contrast",
         "distant hazy hills reduced to soft warm bands"],
    ),
    "azione": (
        ["#scelte", "#pensieroessenziale"],
        ["single soft path of light across a matte floor",
         "warm textured surface with one quiet directional grain",
         "clay and sand tones with a faint forward gradient",
         "soft morning light spreading from one side"],
    ),
}

EXTRA_TAGS = [
    "#filosofia", "#riflessioni", "#pensieri", "#consapevolezza",
    "#lentezza", "#introspezione", "#silenzio", "#quotidiano",
    "#pensierodelgiorno", "#pausa",
]

#: Closing invitations rotated across the dataset (never inside the image).
CALLS_TO_ACTION = [
    "Su cosa ti fa tornare questo pensiero?",
    "Ti riconosci in questa descrizione?",
    "Quando ti è capitato di accorgertene?",
    "Che cosa cambieresti, sapendolo?",
    "Ti convince o ti lascia dubbioso?",
    "Vale anche nella tua esperienza?",
    "Da dove cominceresti, oggi?",
    "Che nome daresti a questa sensazione?",
]


def build() -> list[dict]:
    items: list[dict] = []
    for i, (text, expansion, category, mood) in enumerate(THOUGHTS):
        base, prompts = CATEGORY_PROFILES[category]
        items.append({
            "id": f"pt-{i:04d}-{slugify(text)[:32]}",
            "sequence_index": i,
            "text": text,
            "category": category,
            "mood": mood,
            "caption": expansion,
            "call_to_action": pick(CALLS_TO_ACTION, i),
            "hashtags": hashtags(base, EXTRA_TAGS, i, total=6),
            "background_prompt": pick(prompts, i // 7),
            "status": "approved_for_publication",
            "metadata": {"theme": category},
        })
    return items
