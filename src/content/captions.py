"""Build Instagram captions from a content row + page config.

- Motivational: explanation/application caption + optional CTA question.
- Quote: caption (explains + introduces author/work) with an explicit attribution
  line; author is always present.
Hashtags are varied per content (deterministic rotation) so the same block is not
repeated on every post.
"""
from __future__ import annotations

import hashlib
import json


def _parse_hashtags(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        tags = raw
    else:
        try:
            tags = json.loads(raw)
        except (ValueError, TypeError):
            tags = [t for t in str(raw).split() if t.startswith("#")]
    out = []
    for t in tags:
        t = str(t).strip()
        if t and not t.startswith("#"):
            t = "#" + t
        if t:
            out.append(t)
    return out


def _rotate_subset(tags: list[str], key: str, max_n: int) -> list[str]:
    """Deterministically rotate + cap the hashtag list so posts differ."""
    if not tags:
        return []
    seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    n = len(tags)
    start = seed % n
    rotated = tags[start:] + tags[:start]
    return rotated[:max_n]


def build_caption(content: dict, *, content_type: str | None = None,
                  max_hashtags: int = 12) -> str:
    ctype = content_type or content.get("content_type") or "motivational"
    parts: list[str] = []

    caption = (content.get("caption") or "").strip()
    if caption:
        parts.append(caption)

    if ctype == "famous_quote":
        author = content.get("author_display_name") or content.get("author")
        work = content.get("source_work")
        if author:
            attribution = f"— {author}"
            if work:
                attribution += f", {work}"
            parts.append(attribution)

    cta = (content.get("call_to_action") or "").strip()
    if cta:
        parts.append(cta)

    text = "\n\n".join(parts)

    tags = _rotate_subset(_parse_hashtags(content.get("hashtags")),
                          key=str(content.get("content_hash") or content.get("id") or caption),
                          max_n=max_hashtags)
    if tags:
        text += "\n\n" + " ".join(tags)
    return text.strip()
