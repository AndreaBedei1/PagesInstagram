"""Dataset builder — Una Domanda al Giorno (``daily_question``).

Editorial rules:

* original questions, understandable with no context and no preamble;
* nothing intrusive: no health, trauma, personal data or sensitive situations;
* themes alternate across identity, time, memory, relationships, choices, work,
  happiness, technology, future and society;
* no two questions may be semantic near-duplicates (the validator enforces it).

Each authored entry is ``(question, nudge, theme)``; ``nudge`` becomes the
caption and frames *why* the question is worth a minute.
"""
from __future__ import annotations

from .common import hashtags, pick, slugify
from .data.questions import QUESTIONS

CONTENT_TYPE = "daily_question"

THEME_PROFILES: dict[str, tuple[list[str], str]] = {
    "identita": (["#identità"], "calm"),
    "tempo": (["#tempo"], "reflective"),
    "memoria": (["#memoria"], "tender"),
    "relazioni": (["#relazioni"], "tender"),
    "scelte": (["#scelte"], "determined"),
    "lavoro": (["#lavoro"], "focused"),
    "felicita": (["#felicità"], "hopeful"),
    "tecnologia": (["#tecnologia"], "focused"),
    "futuro": (["#futuro"], "hopeful"),
    "societa": (["#società"], "reflective"),
}

BACKGROUND_PROMPTS = [
    "very dark charcoal background with a single faint distant glow, minimal",
    "deep near-black gradient with a soft blue undertone, almost empty frame",
    "matte black surface with a barely visible grain, quiet and still",
    "dark slate field with one soft diffused light at the top, low contrast",
    "very dark indigo gradient fading to black, smooth and calm",
    "black background with a faint warm ember glow far away, minimal",
]

EXTRA_TAGS = [
    "#unadomandaalgiorno", "#domandadelgiorno", "#riflessioni", "#pensieri",
    "#conversazioni", "#introspezione", "#confronto", "#parliamone",
]

CALLS_TO_ACTION = [
    "Rispondi nei commenti: leggo tutto.",
    "Scrivi la prima cosa che ti è venuta in mente.",
    "Curioso di leggere risposte diverse dalla mia.",
    "Non serve una risposta definitiva: basta la tua.",
    "Se ti va, raccontala in una riga.",
    "Anche un forse è una risposta.",
]


def build() -> list[dict]:
    items: list[dict] = []
    for i, (question, nudge, theme) in enumerate(QUESTIONS):
        base, mood = THEME_PROFILES[theme]
        items.append({
            "id": f"dq-{i:04d}-{slugify(question)[:32]}",
            "sequence_index": i,
            "text": question,
            "category": theme,
            "mood": mood,
            "caption": nudge,
            "call_to_action": pick(CALLS_TO_ACTION, i),
            "hashtags": hashtags(base, EXTRA_TAGS, i, total=6),
            "background_prompt": pick(BACKGROUND_PROMPTS, i // 5),
            "status": "approved_for_publication",
            "metadata": {"theme": theme, "number": i + 1},
        })
    return items
