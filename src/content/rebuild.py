"""Source-first reconstruction of the contents that verification rejected.

The previous corpus was written first and given sources afterwards, which is why
912 curiosities share too few words with the page they cite for any reading to
call them supported. Repairing that item by item is the wrong shape of work: the
claim was never derived from the source, so there is nothing to repair.

This inverts the order. Fetch a source, extract a statement it actually makes,
and write the content **from** that statement. Verification then passes because
the claim came out of the evidence, not because a threshold was lowered — and
the evidence stored alongside is the sentence the claim was built from.

Three builders, one per factual page:

``history``
    The Italian Wikipedia day page for each ``MM-DD`` lists events as
    "year – description". Those listings are maintained separately from the
    articles they describe, so an entry plus the article's own mention of the
    year is a genuine cross-check. Topics are balanced away from the wars and
    deaths those pages are heavy with.

``curiosities``
    Curated topic collections (space, oceans, biodiversity, architecture,
    languages, …) resolved to articles through the official category API. The
    claim is a declarative sentence from the article's lead, normalised — so the
    source contains it by construction.

``words``
    Candidate lemmas probed against Treccani. Only those whose entry really is
    the entry *for that lemma*, with a matching grammatical marker, survive; the
    published definition is written from the entry, not copied out of it.

Everything checkpoints to disk after every batch: an interrupted run resumes
where it stopped and never redoes network work.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .dataset_io import load_dataset, save_dataset
from .evidence import (PageCache, MONTHS, build_session, content_tokens,
                       fetch_wikipedia_extracts, html_to_text, sentences,
                       today_iso)
from .verification import (METHOD_AUTHORITATIVE, METHOD_CROSS_CHECKED,
                           METHOD_STRUCTURED, attach_verification, claim_hash,
                           is_publishable)

WIKI_API = "https://it.wikipedia.org/w/api.php"

#: The renderer's comfortable range for a headline / claim.
MIN_TEXT, MAX_TEXT = 40, 120
#: Explanation shown under the headline.
MAX_EXPLANATION = 165

# ---------------------------------------------------------------------------
# Topic classification — used to keep a page from becoming a war memorial
# ---------------------------------------------------------------------------
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "scienza": ("scopr", "scienziat", "esperiment", "teoria", "element",
                "chimic", "fisic", "biolog", "medic", "vaccin", "genom",
                "fossil", "specie", "matematic", "premio nobel"),
    "tecnologia": ("brevett", "invent", "motore", "macchina", "computer",
                   "telefon", "radio", "televis", "internet", "software",
                   "elettric", "ferrovi", "automobil", "aereo", "volo"),
    "esplorazione": ("spedizion", "esplorat", "raggiunge", "circumnaviga",
                     "polo", "poli", "vetta", "sbarca", "naviga", "traversata",
                     "sonda", "satellite", "orbita", "luna", "marte"),
    "cultura": ("pubblicat", "romanzo", "libro", "poesia", "rivista",
                "giornale", "film", "cinema", "teatro", "opera", "musica",
                "album", "concerto", "museo", "biblioteca"),
    "arte": ("dipint", "quadro", "scultur", "affresc", "mostra", "esposizion",
             "architett", "cattedral", "basilica", "monument"),
    "diritti": ("diritto di voto", "suffragio", "emancipazion", "abolizion",
                "schiavit", "costituzion", "emendamento", "dichiarazione",
                "parit", "libert", "sciopero", "sindacat"),
    "istituzioni": ("fondat", "istituit", "nasce l", "viene creata",
                    "parlamento", "repubblica", "trattato", "accordo",
                    "convenzione", "nazioni unite", "unione europea"),
    "sport": ("olimpi", "campionat", "record", "partita", "torneo",
              "mondiali", "medaglia", "atleta"),
    "ambiente": ("parco nazionale", "riserva", "ambient", "clima",
                 "protocollo di", "specie protett", "conservazion"),
    "societa": ("censiment", "scuola", "universit", "ospedale", "citt",
                "popolazion", "trasport", "metropolitana"),
    "internazionale": ("indipendenz", "confine", "adesione", "ammesso",
                       "riconosc", "ambasciat"),
}

#: Entries dominated by these are capped: day pages are heavy with them and a
#: page of nothing but battles and deaths is not the page that was commissioned.
HEAVY_KEYWORDS = ("battagli", "guerra", "muore", "morte", "ucciso", "uccisa",
                  "assassin", "strage", "massacr", "attentat", "bombardament",
                  "terremot", "naufrag", "incendi", "epidemia", "esecuzion",
                  "giustiziat", "affonda", "disastr", "eccidio", "invasione",
                  "colpo di stato", "deportazion", "genocidi")

#: At most this share of a rebuilt page may be heavy material.
HEAVY_SHARE = 0.18


def classify(text: str) -> str:
    """Topic of an entry, matched on word starts.

    Substring matching filed a Polish coronation under exploration, because
    "Polonia" contains "polo". Keywords are prefixes of *words* now, not of
    arbitrary positions.
    """
    words = re.findall(r"[a-z]+", _fold(text))
    for topic, keys in TOPIC_KEYWORDS.items():
        for key in keys:
            parts = key.split()
            if len(parts) > 1:
                if key in " ".join(words):
                    return topic
            elif len(key) >= 6:
                if any(w.startswith(key) for w in words):
                    return topic
            # A short keyword must match the whole word: "polo" as a prefix
            # also matches "Polonia", which is how a coronation ended up filed
            # under exploration.
            elif key in words:
                return topic
    return "societa"


def is_heavy(text: str) -> bool:
    low = _fold(text)
    return any(k in low for k in HEAVY_KEYWORDS)


def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(c for c in norm if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------
class Checkpoint:
    """Progress that survives an interruption. Never committed."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False),
                             encoding="utf-8")

    def done(self, key: str) -> dict | None:
        return self.data.get(key)

    def record(self, key: str, value: dict) -> None:
        self.data[key] = value


@dataclass
class RebuildOutcome:
    dataset: str
    needed: int = 0
    rebuilt: int = 0
    kept: int = 0
    still_failing: int = 0
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"dataset": self.dataset, "needed": self.needed,
                "rebuilt": self.rebuilt, "kept": self.kept,
                "still_failing": self.still_failing, "notes": self.notes[:40]}


# ---------------------------------------------------------------------------
# Text shaping
# ---------------------------------------------------------------------------
_PAREN_RE = re.compile(r"\s*\([^)]{0,80}\)")
_REF_RE = re.compile(r"\[\d+\]")
_MULTISPACE = re.compile(r"\s+")


def strip_brackets(value: str) -> str:
    """Remove bracketed asides, matching brackets rather than guessing a length.

    The previous pattern only removed parentheses up to eighty characters, so a
    longer aside lost its opening bracket and kept its closing one — which is
    how "La sotalia) è un delfino" reached the dataset.
    """
    out, depth = [], 0
    for ch in value or "":
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _balance_quotes(value: str) -> str:
    """Drop a quotation the extractor cut in half.

    Truncating a lead sentence at a clause boundary regularly severs a quoted
    passage, leaving an opening mark with no partner. Removing the orphan reads
    better than shipping it and keeps the delimiter check honest.
    """
    out = value
    if out.count("«") != out.count("»"):
        idx = out.find("«")
        out = out[:idx].rstrip(" ,:;-") if idx >= 0 else out.replace("»", "")
    if out.count('"') % 2:
        idx = out.rfind('"')
        out = (out[:idx] + out[idx + 1:]).rstrip(" ,:;-")
    return out


def tidy(text: str) -> str:
    """Strip the artefacts of extracted prose without rewording it."""
    out = _REF_RE.sub("", text or "")
    out = strip_brackets(out)
    out = out.replace("–", "-").replace("—", "-")
    out = _MULTISPACE.sub(" ", out)
    # Removing a bracket leaves its neighbouring spaces and punctuation behind.
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])\1+", r"\1", out)
    out = re.sub(r"\.{2,}", ".", out)
    out = re.sub(r",\s*\.", ".", out)
    out = _balance_quotes(out)
    out = _MULTISPACE.sub(" ", out)
    return out.strip(" -:;,")


def as_headline(text: str, limit: int = MAX_TEXT) -> str:
    """A single clause, capitalised, within the renderer's width."""
    clean = tidy(text)
    # Prefer the part before a subordinate clause introduced by a dash or colon.
    for sep in (" - ", ": ", "; "):
        if sep in clean and len(clean.split(sep)[0]) >= MIN_TEXT:
            clean = clean.split(sep)[0]
            break
    if len(clean) > limit:
        cut = clean[:limit]
        for stop in (", ", " e ", " che ", " con ", " per ", " "):
            idx = cut.rfind(stop)
            if idx >= MIN_TEXT:
                cut = cut[:idx]
                break
        clean = cut.rstrip(" ,;:-")
    clean = clean.rstrip(".")
    return (clean[:1].upper() + clean[1:]) if clean else ""


def first_good_sentence(text: str, *, min_len: int = 60,
                        max_len: int = 260) -> str:
    for s in sentences(text or ""):
        s = tidy(s)
        if min_len <= len(s) <= max_len and not s.endswith(("cfr", "ecc")):
            return s
    return ""


# ---------------------------------------------------------------------------
# Wikipedia helpers beyond the extract API
# ---------------------------------------------------------------------------
def wiki_search(session: requests.Session, query: str, *,
                limit: int = 1) -> list[str]:
    try:
        r = session.get(WIKI_API, timeout=30, params={
            "action": "query", "format": "json", "list": "search",
            "srsearch": query, "srlimit": limit, "srnamespace": 0})
        return [h["title"] for h in (r.json().get("query") or {})
                .get("search", [])]
    except (requests.RequestException, ValueError, KeyError):
        return []


def category_members(session: requests.Session, category: str, *,
                     limit: int = 200) -> list[str]:
    """Article titles in a category — the official, structured way to get breadth."""
    titles: list[str] = []
    cont: dict = {}
    while len(titles) < limit:
        params = {"action": "query", "format": "json", "list": "categorymembers",
                  "cmtitle": f"Categoria:{category}", "cmlimit": "200",
                  "cmnamespace": "0", **cont}
        try:
            r = session.get(WIKI_API, timeout=30, params=params)
            data = r.json()
        except (requests.RequestException, ValueError):
            break
        titles.extend(m["title"] for m in
                      (data.get("query") or {}).get("categorymembers", []))
        if "continue" not in data:
            break
        cont = data["continue"]
        time.sleep(0.25)
    return titles[:limit]


def article_url(title: str) -> str:
    return "https://it.wikipedia.org/wiki/" + title.replace(" ", "_")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
_EVENT_RE = re.compile(r"^(\d{3,4})\s*[–-]\s*(.+)$")


def day_entries(day_text: str) -> list[tuple[int, str]]:
    """``(year, description)`` from the "Eventi" section of a day page."""
    out: list[tuple[int, str]] = []
    section = day_text
    if "== Eventi ==" in section:
        section = section.split("== Eventi ==", 1)[1]
        for stop in ("== Nati ==", "== Morti ==", "== Feste", "== Note"):
            if stop in section:
                section = section.split(stop, 1)[0]
    for line in section.splitlines():
        m = _EVENT_RE.match(line.strip())
        if not m:
            continue
        year, desc = int(m.group(1)), tidy(m.group(2))
        if len(desc) < 30:
            continue
        out.append((year, desc))
    return out


_DATE_IN_CLAIM = re.compile(
    r"\b(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|"
    r"agosto|settembre|ottobre|novembre|dicembre)\b", re.IGNORECASE)


def _names_another_day(claim: str, calendar_key: str) -> bool:
    """Does the headline cite a date that is not the one it is filed under?

    Day-page entries sometimes point forward — "… entra in vigore il 18 gennaio
    successivo" — and a headline built from one of those contradicts its own
    calendar slot the moment it is published.
    """
    if len(calendar_key) != 5:
        return False
    month, day = int(calendar_key[:2]), int(calendar_key[3:])
    for m in _DATE_IN_CLAIM.finditer(claim):
        cited_day = int(m.group(1))
        cited_month = MONTHS.index(m.group(2).lower()) + 1
        if (cited_month, cited_day) != (month, day):
            return True
    return False


def rebuild_history(path: Path, *, cache: PageCache, session, checkpoint: Checkpoint,
                    limit: int | None = None, progress=None) -> RebuildOutcome:
    """Replace every unverified event with one taken from the day-page listing.

    The old item's ``calendar_key`` and ``sequence_index`` are kept, so the
    calendar coverage and the rotation are unchanged; only the claim is new, and
    it is new because it came out of the source.
    """
    data = load_dataset(path)
    items = data.get("items") or []
    ctype = data.get("content_type") or ""
    out = RebuildOutcome(dataset=path.name)

    failing = [i for i in items if not is_publishable(i, content_type=ctype).ok]
    out.needed = len(failing)
    out.kept = len(items) - out.needed
    if limit:
        failing = failing[:limit]
    if not failing:
        return out

    # Every day page we will need, in one batched pass.
    keys = sorted({str(i.get("calendar_key") or "") for i in failing})
    day_titles = {k: f"{int(k[3:])} {MONTHS[int(k[:2]) - 1]}" for k in keys
                  if len(k) == 5}
    pages = fetch_wikipedia_extracts(sorted(set(day_titles.values())),
                                     session=session, cache=cache, rate=5.0)
    cache.save()

    # Claims already in the dataset, so a rebuild never duplicates one.
    used_claims = {claim_hash(i.get("text") or "") for i in items}
    from .dedup import SimilarityIndex
    similarity = SimilarityIndex(fuzzy_threshold=0.88, semantic_threshold=0.82)
    for n, existing in enumerate(items):
        if is_publishable(existing, content_type=ctype).ok:
            similarity.add(n, existing.get("text") or "")
    used_years: dict[str, set[int]] = {}
    for i in items:
        used_years.setdefault(str(i.get("calendar_key") or ""), set()).add(
            int((i.get("metadata") or {}).get("year") or 0))

    heavy_budget = int(len(failing) * HEAVY_SHARE)
    heavy_used = 0
    by_key: dict[str, list[dict]] = {}
    for item in failing:
        by_key.setdefault(str(item.get("calendar_key") or ""), []).append(item)

    # Candidate articles for the second confirmation, searched in bulk later.
    plan: list[tuple[dict, int, str, str]] = []
    for key, group in sorted(by_key.items()):
        page = pages.get(day_titles.get(key, ""), {})
        entries = day_entries(page.get("text") or "")
        if not entries:
            out.notes.append(f"{key}: nessuna voce nell'elenco del giorno")
            continue
        taken = 0
        for year, desc in entries:
            if taken >= len(group):
                break
            headline = as_headline(desc)
            if len(headline) < MIN_TEXT:
                continue
            if _names_another_day(headline, key):
                continue
            if claim_hash(headline) in used_claims:
                continue
            duplicate, _match = similarity.is_duplicate(headline)
            if duplicate:
                continue
            if year in used_years.get(key, set()):
                continue
            heavy = is_heavy(desc)
            if heavy and heavy_used >= heavy_budget:
                continue
            plan.append((group[taken], year, headline, f"{year} - {desc}"))
            used_claims.add(claim_hash(headline))
            similarity.add(10_000 + len(plan), headline)
            used_years.setdefault(key, set()).add(year)
            if heavy:
                heavy_used += 1
            taken += 1
        if taken < len(group):
            out.notes.append(f"{key}: {len(group) - taken} slot senza candidato")

    # Second confirmation: the article about the event must mention the year.
    queries = {h: None for _, _, h, _ in plan}
    titles: dict[str, str] = {}
    for n, h in enumerate(queries, 1):
        cached = checkpoint.done(f"hsearch:{h}")
        if cached is not None:
            titles[h] = cached.get("title", "")
        else:
            found = wiki_search(session, h, limit=1)
            titles[h] = found[0] if found else ""
            checkpoint.record(f"hsearch:{h}", {"title": titles[h]})
            time.sleep(0.22)
        if n % 50 == 0:
            checkpoint.save()
            if progress:
                progress(n, len(queries))
    checkpoint.save()

    wanted = sorted({t for t in titles.values() if t})
    articles = fetch_wikipedia_extracts(wanted, session=session, cache=cache,
                                        rate=5.0)
    cache.save()

    checked = today_iso()
    for item, year, headline, entry in plan:
        key = str(item.get("calendar_key") or "")
        day_title = day_titles.get(key, key)
        title = titles.get(headline) or ""
        article = articles.get(title, {}) if title else {}
        art_text = article.get("text") or ""
        confirms = str(year) in art_text

        explanation = ""
        if art_text:
            explanation = first_good_sentence(art_text, min_len=60,
                                              max_len=MAX_EXPLANATION)
        if not explanation:
            explanation = tidy(entry.split(" - ", 1)[-1])[:MAX_EXPLANATION]

        item["text"] = headline
        item["explanation"] = explanation
        item["caption"] = f"{explanation} Fonte: Wikipedia in italiano."
        item["category"] = classify(entry)
        meta = item.setdefault("metadata", {})
        meta["year"] = str(year)
        meta["description"] = explanation
        meta["category"] = item["category"]
        meta.pop("calendar_note", None)
        item["source_name"] = "Wikipedia in italiano"

        if confirms and title:
            method = METHOD_CROSS_CHECKED
            item["source_url"] = article_url(title)
            item["secondary_source_url"] = article_url(day_title)
            evidence = (f"Elenco del {day_title}: «{entry}». "
                        f"La voce «{title}» riporta l'anno {year}.")
            src_title = title
        else:
            method = METHOD_STRUCTURED
            item["source_url"] = article_url(day_title)
            item["secondary_source_url"] = ""
            evidence = f"Elenco degli eventi del {day_title}: «{entry}»."
            src_title = day_title

        attach_verification(
            item, method=method, evidence=evidence,
            source_url=item["source_url"], source_title=src_title,
            source_strength="general_encyclopedia", checked_at=checked,
            note="ricostruito a partire dall'elenco del giorno")
        item["verification_executor"] = "automated_source_first"
        out.rebuilt += 1

    save_dataset(path, data)
    out.still_failing = sum(
        1 for i in items if not is_publishable(i, content_type=ctype).ok)
    return out


# ---------------------------------------------------------------------------
# Curiosities
# ---------------------------------------------------------------------------
#: Topic collections, resolved to articles through the official category API.
#: Breadth first: a thousand facts about volcanoes is not the page that was
#: commissioned, so the pool is drawn from ten areas and capped per article.
CURIOSITY_CATEGORIES: dict[str, tuple[str, ...]] = {
    "animali": ("Cetacei", "Uccelli", "Insetti", "Rettili", "Mammiferi",
                "Anfibi", "Ragni", "Pesci", "Molluschi", "Primati",
                "Animali domestici", "Felidi", "Canidi", "Ursidi", "Roditori",
                "Squali", "Tartarughe", "Api", "Farfalle", "Coleotteri"),
    "geografia": ("Laghi", "Deserti", "Isole", "Ghiacciai", "Fiumi", "Vulcani",
                  "Catene montuose", "Mari", "Cascate", "Golfi", "Penisole",
                  "Oceani", "Stretti", "Arcipelaghi", "Altopiani", "Valli",
                  "Regioni geografiche", "Capitali"),
    "natura": ("Fenomeni atmosferici", "Foreste", "Aree naturali protette",
               "Grotte", "Alberi", "Fiori", "Frutti", "Piante alimentari",
               "Funghi", "Conifere", "Orchidee", "Nuvole", "Venti",
               "Parchi nazionali d'Italia", "Ecosistemi"),
    "spazio": ("Pianeti del sistema solare", "Satelliti naturali",
               "Missioni spaziali", "Costellazioni", "Comete",
               "Esplorazione della Luna", "Telescopi", "Asteroidi",
               "Galassie", "Nebulose", "Astronauti", "Sonde spaziali",
               "Osservatori astronomici"),
    "architettura": ("Ponti", "Grattacieli", "Cattedrali", "Fari", "Torri",
                     "Castelli", "Palazzi", "Teatri", "Stadi", "Gallerie",
                     "Acquedotti", "Mura", "Basiliche", "Moschee", "Templi",
                     "Fontane", "Obelischi", "Mulini"),
    "storia": ("Siti archeologici", "Antico Egitto", "Antica Roma",
               "Civiltà precolombiane", "Musei", "Monumenti", "Piramidi",
               "Antica Grecia", "Vichinghi", "Impero bizantino",
               "Biblioteche", "Archivi", "Necropoli", "Anfiteatri romani"),
    "invenzioni": ("Invenzioni", "Mezzi di trasporto", "Strumenti di misura",
                   "Elettrodomestici", "Giochi da tavolo", "Strumenti musicali",
                   "Utensili"),
    "tradizioni": ("Feste", "Patrimoni dell'umanità", "Danze", "Bevande",
                   "Formaggi", "Dolci", "Piatti nazionali",
                   "Costumi tradizionali", "Carnevali", "Fiere", "Ceramica",
                   "Tessuti", "Artigianato"),
    "lingue": ("Lingue", "Alfabeti", "Sistemi di scrittura",
               "Lingue germaniche", "Lingue slave"),
    "trasporti": ("Automobili", "Locomotive", "Navi", "Aerei", "Biciclette",
                  "Metropolitane", "Porti", "Aeroporti"),
    "sport": ("Sport olimpici", "Giochi", "Arti marziali", "Sport acquatici"),
    "alimentazione": ("Formaggi italiani", "Vini", "Spezie", "Pane",
                      "Salumi", "Pasta", "Bevande analcoliche"),
    "scienza": ("Elementi chimici", "Minerali", "Gemme", "Metalli",
                "Fenomeni ottici", "Unità di misura"),
}

#: Never more than this many claims from a single article, so one long page does
#: not become a week of near-identical posts.
MAX_PER_ARTICLE = 2
#: Nor more than this from a single topic.
MAX_PER_TOPIC = 150

#: A sentence must read as a plain statement of fact to become a claim.
_BAD_SENTENCE = re.compile(
    r"(?i)\b(vedi|cfr|secondo alcuni|si dice|si ritiene|forse|probabilmente|"
    r"sembra che|potrebbe|alcuni autori|la leggenda|questa voce|template|"
    r"disambigua|nota bene)\b")
_HAS_VERB = re.compile(
    r"(?i)\b(è|sono|era|erano|ha|hanno|aveva|avevano|viene|vengono|venne|"
    r"si trova|si trovano|contiene|contengono|misura|misurano|raggiunge|"
    r"raggiungono|ospita|ospitano|produce|producono|copre|coprono|deriva|"
    r"derivano|indica|indicano|comprende|comprendono|costituisce|permette|"
    r"consente|si estende|vive|vivono|appartiene|designa|denota|occupa)\b")
#: Absolutes are dropped rather than checked: the check is unreliable and the
#: claim is perfectly good without them.
_ABSOLUTE = re.compile(
    r"(?i)\b(il più grande del mondo|la più grande del mondo|l\'unico al mondo|"
    r"l\'unica al mondo|il primo in assoluto|mai esistito|il migliore|"
    r"la migliore|il peggiore)\b")
_PRONOUN_START = re.compile(
    r"(?i)^(esso|essa|essi|esse|questo|questa|questi|queste|vi |ne |"
    r"il suo|la sua|i suoi|le sue|tale|tali)\b")


#: Vocabulary that marks a sentence as written for specialists. A claim built
#: out of these is accurate and unreadable, which is the wrong trade for a page
#: a general audience scrolls past in two seconds.
_JARGON = (
    "trascrizione", "eucariot", "procariot", "polimer", "cinetica", "enzim",
    "proteic", "molecolar", "isotop", "cromosom", "genoma", "nucleotid",
    "ossidazione", "catalizz", "stechiometr", "termodinam", "equazione",
    "algoritm", "matrice", "tensore", "integrale", "derivata", "coefficiente",
    "asintot", "topolog", "morfism", "funtore", "ontolog", "epistemolog",
    "sintagma", "fonema", "morfema", "flession", "declinazion",
    "tassonom", "filogene", "sottospecie", "sinonimia", "nomenclatur",
    "parametr", "vettoriale", "scalare", "logaritm",
    # Taxonomy reads as specialist vocabulary to everyone except taxonomists,
    # and Wikipedia's lead sentences for animals and habitats are full of it.
    "clade", "emimetabol", "paurometabol", "olometabol", "anguimorf",
    "sinapomorf", "plesiomorf", "monofiletic", "parafiletic", "taxon",
    "sottordine", "superordine", "infraordine", "sottofamiglia",
    "sottogenere", "cladistic", "ecoregion", "endemism", "fenotip",
    "genotip", "sottospecie", "euteri", "whippomorf", "artyodattil",
    "cetartiodattil", "raggruppamento evolutivo", "ecozona",
)

#: Openings that mean "this sentence is the dictionary definition of the title",
#: which reads as an encyclopedia entry rather than as something worth knowing.
_DEFINITION_RE = re.compile(
    r"(?i)^\s*(?:in|nella|nel)?\s*[a-zàèéìòù\s]{0,24},?\s*"
    r"(?:il|lo|la|i|gli|le|un|uno|una|l')\s+\S+\s+(?:è|sono)\s+"
    r"(?:un|uno|una|il|lo|la|l')\b")


def _is_jargon(text: str) -> bool:
    low = _fold(text)
    return sum(1 for j in _JARGON if j in low) >= 1


def _is_bare_definition(claim: str, title: str) -> bool:
    """Is the sentence just "<title> è un <categoria>"?"""
    low, head = _fold(claim), _fold(title)
    if not head:
        return False
    first = low.split(" e ")[0][:120]
    if head[:14] in first and re.search(r"\b(e|sono)\s+(un|uno|una|il|lo|la|l)\b",
                                        first):
        # A definition that carries a number or a place is still interesting.
        return not re.search(r"\d", claim)
    return False


#: Openings that only make sense after the sentence before them.
_CONTINUATION_RE = re.compile(
    r"(?i)^(è |sono |ha |hanno |con |nella regione|il primo esempio|"
    r"in seguito|inoltre|invece|infatti|tuttavia|anche |oltre a|"
    r"secondo |dal punto di vista|si tratta|viene inoltre|nel caso)")

#: A lead shorter than this usually belongs to a stub, and a stub is a decent
#: proxy for a subject nobody was curious about.
MIN_LEAD_CHARS = 450
#: Only the opening sentences of a lead are self-contained.
MAX_LEAD_SENTENCES = 2


def _mentions_subject(claim: str, title: str) -> bool:
    """Does the claim name what the article is about?

    Guards against sentences that carry their subject in a pronoun three
    sentences earlier. The head word of the title is enough: "Lago Bajkal" ->
    "bajkal", which any sentence genuinely about it will contain.
    """
    head = _fold(title).split("(")[0].strip()
    parts = [w for w in re.findall(r"[a-z]{4,}", head)
             if w not in {"della", "delle", "degli", "dell", "nella"}]
    if not parts:
        return True
    low = _fold(claim)
    return any(w[:6] in low for w in parts)


def candidate_claim(sentence: str, title: str = "") -> str:
    """Turn a lead sentence into a claim that stands on its own, or return ''."""
    clean = tidy(sentence)
    if len(clean) > 175:
        # A long lead sentence usually carries its claim in the first clause;
        # discarding it throws away most of Wikipedia's best material.
        cut = clean[:175]
        for stop in (", ", " e ", " che ", " ed "):
            idx = cut.rfind(stop)
            if idx >= MIN_TEXT + 25:
                cut = cut[:idx]
                break
        clean = cut.rstrip(" ,;:-")
    if not (MIN_TEXT + 15 <= len(clean) <= 175):
        return ""
    if _BAD_SENTENCE.search(clean) or _ABSOLUTE.search(clean):
        return ""
    if not _HAS_VERB.search(clean):
        return ""
    if clean.count(",") > 4 or ";" in clean or "=" in clean:
        return ""
    if _PRONOUN_START.match(clean):
        return ""
    if _is_jargon(clean):
        return ""
    if _CONTINUATION_RE.match(clean):
        return ""
    if title and not _mentions_subject(clean, title):
        return ""
    if not clean.endswith((".", "!", "?")):
        clean += "."
    return clean[:1].upper() + clean[1:]


def rebuild_curiosities(path: Path, *, cache: PageCache, session,
                        checkpoint: Checkpoint, limit: int | None = None,
                        progress=None) -> RebuildOutcome:
    """Replace unverified curiosities with statements taken out of real articles."""
    data = load_dataset(path)
    items = data.get("items") or []
    ctype = data.get("content_type") or ""
    out = RebuildOutcome(dataset=path.name)

    failing = [i for i in items if not is_publishable(i, content_type=ctype).ok]
    out.needed = len(failing)
    out.kept = len(items) - out.needed
    if limit:
        failing = failing[:limit]
    if not failing:
        return out

    # 1 - the article pool, from category listings (checkpointed).
    pool: list[tuple[str, str]] = []
    for topic, categories in CURIOSITY_CATEGORIES.items():
        for category in categories:
            key = f"cat:{category}"
            cached = checkpoint.done(key)
            if cached is None:
                titles = category_members(session, category, limit=260)
                checkpoint.record(key, {"titles": titles})
                checkpoint.save()
            else:
                titles = cached.get("titles", [])
            pool.extend((topic, t) for t in titles)

    seen_titles: set[str] = set()
    unique_pool: list[tuple[str, str]] = []
    for topic, title in pool:
        if title in seen_titles or ":" in title:
            continue
        seen_titles.add(title)
        unique_pool.append((topic, title))
    out.notes.append(f"pool di articoli distinti: {len(unique_pool)}")

    # 2 - extracts, batched twenty at a time.
    articles = fetch_wikipedia_extracts([t for _, t in unique_pool],
                                        session=session, cache=cache, rate=5.0,
                                        intro_only=True, progress=progress)
    cache.save()

    # 3 - build claims, honouring the per-article and per-topic caps.
    used_claims = {claim_hash(i.get("text") or "") for i in items}
    # The very index the final gate uses, seeded with everything already in the
    # dataset: rejecting with a different algorithm than the one that judges is
    # how each pass produced a fresh duplicate for the next pass to find.
    from .dedup import SimilarityIndex
    similarity = SimilarityIndex(fuzzy_threshold=0.88, semantic_threshold=0.82)
    for n, existing in enumerate(items):
        text_existing = existing.get("text") or ""
        if text_existing and not is_publishable(
                existing, content_type=ctype).ok:
            continue                      # the slot being replaced
        similarity.add(n, text_existing)
    per_topic: dict[str, int] = {}
    checked = today_iso()
    slots = iter(failing)
    built = 0

    for topic, title in unique_pool:
        if built >= len(failing):
            break
        if per_topic.get(topic, 0) >= MAX_PER_TOPIC:
            continue
        article = articles.get(title, {})
        if article.get("missing") or not article.get("text"):
            continue
        lead = article["text"].split("\n==", 1)[0]
        if len(lead) < MIN_LEAD_CHARS:
            continue
        from_this = 0
        for sentence in sentences(lead)[:MAX_LEAD_SENTENCES]:
            if built >= len(failing) or from_this >= MAX_PER_ARTICLE:
                break
            claim = candidate_claim(sentence, title)
            if not claim:
                continue
            h = claim_hash(claim)
            if h in used_claims:
                continue
            tokens = content_tokens(claim)
            if len(tokens) < 5:
                continue
            # Against every claim already accepted, not a sliding window:
            # a paraphrase a thousand items away is still a paraphrase.
            duplicate, _match = similarity.is_duplicate(claim)
            if duplicate:
                continue

            try:
                item = next(slots)
            except StopIteration:
                break

            explanation = first_good_sentence(lead.replace(sentence, " "),
                                              min_len=50, max_len=MAX_EXPLANATION)
            if not explanation:
                explanation = f"Dalla voce «{title}» di Wikipedia in italiano."

            item["text"] = claim
            item["category"] = topic
            item["explanation"] = explanation
            item["caption"] = (f"{title} - {explanation} "
                               f"Fonte: Wikipedia in italiano.")
            item["source_name"] = "Wikipedia in italiano"
            item["source_url"] = article_url(title)
            item["secondary_source_url"] = ""
            meta = item.setdefault("metadata", {})
            meta["topic"] = topic
            meta["source_article"] = title

            attach_verification(
                item, method=METHOD_STRUCTURED,
                evidence=f"Voce «{title}» di Wikipedia in italiano: «{claim}»",
                source_url=article_url(title), source_title=title,
                source_strength="general_encyclopedia", checked_at=checked,
                note="affermazione estratta dall'incipit della voce citata")
            item["verification_executor"] = "automated_source_first"

            used_claims.add(h)
            similarity.add(10_000 + built, claim)
            per_topic[topic] = per_topic.get(topic, 0) + 1
            from_this += 1
            built += 1
            out.rebuilt += 1

    save_dataset(path, data)
    out.still_failing = sum(
        1 for i in items if not is_publishable(i, content_type=ctype).ok)
    out.notes.append(f"per argomento: {per_topic}")
    return out


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------
#: Candidate lemmas for the slots that failed. Ordinary, useful Italian words —
#: no coinages, no rarities whose entry would be a guess. Each is probed against
#: Treccani before it can be used; the ones with no real entry are skipped, so
#: this list is deliberately longer than the number of slots.
WORD_CANDIDATES: tuple[tuple[str, str, str, str, str], ...] = (
    # (lemma, part of speech, short paraphrased definition, example, register)
    ("meticoloso", "aggettivo", "Che cura ogni dettaglio con attenzione minuziosa.",
     "Un lavoro meticoloso richiede più tempo ma lascia meno da rifare.", "comune"),
    ("perspicace", "aggettivo", "Che capisce in fretta ciò che agli altri sfugge.",
     "Una domanda perspicace vale più di dieci risposte.", "comune"),
    ("tenace", "aggettivo", "Che non molla la presa né l'intento.",
     "Fu tenace fino a quando la porta si aprì.", "comune"),
    ("effimero", "aggettivo", "Che dura pochissimo.",
     "Il successo effimero si dimentica prima di essere goduto.", "letterario"),
    ("solerte", "aggettivo", "Pronto e diligente nel fare ciò che serve.",
     "Un collaboratore solerte anticipa i problemi.", "letterario"),
    ("arguto", "aggettivo", "Spiritoso e acuto insieme.",
     "Rispose con una battuta arguta che chiuse la discussione.", "comune"),
    ("ostinato", "aggettivo", "Che insiste senza cambiare direzione.",
     "Fu ostinato anche quando i fatti dicevano il contrario.", "comune"),
    ("garbato", "aggettivo", "Cortese nei modi e nel tono.",
     "Un rifiuto garbato resta un rifiuto, ma non offende.", "comune"),
    ("scrupoloso", "aggettivo", "Che teme di sbagliare e controlla tutto.",
     "Fu scrupoloso nel citare ogni fonte.", "comune"),
    ("versatile", "aggettivo", "Capace di adattarsi a compiti molto diversi.",
     "Uno strumento versatile occupa il posto di tre.", "comune"),
    ("incisivo", "aggettivo", "Che colpisce e lascia il segno.",
     "Un discorso incisivo dura tre minuti.", "comune"),
    ("prolisso", "aggettivo", "Che si dilunga più del necessario.",
     "Il resoconto era prolisso e finiva per confondere.", "letterario"),
    ("cauto", "aggettivo", "Che procede con prudenza.",
     "Fu cauto nel promettere e generoso nel mantenere.", "comune"),
    ("lungimirante", "aggettivo", "Che sa prevedere le conseguenze lontane.",
     "Una scelta lungimirante costa oggi e rende domani.", "comune"),
    ("caparbio", "aggettivo", "Ostinato al punto di non ascoltare.",
     "Rimase caparbio anche davanti all'evidenza.", "comune"),

    ("discernimento", "sostantivo maschile",
     "Capacità di distinguere ciò che conta da ciò che non conta.",
     "Serve discernimento per scegliere fra due cose entrambe buone.", "letterario"),
    ("perseveranza", "sostantivo femminile",
     "Costanza nel proseguire nonostante gli ostacoli.",
     "La perseveranza non è testardaggine: cambia metodo, non meta.", "comune"),
    ("prontezza", "sostantivo femminile",
     "Rapidità nel reagire o nel capire.",
     "La prontezza salva più occasioni della forza.", "comune"),
    ("assennatezza", "sostantivo femminile",
     "Qualità di chi giudica con equilibrio.",
     "Rispose con un'assennatezza rara alla sua età.", "letterario"),
    ("misura", "sostantivo femminile",
     "Giusto limite nel comportamento o nel giudizio.",
     "Parlò con misura, e proprio per questo lo ascoltarono.", "comune"),
    ("candore", "sostantivo maschile",
     "Sincerità priva di malizia.",
     "Disse la verità con un candore che spiazzò tutti.", "letterario"),
    ("premura", "sostantivo femminile",
     "Attenzione affettuosa verso qualcuno.",
     "Si occupò di lui con una premura discreta.", "comune"),
    ("costanza", "sostantivo femminile",
     "Fermezza nel mantenere un impegno nel tempo.",
     "La costanza vale più dell'entusiasmo iniziale.", "comune"),
    ("lucidità", "sostantivo femminile",
     "Chiarezza di pensiero, anche in circostanze difficili.",
     "Mantenne la lucidità mentre tutti perdevano la calma.", "comune"),
    ("sobrietà", "sostantivo femminile",
     "Moderazione nei modi, nelle parole o nei consumi.",
     "La sobrietà dell'allestimento faceva risaltare le opere.", "comune"),
    ("prospettiva", "sostantivo femminile",
     "Punto di vista da cui una cosa appare in un certo modo.",
     "Cambiando prospettiva il problema si ridimensionò.", "comune"),
    ("equilibrio", "sostantivo maschile",
     "Rapporto stabile fra forze o esigenze contrapposte.",
     "Trovare un equilibrio richiede più lavoro che scegliere un estremo.", "comune"),
    ("consuetudine", "sostantivo femminile",
     "Abitudine consolidata in un gruppo o in una persona.",
     "Per consuetudine la riunione si apriva senza formalità.", "letterario"),
    ("acume", "sostantivo maschile",
     "Finezza di intuito nel cogliere ciò che è nascosto.",
     "Con acume notò l'unico dettaglio che non tornava.", "letterario"),
    ("indugio", "sostantivo maschile",
     "Ritardo dovuto a esitazione.",
     "Rispose senza indugio e la trattativa si sbloccò.", "letterario"),
    ("congettura", "sostantivo femminile",
     "Ipotesi formulata su indizi incompleti.",
     "Restava una congettura finché non arrivarono i dati.", "letterario"),
    ("rammarico", "sostantivo maschile",
     "Dispiacere per qualcosa che è andato diversamente.",
     "Parlò del progetto chiuso con un rammarico sincero.", "comune"),
    ("proposito", "sostantivo maschile",
     "Intenzione formulata con decisione.",
     "Prese il proposito di rispondere sempre entro un giorno.", "comune"),
    ("riguardo", "sostantivo maschile",
     "Attenzione rispettosa verso qualcuno o qualcosa.",
     "Trattò l'obiezione con riguardo, senza liquidarla.", "comune"),
    ("solitudine", "sostantivo femminile",
     "Condizione di chi si trova senza compagnia.",
     "Cercava una solitudine breve, non un isolamento.", "comune"),
    ("chiarezza", "sostantivo femminile",
     "Qualità di ciò che si capisce senza sforzo.",
     "La chiarezza costa fatica a chi scrive e la risparmia a chi legge.", "comune"),

    ("soppesare", "verbo transitivo",
     "Valutare con attenzione prima di decidere.",
     "Soppesò le due offerte per un giorno intero.", "comune"),
    ("ponderare", "verbo transitivo",
     "Esaminare a fondo tutti gli aspetti di una scelta.",
     "Ponderò la proposta senza lasciarsi mettere fretta.", "comune"),
    ("dirimere", "verbo transitivo",
     "Risolvere in modo definitivo una controversia.",
     "Una sola prova bastò a dirimere la questione.", "letterario"),
    ("ravvedersi", "verbo riflessivo",
     "Riconoscere il proprio errore e correggersi.",
     "Si ravvide prima che il danno diventasse irreparabile.", "letterario"),
    ("indugiare", "verbo intransitivo",
     "Trattenersi o esitare più del necessario.",
     "Indugiò sulla soglia come se dovesse ancora decidere.", "letterario"),
    ("assecondare", "verbo transitivo",
     "Andare nella direzione voluta da qualcun altro.",
     "Assecondare ogni richiesta non è gentilezza.", "comune"),
    ("declinare", "verbo transitivo",
     "Rifiutare qualcosa con cortesia.",
     "Declinò l'invito ringraziando.", "comune"),
    ("stemperare", "verbo transitivo",
     "Ridurre l'intensità di qualcosa.",
     "Una battuta stemperò la tensione della riunione.", "comune"),
    ("suffragare", "verbo transitivo",
     "Sostenere un'affermazione con prove.",
     "I dati suffragarono l'ipotesi iniziale.", "letterario"),
    ("annoverare", "verbo transitivo",
     "Includere in un elenco o in un gruppo.",
     "La città annovera tre biblioteche pubbliche.", "letterario"),
    ("perorare", "verbo transitivo",
     "Sostenere una causa con argomenti.",
     "Perorò la proposta davanti a un pubblico scettico.", "letterario"),
    ("rifuggire", "verbo intransitivo",
     "Evitare per istinto o per principio.",
     "Rifuggiva dalle promesse che non poteva mantenere.", "letterario"),
    ("desistere", "verbo intransitivo",
     "Smettere di insistere in un tentativo.",
     "Non desistette finché non trovò la risposta.", "letterario"),
    ("prevalere", "verbo intransitivo",
     "Imporsi rispetto ad altri o ad altro.",
     "Alla fine prevalse la soluzione più semplice.", "comune"),
    ("attenersi", "verbo riflessivo",
     "Restare fedele a una regola o a un'indicazione.",
     "Si attenne al programma anche quando fu scomodo.", "comune"),
    ("prescindere", "verbo intransitivo",
     "Non tenere conto di qualcosa nel ragionamento.",
     "A prescindere dal risultato, il metodo era corretto.", "comune"),
    ("sopperire", "verbo intransitivo",
     "Rimediare a una mancanza.",
     "L'esperienza sopperì alla scarsità di mezzi.", "letterario"),
    ("ottemperare", "verbo intransitivo",
     "Adempiere a un obbligo o a una disposizione.",
     "L'ufficio ottemperò alla richiesta entro i termini.", "letterario"),
    ("propendere", "verbo intransitivo",
     "Essere inclinato verso una possibilità.",
     "Propendeva per la seconda ipotesi senza escludere la prima.", "comune"),
    ("ricusare", "verbo transitivo",
     "Rifiutare formalmente qualcosa.",
     "Ricusò l'incarico per evidente conflitto di interessi.", "letterario"),
    ("corroborare", "verbo transitivo",
     "Rafforzare qualcosa con nuovi elementi.",
     "Le misure successive corroborarono la prima stima.", "letterario"),
    ("disattendere", "verbo transitivo",
     "Non rispettare un impegno o un'aspettativa.",
     "Il progetto disattese le previsioni più ottimistiche.", "letterario"),
    ("subentrare", "verbo intransitivo",
     "Prendere il posto di qualcuno o di qualcosa.",
     "Alla stanchezza subentrò una calma inattesa.", "comune"),
    ("scaturire", "verbo intransitivo",
     "Nascere o derivare da qualcosa.",
     "Dalla discussione scaturì una proposta migliore.", "letterario"),
    ("consolidare", "verbo transitivo",
     "Rendere più stabile e duraturo.",
     "Ripetere consolida più che studiare a lungo una volta sola.", "comune"),
    ('assiduo', 'aggettivo',
     'Che si dedica a qualcosa con regolarità costante.',
     "Fu un lettore assiduo di quella rivista per vent'anni.", 'comune'),
    ('morigerato', 'aggettivo',
     'Moderato e misurato nei costumi.',
     'Conduceva una vita morigerata e senza eccessi.', 'letterario'),
    ('affabile', 'aggettivo',
     'Che si rivolge agli altri con cordialità.',
     'Un direttore affabile accorcia le distanze.', 'comune'),
    ('recondito', 'aggettivo',
     'Nascosto e difficile da raggiungere.',
     'Cercava il motivo recondito di quella scelta.', 'letterario'),
    ('esiguo', 'aggettivo',
     'Molto scarso in quantità.',
     'Il margine era esiguo ma sufficiente.', 'letterario'),
    ('cospicuo', 'aggettivo',
     'Notevole per quantità o importanza.',
     'Ricevette un lascito cospicuo e lo destinò alla scuola.', 'letterario'),
    ('proficuo', 'aggettivo',
     'Che porta un vantaggio concreto.',
     'Fu un confronto proficuo per entrambe le parti.', 'comune'),
    ('verosimile', 'aggettivo',
     'Che sembra vero pur non essendo dimostrato.',
     "La ricostruzione era verosimile ma restava un'ipotesi.", 'comune'),
    ('inderogabile', 'aggettivo',
     'Che non ammette rinvii né eccezioni.',
     'Il termine era inderogabile e nessuno chiese proroghe.', 'comune'),
    ('meticolosità', 'sostantivo femminile',
     'Cura minuziosa di ogni particolare.',
     'La meticolosità del restauro si nota solo da vicino.', 'comune'),
    ('parsimonia', 'sostantivo femminile',
     "Moderazione nello spendere o nell'usare qualcosa.",
     'Usava gli aggettivi con parsimonia.', 'letterario'),
    ('dovizia', 'sostantivo femminile',
     'Grande abbondanza.',
     'Spiegò il metodo con dovizia di esempi.', 'letterario'),
    ('ritrosia', 'sostantivo femminile',
     'Riluttanza timida a farsi avanti.',
     'Vinse la ritrosia e prese la parola per primo.', 'letterario'),
    ('alacrità', 'sostantivo femminile',
     'Prontezza vivace nel lavorare.',
     "Si mise all'opera con un'alacrità contagiosa.", 'letterario'),
    ('pacatezza', 'sostantivo femminile',
     'Calma nel tono e nei modi.',
     "Rispose con una pacatezza che disarmò l'interlocutore.", 'comune'),
    ('tempismo', 'sostantivo maschile',
     'Capacità di agire nel momento giusto.',
     "Il tempismo conta più della forza dell'argomento.", 'comune'),
    ('intraprendenza', 'sostantivo femminile',
     'Disposizione a cominciare e a rischiare.',
     "L'intraprendenza da sola non basta: serve costanza.", 'comune'),
    ('puntiglio', 'sostantivo maschile',
     'Ostinazione per una questione di principio.',
     'Difese quel dettaglio per puro puntiglio.', 'comune'),
    ('cautela', 'sostantivo femminile',
     'Prudenza nel procedere.',
     'Trattò la notizia con la cautela che meritava.', 'comune'),
    ('verve', 'sostantivo femminile',
     "Brio vivace nell'esprimersi.",
     "Raccontò l'episodio con una verve inattesa.", 'letterario'),
    ('acribia', 'sostantivo femminile',
     'Rigore scrupoloso nella ricerca.',
     'Ricostruì le fonti con acribia da archivista.', 'letterario'),
    ('aplomb', 'sostantivo maschile',
     'Padronanza di sé nelle situazioni difficili.',
     "Mantenne l'aplomb anche davanti alle critiche.", 'letterario'),
    ('epilogo', 'sostantivo maschile',
     'Conclusione di una vicenda.',
     "L'epilogo fu meno drammatico di quanto si temesse.", 'comune'),
    ('preambolo', 'sostantivo maschile',
     'Discorso introduttivo che precede la sostanza.',
     'Entrò nel merito senza preamboli.', 'comune'),
    ('corollario', 'sostantivo maschile',
     'Conseguenza che discende da una cosa già stabilita.',
     'La seconda regola è un corollario della prima.', 'letterario'),
    ('baluardo', 'sostantivo maschile',
     'Difesa salda contro qualcosa.',
     'Quella norma resta un baluardo contro gli abusi.', 'letterario'),
    ('crocevia', 'sostantivo maschile',
     'Punto in cui si incontrano più direzioni.',
     'La città fu per secoli un crocevia di lingue.', 'comune'),
    ('connubio', 'sostantivo maschile',
     'Unione stretta fra due elementi diversi.',
     'Un connubio riuscito fra rigore e leggerezza.', 'letterario'),
    ('appiglio', 'sostantivo maschile',
     'Punto a cui aggrapparsi, anche in senso figurato.',
     'Cercava un appiglio per riaprire la discussione.', 'comune'),
    ('guizzo', 'sostantivo maschile',
     'Movimento rapido e improvviso.',
     'Ebbe un guizzo di intuizione a metà riunione.', 'comune'),
    ('scampolo', 'sostantivo maschile',
     'Piccola parte residua di qualcosa.',
     'Approfittò di uno scampolo di tempo libero.', 'comune'),
    ('appannaggio', 'sostantivo maschile',
     'Ciò che spetta o è riservato a qualcuno.',
     'Quella competenza non è appannaggio dei soli esperti.', 'letterario'),
    ('vaglio', 'sostantivo maschile',
     'Esame selettivo di ciò che vale e ciò che no.',
     'Passò al vaglio ogni riga del contratto.', 'letterario'),
    ('compendio', 'sostantivo maschile',
     "Esposizione breve che raccoglie l'essenziale.",
     'Ne fece un compendio di due pagine.', 'letterario'),
    ('cimento', 'sostantivo maschile',
     'Prova impegnativa a cui ci si sottopone.',
     'Affrontò il cimento senza cercare scorciatoie.', 'letterario'),
    ('dimestichezza', 'sostantivo femminile',
     'Familiarità pratica con qualcosa.',
     'Aveva dimestichezza con gli strumenti di misura.', 'comune'),
    ('disamina', 'sostantivo femminile',
     'Esame condotto punto per punto.',
     "La disamina occupò l'intera mattinata.", 'letterario'),
    ('annoverare', 'verbo transitivo',
     'Includere in un insieme o in un elenco.',
     'La collezione annovera tre incunaboli.', 'letterario'),
    ('vagliare', 'verbo transitivo',
     'Esaminare per scegliere ciò che serve.',
     'Vagliò le candidature una per una.', 'comune'),
    ('appurare', 'verbo transitivo',
     'Accertare come stanno davvero le cose.',
     'Appurò la data prima di pubblicarla.', 'comune'),
    ('esulare', 'verbo intransitivo',
     "Restare fuori dall'ambito di cui si parla.",
     "La questione esula dai compiti dell'ufficio.", 'letterario'),
    ('addurre', 'verbo transitivo',
     'Portare una ragione a sostegno di qualcosa.',
     'Addusse motivi che nessuno aveva considerato.', 'letterario'),
    ('avvalersi', 'verbo riflessivo',
     'Servirsi di qualcosa che si ha diritto di usare.',
     'Si avvalse della facoltà di rispondere per iscritto.', 'comune'),
    ('demandare', 'verbo transitivo',
     'Affidare a un altro un compito o una decisione.',
     'Demandò la scelta a chi conosceva il dossier.', 'letterario'),
    ('procrastinare', 'verbo transitivo',
     'Rimandare a un momento successivo.',
     'Procrastinare una decisione è già una decisione.', 'comune'),
    ('dipanare', 'verbo transitivo',
     'Sciogliere qualcosa di intricato.',
     'Dipanò la vicenda partendo dai documenti.', 'letterario'),
    ('perlustrare', 'verbo transitivo',
     'Esaminare un luogo con attenzione.',
     "Perlustrarono l'archivio scaffale per scaffale.", 'comune'),
    ('rimarcare', 'verbo transitivo',
     'Mettere in evidenza qualcosa.',
     'Rimarcò che il dato andava aggiornato.', 'comune'),
    ('ravvisare', 'verbo transitivo',
     'Riconoscere la presenza di qualcosa.',
     'Non ravvisò alcun motivo per intervenire.', 'letterario'),
    ('desumere', 'verbo transitivo',
     'Ricavare una conclusione da ciò che si osserva.',
     'Si desume dai registri che la fiera durava tre giorni.', 'letterario'),
    ('comprovare', 'verbo transitivo',
     'Confermare con prove.',
     'I rilievi comprovarono la ricostruzione iniziale.', 'comune'),
    ('esperire', 'verbo transitivo',
     'Mettere in atto un tentativo o una procedura.',
     'Furono esperiti tutti i tentativi di accordo.', 'letterario'),
    ('addivenire', 'verbo intransitivo',
     'Arrivare a un risultato dopo un percorso.',
     "Le parti addivennero a un'intesa in serata.", 'letterario'),
    ('soggiacere', 'verbo intransitivo',
     'Essere sottoposto a una regola o a una forza.',
     'Ogni testo soggiace alle stesse verifiche.', 'letterario'),
)



def _slug_variants(lemma: str) -> list[str]:
    """Every Treccani slug this lemma could plausibly live under, best first."""
    base = lemma.strip().lower()
    plain = _fold(base)
    out = [base, plain]
    for suffix, replacement in (("arsi", "are"), ("ersi", "ere"), ("irsi", "ire")):
        if plain.endswith(suffix):
            out.append(plain[: -len(suffix)] + replacement)
    out.extend(f"{plain}{n}" for n in (1, 2))
    seen: list[str] = []
    for s in out:
        if s and s not in seen:
            seen.append(s)
    return seen


def rebuild_words(path: Path, *, cache: PageCache, session,
                  checkpoint: Checkpoint, limit: int | None = None,
                  progress=None) -> RebuildOutcome:
    """Replace failed lemmas with words whose dictionary entry really exists."""
    from .evidence import fetch_page, verify_lemma

    data = load_dataset(path)
    items = data.get("items") or []
    ctype = data.get("content_type") or ""
    out = RebuildOutcome(dataset=path.name)

    failing = [i for i in items if not is_publishable(i, content_type=ctype).ok]
    out.needed = len(failing)
    out.kept = len(items) - out.needed
    if limit:
        failing = failing[:limit]
    if not failing:
        return out

    present = {_fold((i.get("text") or "").strip()) for i in items}
    rate_state: dict = {}
    checked = today_iso()
    slots = iter(failing)
    built = 0

    for lemma, pos, definition, example, register in WORD_CANDIDATES:
        if built >= len(failing):
            break
        if _fold(lemma) in present:
            continue
        # Treccani drops accents from its slugs (lucidità -> lucidita), files
        # homographs under a numeric suffix, and keeps some pronominal verbs
        # under the base form and others under the pronominal one. Probe the
        # variants rather than guess which convention applies to this lemma.
        ev = None
        url = ""
        page = {}
        for slug in _slug_variants(lemma):
            candidate = f"https://www.treccani.it/vocabolario/{slug}/"
            page = fetch_page(candidate, session=session, cache=cache,
                              rate_state=rate_state, rate=3.0)
            ev = verify_lemma(lemma, pos, definition, page)
            if ev.supported:
                url = candidate
                break
        if not ev.supported:
            out.notes.append(f"{lemma}: {ev.reason if ev else 'nessuna variante'}")
            continue

        try:
            item = next(slots)
        except StopIteration:
            break

        item["text"] = lemma
        item["category"] = register
        item["explanation"] = definition
        item["caption"] = (f"{lemma} - {definition} Esempio: «{example}» "
                           f"Fonte: Treccani - Vocabolario della lingua italiana.")
        item["source_name"] = "Treccani - Vocabolario della lingua italiana"
        item["source_url"] = page.get("final_url") or url
        item["secondary_source_url"] = ""
        meta = item.setdefault("metadata", {})
        meta["part_of_speech"] = (ev.extra or {}).get("declared_pos") or pos
        meta["definition"] = definition
        meta["example"] = example
        meta["register"] = register
        meta["etymology"] = None

        attach_verification(
            item, method=METHOD_AUTHORITATIVE, evidence=ev.passage,
            source_url=item["source_url"],
            source_title=ev.source_title or page.get("title") or lemma,
            source_strength="authoritative_reference", checked_at=checked,
            note="voce di vocabolario: lemma e categoria grammaticale confermati")
        item["verification_executor"] = "automated_source_first"
        present.add(_fold(lemma))
        built += 1
        out.rebuilt += 1
        if progress and built % 10 == 0:
            progress(built, len(failing))

    cache.save()
    save_dataset(path, data)
    out.still_failing = sum(
        1 for i in items if not is_publishable(i, content_type=ctype).ok)
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
BUILDERS = {
    "today_in_history": rebuild_history,
    "world_curiosity": rebuild_curiosities,
    "word_of_the_day": rebuild_words,
}


def rebuild_dataset(path: Path, *, cache_dir: Path, limit: int | None = None,
                    progress=None) -> RebuildOutcome:
    data = load_dataset(path)
    ctype = data.get("content_type") or ""
    builder = BUILDERS.get(ctype)
    if builder is None:
        return RebuildOutcome(dataset=path.name, notes=["nessun builder"])
    cache = PageCache(Path(cache_dir) / f"evidence_{path.stem}.json")
    checkpoint = Checkpoint(Path(cache_dir) / f"rebuild_{path.stem}.json")
    session = build_session()
    try:
        return builder(path, cache=cache, session=session,
                       checkpoint=checkpoint, limit=limit, progress=progress)
    finally:
        cache.save()
        checkpoint.save()
