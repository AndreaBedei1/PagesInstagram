"""Automatic Italian language checks over the dataset text.

Not a grammar engine — a set of narrow, high-precision rules for the mistakes
that actually reach a rendered image and that a human proof-reader keeps
missing on the thousandth item. Every rule earned its place by finding a real
error in these datasets, or by guarding one that was found by hand.

Each finding is a :class:`LanguageIssue` with a severity:

``error``
    Certainly wrong. ``validate-datasets`` fails on these.
``warning``
    Suspicious. Reported, never blocking, because the rule has known false
    positives (an absolute claim may be perfectly accurate).

Deliberately **not** implemented: blind global substitutions. Every fix in this
repository was applied to a specific item after reading it.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Fields that end up in front of a reader (image or caption).
CHECKED_FIELDS = ("text", "caption", "explanation", "call_to_action")

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class LanguageIssue:
    rule: str
    severity: str
    field: str
    message: str
    excerpt: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "field": self.field,
                "message": self.message, "excerpt": self.excerpt}


# ---------------------------------------------------------------------------
# Article before a numeral: "gli 80 metri" but "i 10.900 metri"
# ---------------------------------------------------------------------------
_NUM_AFTER_ARTICLE = re.compile(r"\b(gli|i)\s+(\d[\d.  ]*)", re.IGNORECASE)


def _numeral_starts_with_vowel(digits: str) -> bool:
    """Does the Italian reading of this number begin with a vowel sound?

    ``otto``/``ottanta``/``ottocento``/``ottomila`` and ``undici``/``undicimila``
    are the cases that take *gli*; everything else takes *i*.
    """
    clean = re.sub(r"[. \s]", "", digits)
    if not clean:
        return False
    if clean.startswith("8"):
        return True
    if clean.startswith("11") and (len(clean) == 2 or len(clean) >= 5):
        # 11, 11.000, 11.500 -> "undici", "undicimila"; 110/1100 -> "centodieci"
        return True
    return False


def _check_numeral_articles(text: str) -> list[tuple[str, str, str]]:
    out = []
    for m in _NUM_AFTER_ARTICLE.finditer(text):
        article, digits = m.group(1), m.group(2).strip()
        vowel = _numeral_starts_with_vowel(digits)
        low = article.lower()
        if low == "gli" and not vowel:
            out.append(("articolo_numerale",
                        f"«{article} {digits}» — davanti a questo numero va «i»",
                        m.group(0)))
        elif low == "i" and vowel:
            out.append(("articolo_numerale",
                        f"«{article} {digits}» — davanti a questo numero va «gli»",
                        m.group(0)))
    return out


# ---------------------------------------------------------------------------
# Simple regex rules
# ---------------------------------------------------------------------------
#: (rule, severity, pattern, message). Patterns are searched in every field.
_RULES: tuple[tuple[str, str, re.Pattern, str], ...] = (
    ("doppio_spazio", ERROR, re.compile(r"\S  +\S"),
     "spazi multipli fra due parole"),
    ("spazio_prima_punteggiatura", ERROR, re.compile(r"\s[,.;:!?](?:\s|$)"),
     "spazio prima di un segno di punteggiatura"),
    ("punteggiatura_ripetuta", ERROR, re.compile(r"([,;:!?])\1|\.{2}(?!\.)|\.{4,}"),
     "punteggiatura duplicata"),
    ("spazio_ai_bordi", ERROR, re.compile(r"^\s|\s$"),
     "spazio all'inizio o alla fine del campo"),
    ("qual_apostrofo", ERROR, re.compile(r"\bqual'", re.IGNORECASE),
     "«qual è» si scrive senza apostrofo"),
    ("e_maiuscola_apostrofo", ERROR, re.compile(r"\bE'\s"),
     "«E'» va scritto «È»"),
    ("un_apostrofo_maschile", ERROR,
     re.compile(r"\bun'\s*(?=[bcdfghjklmnpqrstvwxz])", re.IGNORECASE),
     "«un'» si usa solo davanti a vocale"),
    ("po_senza_apostrofo", ERROR, re.compile(r"\bun po\b(?!')"),
     "«un po'» richiede l'apostrofo"),
    ("accento_mancante", ERROR,
     re.compile(r"\b(perche|poiche|finche|benche|cioe|percio|piu|gia|cosi|"
                r"pero|caffe|citta|liberta|verita|meta(?=\s)|puo)\b"),
     "manca l'accento"),
    ("articolo_s_impura", ERROR,
     re.compile(r"\b(il|un)\s+(s[bcdfgklmnpqrtvz]|z|gn|ps|pn|x|y)\w", re.IGNORECASE),
     "davanti a s impura / z / gn / ps / pn / x / y servono «lo» e «uno»"),
    ("url_nel_testo", ERROR, re.compile(r"https?://|\bwww\."),
     "un URL non deve comparire nel testo mostrato"),
    ("carattere_non_valido", ERROR,
     re.compile("[�​‌‍  ­]"),
     "carattere invisibile o di sostituzione nel testo"),
    ("nbsp", ERROR, re.compile(" "),
     "spazio unificatore (U+00A0): usa uno spazio normale"),
    ("unita_attaccata", ERROR,
     re.compile(r"\b\d+(?:[.,]\d+)?(km|cm|mm|kg|mq|kmq|ml|kW|MW|GW)\b"),
     "manca lo spazio fra numero e unità di misura"),
    # "rendere illeggibili." with nothing to render. An enclitic pronoun
    # ("renderci", "renderlo") or a following object makes it correct, so the
    # rule only fires on the bare infinitive immediately before a full stop.
    ("verbo_senza_oggetto", ERROR,
     re.compile(r"\b(rendere|lasciare|fare|tenere|mantenere)\s+"
                r"(illeggibil|invisibil|irriconoscibil|incomprensibil|inutil|"
                r"vulnerabil|prevedibil|fragil|opach|sord|ciech)\w+\s*[.!?]"),
     "verbo transitivo senza complemento oggetto (manca un clitico?)"),
    ("data_impossibile", ERROR,
     re.compile(r"\b(3[2-9]|[4-9]\d)\s+(gennaio|febbraio|marzo|aprile|maggio|"
                r"giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\b",
                re.IGNORECASE),
     "giorno del mese inesistente"),
    ("febbraio_30", ERROR,
     re.compile(r"\b(30|31)\s+febbraio\b", re.IGNORECASE),
     "data inesistente"),
    ("maiuscola_iniziale", WARNING, re.compile(r"^[a-zàèéìòù]"),
     "il campo inizia in minuscolo"),
    ("assoluto", WARNING,
     re.compile(r"\b(l'unic\w+|unico al mondo|il solo\b|il primo in assoluto|"
                r"mai esistit\w+|il più grande del mondo|il più antico del mondo|"
                r"visibile dallo spazio|il più profondo del mondo|"
                r"il più veloce del mondo|da sempre|per sempre)\b", re.IGNORECASE),
     "affermazione assoluta: la fonte deve sostenerla esplicitamente"),
)

_OPEN_CLOSE = (("(", ")"), ("«", "»"), ("“", "”"), ("[", "]"))


def _check_balanced(text: str) -> list[tuple[str, str, str]]:
    out = []
    for opener, closer in _OPEN_CLOSE:
        if text.count(opener) != text.count(closer):
            out.append(("delimitatori_sbilanciati",
                        f"«{opener}{closer}» non bilanciati "
                        f"({text.count(opener)} aperti, {text.count(closer)} chiusi)",
                        text[:80]))
    if text.count('"') % 2:
        out.append(("delimitatori_sbilanciati",
                    "virgolette dritte in numero dispari", text[:80]))
    return out


def _check_control_chars(text: str) -> list[tuple[str, str, str]]:
    bad = {ch for ch in text
           if unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\n\t"}
    if not bad:
        return []
    names = ", ".join(f"U+{ord(c):04X}" for c in sorted(bad))
    return [("carattere_di_controllo", f"caratteri di controllo: {names}", text[:60])]


_SEVERITY = {rule: sev for rule, sev, _, _ in _RULES}
_SEVERITY["articolo_numerale"] = ERROR
_SEVERITY["delimitatori_sbilanciati"] = ERROR
_SEVERITY["carattere_di_controllo"] = ERROR

#: Fields where a lowercase opening is normal (a lemma, a one-word answer).
_LOWERCASE_OK = {"word_of_the_day": {"text", "caption"}}


def check_text(text: str, *, field: str = "text",
               content_type: str = "") -> list[LanguageIssue]:
    """Every issue found in one string. Never raises."""
    if not isinstance(text, str) or not text:
        return []
    issues: list[LanguageIssue] = []

    def add(rule: str, message: str, excerpt: str) -> None:
        if rule == "maiuscola_iniziale" and field in _LOWERCASE_OK.get(content_type, ()):
            return
        issues.append(LanguageIssue(rule=rule, severity=_SEVERITY.get(rule, WARNING),
                                    field=field, message=message,
                                    excerpt=excerpt.strip()[:120]))

    for rule, _sev, pattern, message in _RULES:
        m = pattern.search(text)
        if m:
            add(rule, message, m.group(0) or text[:60])
    for rule, message, excerpt in _check_numeral_articles(text):
        add(rule, message, excerpt)
    for rule, message, excerpt in _check_balanced(text):
        add(rule, message, excerpt)
    for rule, message, excerpt in _check_control_chars(text):
        add(rule, message, excerpt)
    return issues


def check_item(item: dict, *, content_type: str = "") -> list[LanguageIssue]:
    """Every issue across the reader-facing fields of one dataset item."""
    out: list[LanguageIssue] = []
    for field in CHECKED_FIELDS:
        value = item.get(field)
        if isinstance(value, str):
            out.extend(check_text(value, field=field, content_type=content_type))
    return out


def check_items(items: list[dict], *, content_type: str = "") -> dict[int, list[LanguageIssue]]:
    found: dict[int, list[LanguageIssue]] = {}
    for i, item in enumerate(items):
        issues = check_item(item, content_type=content_type)
        if issues:
            found[i] = issues
    return found


def errors_only(found: dict[int, list[LanguageIssue]]) -> dict[int, list[LanguageIssue]]:
    out = {}
    for idx, issues in found.items():
        errs = [i for i in issues if i.severity == ERROR]
        if errs:
            out[idx] = errs
    return out
