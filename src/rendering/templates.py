"""Declarative, block-based typographic templates — one per content type.

Each template is an ordered stack of :class:`Block` specs. The renderer binary
searches a single *base* font size so the **whole stack** fits the safe box;
every block's own size is ``base * scale`` (floored at ``min_px``). That gives
each page a genuinely different typographic hierarchy — not one layout recoloured
five times — while keeping a single, testable fitting algorithm.

The model never writes text: every glyph on the image comes from here via Pillow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

MONTHS_IT = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)


@dataclass(frozen=True)
class Block:
    """One typographic element in a template stack."""

    key: str                      #: field name (or a literal for ``kind='rule'``)
    family: str = "sans"
    scale: float = 1.0            #: font size relative to the fitted base size
    max_lines: int = 4
    space_before: float = 0.0     #: vertical gap, in multiples of the base size
    line_spacing: float = 1.20
    uppercase: bool = False
    tracking: float = 0.0         #: letterspacing, as a fraction of the font size
    alpha: int = 255
    kind: str = "text"            #: ``text`` | ``rule``
    rule_width: float = 0.16      #: rule length as a fraction of the box width
    rule_thickness: float = 0.035  #: rule thickness, fraction of the base size
    min_px: int = 18
    quote_marks: bool = False
    optional: bool = True         #: a missing/empty field simply disappears


@dataclass(frozen=True)
class LayoutTemplate:
    """A full page layout: the block stack plus fitting bounds."""

    id: str
    blocks: tuple[Block, ...]
    base_max: int = 112
    base_min: int = 30
    anchor: str = "center"        #: center | upper | lower
    body_key: str = "text"        #: block reported as "the text" to the validator
    body_max_lines: int = 6

    def block(self, key: str) -> Block | None:
        for b in self.blocks:
            if b.key == key:
                return b
        return None


# ---------------------------------------------------------------------------
#  The five evergreen templates (+ the two legacy ones, same engine)
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, LayoutTemplate] = {
    # 1 — Pensiero Essenziale: one calm centred thought, nothing else.
    "philosophical_thought": LayoutTemplate(
        id="philosophical_thought",
        base_max=104, base_min=34, anchor="center", body_max_lines=6,
        blocks=(
            Block(key="text", family="serif", scale=1.0, max_lines=6,
                  line_spacing=1.30, min_px=34, optional=False),
            Block(key="rule", kind="rule", space_before=0.42, rule_width=0.10,
                  alpha=150),
        ),
    ),

    # 2 — Curiosità dal Mondo: a small place/topic label, then the fact.
    "world_curiosity": LayoutTemplate(
        id="world_curiosity",
        base_max=94, base_min=34, anchor="center", body_max_lines=6,
        blocks=(
            Block(key="eyebrow", family="sans_semibold", scale=0.30, max_lines=2,
                  uppercase=True, tracking=0.16, alpha=225, min_px=24,
                  line_spacing=1.35),
            Block(key="rule", kind="rule", space_before=0.26, rule_width=0.13,
                  alpha=160),
            Block(key="text", family="sans_semibold", scale=1.0, max_lines=6,
                  space_before=0.34, line_spacing=1.24, min_px=34, optional=False),
            Block(key="tag", family="sans", scale=0.24, max_lines=1,
                  space_before=0.46, uppercase=True, tracking=0.20, alpha=185,
                  min_px=20),
        ),
    ),

    # 3 — Parola del Giorno: the word dominates; definition is secondary.
    "word_of_the_day": LayoutTemplate(
        id="word_of_the_day",
        base_max=184, base_min=44, anchor="center", body_max_lines=2,
        blocks=(
            Block(key="label", family="sans", scale=0.16, max_lines=1,
                  uppercase=True, tracking=0.24, alpha=170, min_px=20),
            Block(key="text", family="serif", scale=1.0, max_lines=2,
                  space_before=0.22, line_spacing=1.06, min_px=44, optional=False),
            Block(key="pos", family="serif_italic", scale=0.235, max_lines=1,
                  space_before=0.14, alpha=205, min_px=24),
            Block(key="rule", kind="rule", space_before=0.30, rule_width=0.22,
                  alpha=150),
            Block(key="definition", family="sans", scale=0.245, max_lines=5,
                  space_before=0.30, line_spacing=1.34, alpha=240, min_px=26),
        ),
    ),

    # 4 — Oggi nella Storia: date badge, big year, title, one-line description.
    "today_in_history": LayoutTemplate(
        id="today_in_history",
        base_max=190, base_min=40, anchor="center", body_max_lines=3,
        blocks=(
            Block(key="day", family="sans_semibold", scale=0.20, max_lines=1,
                  uppercase=True, tracking=0.26, alpha=215, min_px=22),
            Block(key="year", family="serif", scale=1.0, max_lines=1,
                  space_before=0.10, line_spacing=1.02, min_px=40),
            Block(key="rule", kind="rule", space_before=0.18, rule_width=0.20,
                  alpha=160),
            Block(key="text", family="sans_semibold", scale=0.315, max_lines=3,
                  space_before=0.26, line_spacing=1.24, min_px=34, optional=False),
            Block(key="description", family="sans", scale=0.215, max_lines=4,
                  space_before=0.24, line_spacing=1.36, alpha=225, min_px=24),
        ),
    ),

    # 5 — Una Domanda al Giorno: number, then the question, maximum legibility.
    "daily_question": LayoutTemplate(
        id="daily_question",
        base_max=100, base_min=34, anchor="center", body_max_lines=6,
        blocks=(
            Block(key="number", family="sans", scale=0.26, max_lines=1,
                  uppercase=True, tracking=0.22, alpha=175, min_px=22),
            Block(key="text", family="sans_semibold", scale=1.0, max_lines=6,
                  space_before=0.42, line_spacing=1.28, min_px=34, optional=False),
            Block(key="rule", kind="rule", space_before=0.48, rule_width=0.09,
                  alpha=150),
        ),
    ),

    # -- legacy pages (archived YAMLs) — same engine, original look ----------
    "motivational": LayoutTemplate(
        id="motivational", base_max=120, base_min=32, body_max_lines=8,
        blocks=(
            Block(key="text", family="sans_semibold", scale=1.0, max_lines=8,
                  line_spacing=1.24, min_px=34, optional=False),
        ),
    ),
    "famous_quote": LayoutTemplate(
        id="famous_quote", base_max=104, base_min=30, body_max_lines=8,
        blocks=(
            Block(key="text", family="serif", scale=1.0, max_lines=8,
                  line_spacing=1.30, min_px=34, quote_marks=True, optional=False),
            Block(key="author", family="serif_italic", scale=0.50, max_lines=2,
                  space_before=0.55, min_px=24),
        ),
    ),
}

DEFAULT_TEMPLATE = "motivational"


def get_template(template_id: str | None) -> LayoutTemplate:
    return TEMPLATES.get(template_id or "", TEMPLATES[DEFAULT_TEMPLATE])


# ---------------------------------------------------------------------------
#  content row -> template fields
# ---------------------------------------------------------------------------

def _meta(content: dict) -> dict:
    raw = content.get("metadata_json")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def day_label(calendar_key: str | None) -> str:
    """``'08-04'`` → ``'4 agosto'`` (empty string when unknown)."""
    if not calendar_key or "-" not in str(calendar_key):
        return ""
    try:
        mm, dd = str(calendar_key).split("-")[:2]
        return f"{int(dd)} {MONTHS_IT[int(mm) - 1]}"
    except (ValueError, IndexError):
        return ""


def build_fields(template_id: str, content: dict, *,
                 show_author: bool = False) -> dict[str, str]:
    """Map a ``contents`` row onto the fields its template expects."""
    meta = _meta(content)
    text = (content.get("text") or "").strip()

    if template_id == "philosophical_thought":
        return {"text": text}

    if template_id == "world_curiosity":
        eyebrow = (meta.get("location") or meta.get("title")
                   or content.get("category") or "").strip()
        return {"eyebrow": eyebrow, "text": text,
                "tag": (content.get("category") or "").strip()}

    if template_id == "word_of_the_day":
        return {
            "label": "parola del giorno",
            "text": text,
            "pos": (meta.get("part_of_speech") or content.get("category") or "").strip(),
            "definition": (meta.get("definition") or content.get("explanation") or "").strip(),
        }

    if template_id == "today_in_history":
        year = str(meta.get("year") or "").strip()
        return {
            "day": day_label(content.get("calendar_key")),
            "year": year,
            "text": text,
            "description": (meta.get("description") or content.get("explanation") or "").strip(),
        }

    if template_id == "daily_question":
        idx = content.get("sequence_index")
        number = f"n. {int(idx) + 1:03d}" if idx is not None else ""
        return {"number": number, "text": text}

    # legacy
    fields = {"text": text}
    author = content.get("author_display_name") or content.get("author")
    if show_author and author:
        fields["author"] = f"— {author}"
    return fields
