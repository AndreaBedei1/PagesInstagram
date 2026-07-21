"""Text normalization and content hashing (used for exact dedup + fuzzy compare)."""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Curly quotes / dashes → straight equivalents so visually-identical text hashes equal.
_QUOTE_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", "«": '"', "»": '"',
}

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def unify_punctuation(text: str) -> str:
    for k, v in _QUOTE_MAP.items():
        text = text.replace(k, v)
    return text


def normalize_text(text: str) -> str:
    """Lowercase, unify punctuation, strip punctuation, collapse whitespace.

    Accented letters are preserved (they are meaningful in Italian). The result
    is used for exact-hash dedup and as the basis for fuzzy/semantic compare.
    """
    if not text:
        return ""
    text = unify_punctuation(text)
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def content_hash(text: str) -> str:
    """Stable 32-hex-char hash of the normalized text (exact-duplicate key)."""
    norm = normalize_text(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def tokens(text: str) -> list[str]:
    return normalize_text(text).split()
