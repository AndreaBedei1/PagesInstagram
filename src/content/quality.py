"""Measurable text-quality scoring for content items.

Produces a score in [0, 1] from independent, testable sub-checks plus a list of
human-readable issues. Language-aware for Italian (Gulpease readability index).
No heavy NLP / no network — pure heuristics so it runs anywhere.

A pluggable grammar backend (LanguageTool) is used automatically *if installed*;
otherwise lightweight style heuristics apply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .normalize import normalize_text, tokens, unify_punctuation

# Compact Italian stopword set (function words) for a genericity signal.
_IT_STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "a", "da",
    "in", "con", "su", "per", "tra", "fra", "e", "ed", "o", "ma", "se", "che",
    "chi", "cui", "non", "ne", "si", "sì", "no", "come", "dove", "quando",
    "perché", "più", "meno", "molto", "poco", "tutto", "tutti", "ogni", "al",
    "allo", "alla", "ai", "agli", "alle", "del", "dello", "della", "dei",
    "degli", "delle", "nel", "nella", "sul", "sulla", "è", "sono", "sei",
    "essere", "questo", "questa", "quello", "quella", "mi", "ti", "ci", "vi",
    "lui", "lei", "noi", "voi", "loro", "io", "tu", "suo", "sua", "tuo", "tua",
    "mio", "mia", "ha", "hai", "ho", "anche", "solo", "già", "sempre", "mai",
    "cosa", "fare", "puoi", "può",
}

# A few vacuous cliché fragments that, alone, make a phrase feel mass-produced.
_CLICHE_FRAGMENTS = (
    "segui i tuoi sogni",
    "non arrenderti mai",
    "credi in te stesso e",
    "tutto è possibile se",
)

_SENTENCE_SPLIT = re.compile(r"[.!?]+")

#: Per-content-type text length bands: (comfortable_min, comfortable_max, hard_max).
#: The heuristics were originally tuned for motivational one-liners; the evergreen
#: pages have very different natural shapes (a single lemma, a fact, a question),
#: so each type declares its own band instead of being penalised for its format.
LENGTH_PROFILES: dict[str, tuple[int, int, int]] = {
    "famous_quote": (20, 180, 240),
    "philosophical_thought": (25, 125, 170),
    "daily_question": (20, 125, 160),
    "world_curiosity": (40, 190, 250),
    "today_in_history": (18, 125, 170),
    "word_of_the_day": (3, 28, 40),
    "motivational": (15, 110, 160),
}

#: Types whose ``text`` is a single lexical item, not a sentence.
_SINGLE_TOKEN_TYPES = frozenset({"word_of_the_day"})

#: Types whose ``text`` is legitimately lowercase (dictionary lemmas).
_LOWERCASE_OK_TYPES = frozenset({"word_of_the_day"})

#: Types whose ``text`` must be a question.
_QUESTION_TYPES = frozenset({"daily_question"})


def gulpease_index(text: str) -> float:
    """Italian readability (Gulpease). 0..100, higher = easier to read."""
    letters = sum(1 for c in text if c.isalpha())
    words = len(re.findall(r"\w+", text))
    sentences = max(1, len([s for s in _SENTENCE_SPLIT.split(text) if s.strip()]))
    if words == 0:
        return 0.0
    idx = 89 + (300 * sentences - 10 * letters) / words
    return max(0.0, min(100.0, idx))


@dataclass
class QualityResult:
    score: float
    subscores: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def passes(self, minimum: float) -> bool:
        return self.score >= minimum


def _length_subscore(text: str, lo: int, hi: int, hard_max: int) -> tuple[float, list[str]]:
    n = len(text)
    issues: list[str] = []
    if n == 0:
        return 0.0, ["testo vuoto"]
    if n > hard_max:
        return 0.2, [f"testo troppo lungo ({n} caratteri)"]
    if n < lo:
        return 0.5, [f"testo molto corto ({n} caratteri)"]
    if n > hi:
        return 0.75, [f"testo lungo ({n} caratteri)"]
    return 1.0, issues


def _structure_subscore(text: str, content_type: str = "motivational"
                        ) -> tuple[float, list[str]]:
    toks = tokens(text)
    wc = len(toks)
    issues: list[str] = []
    if content_type in _SINGLE_TOKEN_TYPES:
        # A dictionary lemma: one or two tokens is exactly right.
        if wc == 0:
            return 0.0, ["parola mancante"]
        if wc > 3:
            return 0.5, ["non è un lemma singolo"]
        return 1.0, issues
    if wc < 3:
        return 0.3, ["troppo poche parole"]
    if wc > 32:
        issues.append("molte parole")
        return 0.7, issues
    if wc > 28:
        issues.append("molte parole")
        return 0.85, issues
    return 1.0, issues


def _readability_subscore(text: str) -> tuple[float, list[str]]:
    g = gulpease_index(text)
    # Map Gulpease 40..95 -> 0..1 (short aphorisms usually score high, which is fine).
    s = (g - 40) / (95 - 40)
    return max(0.0, min(1.0, s)), ([] if g >= 45 else ["difficile da leggere"])


def _genericity_subscore(text: str) -> tuple[float, list[str]]:
    toks = tokens(text)
    if not toks:
        return 0.0, ["nessun contenuto"]
    content_words = [t for t in toks if t not in _IT_STOPWORDS and len(t) > 2]
    ratio = len(set(content_words)) / max(1, len(toks))
    issues: list[str] = []
    norm = normalize_text(text)
    for frag in _CLICHE_FRAGMENTS:
        if frag in norm:
            issues.append("frase troppo generica/cliché")
            return 0.35, issues
    # ratio ~0.4+ is healthy for a short phrase.
    score = min(1.0, ratio / 0.55)
    if score < 0.5:
        issues.append("poca specificità (molte parole comuni)")
    return score, issues


def _style_subscore(text: str, content_type: str = "motivational"
                    ) -> tuple[float, list[str]]:
    issues: list[str] = []
    s = 1.0
    t = unify_punctuation(text.strip())
    if not t:
        return 0.0, ["vuoto"]
    if t[0].islower() and content_type not in _LOWERCASE_OK_TYPES:
        s -= 0.25
        issues.append("non inizia con la maiuscola")
    if content_type in _QUESTION_TYPES and not t.endswith("?"):
        s -= 0.35
        issues.append("la domanda non termina con '?'")
    if "  " in t:
        s -= 0.2
        issues.append("doppi spazi")
    if t.isupper() and len(t) > 4:
        s -= 0.3
        issues.append("tutto maiuscolo")
    if t.count('"') % 2 != 0:
        s -= 0.2
        issues.append("virgolette non bilanciate")
    if re.search(r"\s[,.;:!?]", t):
        s -= 0.15
        issues.append("spazio prima della punteggiatura")
    # repeated adjacent word (e.g. "la la")
    if re.search(r"\b(\w+)\s+\1\b", normalize_text(t)):
        s -= 0.2
        issues.append("parola ripetuta consecutiva")
    return max(0.0, s), issues


def _try_languagetool(text: str, lang: str) -> int | None:
    """Return #grammar matches if language_tool_python is installed, else None."""
    try:  # optional dependency
        import language_tool_python  # type: ignore

        tool = language_tool_python.LanguageToolPublicAPI(lang)
        return len(tool.check(text))
    except Exception:
        return None


def score_content(
    text: str,
    *,
    content_type: str = "motivational",
    caption: str | None = None,
    language: str = "it",
    use_languagetool: bool = False,
) -> QualityResult:
    """Score a content item. Returns a :class:`QualityResult`."""
    lo, hi, hard_max = LENGTH_PROFILES.get(content_type,
                                           LENGTH_PROFILES["motivational"])

    subs: dict[str, float] = {}
    issues: list[str] = []

    for name, (val, iss) in {
        "length": _length_subscore(text, lo, hi, hard_max),
        "structure": _structure_subscore(text, content_type),
        "readability": _readability_subscore(text),
        "genericity": _genericity_subscore(text),
        "style": _style_subscore(text, content_type),
    }.items():
        subs[name] = round(val, 3)
        issues.extend(iss)

    # Caption sub-check: present, reasonable length, and NOT a mere repeat of text.
    cap_score = 1.0
    if caption is not None:
        cap = caption.strip()
        if len(cap) < 20:
            cap_score = 0.4
            issues.append("didascalia troppo corta")
        elif normalize_text(cap) == normalize_text(text):
            cap_score = 0.2
            issues.append("la didascalia ripete solo la frase")
        elif normalize_text(text) in normalize_text(cap) and len(cap) < len(text) * 1.6:
            cap_score = 0.6
            issues.append("didascalia poco più che una ripetizione")
    subs["caption"] = round(cap_score, 3)

    if use_languagetool:
        matches = _try_languagetool(text, language)
        if matches is not None:
            g = max(0.0, 1.0 - 0.15 * matches)
            subs["grammar"] = round(g, 3)
            if matches:
                issues.append(f"{matches} possibili errori grammaticali")

    weights = {
        "length": 0.18,
        "structure": 0.14,
        "readability": 0.14,
        "genericity": 0.22,
        "style": 0.20,
        "caption": 0.12,
    }
    if "grammar" in subs:
        weights = {k: v * 0.9 for k, v in weights.items()}
        weights["grammar"] = 0.10

    total_w = sum(weights.values())
    score = sum(subs[k] * w for k, w in weights.items()) / total_w

    # Degeneracy gate: ultra-short / near-empty text can never look "good"
    # regardless of how the weighted average lands. Single-lemma types are
    # exempt — for them one short token is the correct shape, not degeneracy.
    wc = len(tokens(text))
    n = len(text.strip())
    if content_type not in _SINGLE_TOKEN_TYPES:
        if wc < 4:
            score *= 0.55
            issues.append("frase troppo breve per essere pubblicabile")
        if n < 12:
            score *= 0.6
    elif n < 3:
        score *= 0.4
        issues.append("lemma troppo corto")
    subs["_gate_wc"] = wc

    return QualityResult(score=round(min(1.0, score), 4), subscores=subs, issues=issues)
