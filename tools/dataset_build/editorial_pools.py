"""Call-to-action, background-prompt and mood pools, one voice per page.

The first release rotated through five or six calls to action per dataset,
assigned by ``sequence_index % len(pool)``. That means the identical closing
line every five days, forever — the single loudest "this account is a script"
signal a reader gets, and it was there from day six.

Two things changed:

* the pools are large enough that nothing repeats inside a month (a full 30-day
  window sees 30 distinct calls to action);
* each page has its own register, instead of five variations on "Lo sapevi?".

Assignment stays a pure function of ``sequence_index`` — deterministic, no
randomness — but uses a **stride coprime with the pool size** so that
consecutive days walk the pool instead of marching through it in order. Two
posts that share a call to action are then at least ``len(pool)`` days apart and
their neighbours differ too.

``leggo tutto`` and equivalents were removed on purpose: the pages run
unattended, and promising to read every comment is a promise nobody keeps.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Pensiero Essenziale — sober. A thought does not need a prompt to react, and
# roughly a third of the days deliberately carry no call to action at all.
# ---------------------------------------------------------------------------
CTA_PHILOSOPHY: tuple[str, ...] = (
    "", "", "", "", "", "", "", "", "", "", "", "",
    "Su cosa ti fa tornare questo pensiero?",
    "Ti ci riconosci?",
    "Quando te ne sei accorto?",
    "Vale anche al contrario?",
    "A chi lo faresti leggere?",
    "Se non sei d'accordo, dove si rompe?",
    "Rileggilo domani e vedi se cambia.",
    "Un esempio ti è venuto in mente subito?",
    "C'è una parola che toglieresti?",
    "Funziona anche fuori dai casi facili?",
    "Ti sembra vero o solo ben detto?",
    "Che cosa costa, in pratica?",
    "Lo diresti a te stesso di dieci anni fa?",
    "Dove l'hai visto succedere?",
    "Che cosa cambia se lo prendi sul serio?",
    "Quanto dura, secondo te?",
    "È una scusa o una descrizione?",
    "Salvalo se ti serve più tardi.",
    "Ti convince fino in fondo?",
    "Qual è l'eccezione che ti viene in mente?",
)

# ---------------------------------------------------------------------------
# Curiosità dal Mondo — invites saving, sharing or a relevant experience.
# No "tag three friends", no urgency, no fake scarcity.
# ---------------------------------------------------------------------------
CTA_CURIOSITY: tuple[str, ...] = (
    "Lo sapevi?",
    "Ne avevi mai sentito parlare?",
    "Salvalo, torna utile.",
    "Conosci un caso simile?",
    "La fonte è nel testo: verifica pure.",
    "L'hai visto di persona?",
    "Da dove lo stai leggendo?",
    "Sapresti spiegarlo a qualcuno stasera?",
    "Ti aspettavi un numero così?",
    "C'è qualcosa di simile dalle tue parti?",
    "Mandalo a chi ci passerebbe un'ora.",
    "Quale parte ti ha sorpreso di più?",
    "Lo avresti indovinato?",
    "Se ne sai di più, aggiungi pure.",
    "Salvalo per la prossima cena fra amici.",
    "Ci saresti mai andato?",
    "Un dettaglio che non ti aspettavi?",
    "Immaginavi questo ordine di grandezza?",
    "Hai un esempio più vicino a casa?",
    "Vale la pena approfondire?",
    "Ti torna, come spiegazione?",
    "Lo trovi più strano o più logico?",
    "Ne conosci una versione diversa?",
    "Ti è mai capitato di notarlo?",
    "Salvalo se vuoi ricontrollarlo con calma.",
    "Che cosa te lo ricorda?",
    "Quanto ci saresti andato vicino?",
    "L'avresti detto?",
    "Meriterebbe un post a parte?",
    "Se hai una fonte migliore, segnalala.",
    "C'è un motivo semplice dietro?",
    "Lo useresti come esempio?",
)

# ---------------------------------------------------------------------------
# Parola del Giorno — asks to use the word, or whether it was already known.
# ---------------------------------------------------------------------------
CTA_WORD: tuple[str, ...] = (
    "La conoscevi già?",
    "Quando la useresti?",
    "Provala oggi, almeno una volta.",
    "Scrivi una frase con questa parola.",
    "Ti suona antiquata o attuale?",
    "L'avevi mai sentita dire?",
    "Qual è il sinonimo che useresti di solito?",
    "In che contesto ti verrebbe naturale?",
    "Te la ricorderai domani?",
    "Ti piace come suona?",
    "La useresti parlando o solo scrivendo?",
    "Conosci una parola vicina ma non uguale?",
    "C'è un dialetto che ne ha una migliore?",
    "La diresti a voce alta?",
    "Sapevi che cosa significava di preciso?",
    "Qual è il contrario, secondo te?",
    "Ti è capitato di leggerla di recente?",
    "Ti serve una parola così ogni tanto?",
    "Salvala se vuoi riprenderla.",
    "Che immagine ti mette in testa?",
    "Ti sembra utile o solo bella?",
    "Dove l'hai incontrata la prima volta?",
    "Prova a spiegarla senza usarla.",
    "Regalala a qualcuno oggi.",
    "Quale sfumatura aggiunge rispetto al sinonimo?",
    "La useresti in una mail di lavoro?",
    "Ti sembra troppo ricercata?",
    "Che cosa ti fa venire in mente?",
    "Un esempio tuo?",
    "La conoscevi con un altro significato?",
    "Ti convince come definizione?",
    "Vale la pena tenerla in circolazione?",
)

# ---------------------------------------------------------------------------
# Oggi nella Storia — asks which consequence matters, without repeating one
# stock question.
# ---------------------------------------------------------------------------
CTA_HISTORY: tuple[str, ...] = (
    "Lo ricordavi?",
    "Che effetto ti fa, riletto oggi?",
    "Salvalo per ricordartelo.",
    "Sapevi come andò a finire?",
    "La fonte è nel testo: approfondisci pure.",
    "Quale conseguenza pesa ancora?",
    "Sarebbe successo comunque, secondo te?",
    "Che cosa se ne ricorda oggi?",
    "Ti era stato raccontato così?",
    "Quanto ci ha messo a cambiare qualcosa?",
    "Chi ci ha guadagnato davvero?",
    "Lo avevi studiato a scuola?",
    "Cambia qualcosa saperne la data esatta?",
    "Che cosa è venuto subito dopo?",
    "Sembra vicino o lontanissimo?",
    "Quale dettaglio non ti aspettavi?",
    "Ne conosci una versione diversa?",
    "Che cosa ne resta, materialmente?",
    "È stato un inizio o una fine?",
    "Quanto era prevedibile?",
    "Chi lo aveva capito per primo?",
    "Ti sembra sopravvalutato o dimenticato?",
    "Che cosa avrebbe cambiato un anno in più?",
    "Lo racconteresti così anche tu?",
    "Vale la pena approfondirlo?",
    "Ha ancora effetti visibili?",
    "Che cosa ci dice di quell'epoca?",
    "Ti torna, questa ricostruzione?",
    "Meriterebbe più spazio?",
    "A chi lo faresti leggere?",
    "Che cosa ne penserebbe chi c'era?",
    "Quale altra data gli metteresti accanto?",
)

# ---------------------------------------------------------------------------
# Una Domanda al Giorno — varied and natural. Never promises to read everything.
# ---------------------------------------------------------------------------
CTA_QUESTION: tuple[str, ...] = (
    "", "", "", "",
    "Rispondi come ti viene.",
    "Scrivi la prima cosa che ti è venuta in mente.",
    "Basta una riga.",
    "Non serve una risposta definitiva.",
    "Anche un forse è una risposta.",
    "Se ti va, raccontala.",
    "Rispondi domani, se oggi non lo sai.",
    "Provaci senza pensarci troppo.",
    "Una parola sola va benissimo.",
    "Curioso di leggere risposte diverse dalla mia.",
    "Chiedilo anche a qualcun altro.",
    "Vale anche una risposta scomoda.",
    "Se cambia idea a metà, scrivi entrambe.",
    "Tienila per te, se preferisce.",
    "Rileggila fra un mese.",
    "Prova a rispondere senza spiegare.",
    "Se non ti viene niente, va bene lo stesso.",
    "Scrivila prima di razionalizzarla.",
    "Salvala e rispondi con calma.",
    "Anche 'non lo so' dice qualcosa.",
    "Portala a cena stasera.",
    "Rispondi per la persona che eri.",
    "C'è una risposta che non diresti ad alta voce?",
    "Metti un numero, se è più facile.",
    "Sceglila per qualcun altro.",
    "Rispondi con un esempio, non con una regola.",
    "Fa più effetto la domanda o la risposta?",
)

CTA_POOLS: dict[str, tuple[str, ...]] = {
    "philosophical_thought": CTA_PHILOSOPHY,
    "world_curiosity": CTA_CURIOSITY,
    "word_of_the_day": CTA_WORD,
    "today_in_history": CTA_HISTORY,
    "daily_question": CTA_QUESTION,
}


# ---------------------------------------------------------------------------
# Background prompts. Same problem, same fix: five prompts meant the same image
# family every five days.
# ---------------------------------------------------------------------------
PROMPT_PHILOSOPHY: tuple[str, ...] = (
    "warm minimal abstract background, soft beige gradient, gentle grain",
    "muted sand-coloured haze, soft light from the upper left, empty centre",
    "warm off-white paper texture, very soft shadow, calm empty middle",
    "soft amber gradient, blurred horizon line low in the frame",
    "pale terracotta wash, subtle vignette, quiet uncluttered centre",
    "warm grey mist, faint diagonal light, minimal composition",
    "soft cream background with a barely visible fold, matte finish",
    "dusty rose to sand gradient, gentle film grain, empty middle band",
    "warm neutral backdrop, out-of-focus soft light, restrained palette",
    "faded ochre wash, soft edges, generous negative space",
    "pale clay surface, diffuse morning light, minimal texture",
    "soft linen tone, very low contrast, quiet centre",
    "warm ivory gradient with a soft glow near the top",
    "muted apricot haze, blurred, no objects, calm",
    "soft brown paper tone, gentle grain, understated",
    "warm neutral fog, low contrast, wide empty area",
    "pale sepia gradient, soft focus, contemplative",
    "matte sand surface, faint diagonal shadow, spare",
    "warm dusk light on a plain surface, no detail",
    "soft wheat-coloured background, subtle noise, tranquil",
    "cream and taupe blend, blurred, softly lit",
    "faint golden haze, empty composition, gentle",
    "muted parchment tone, very soft gradient, still",
    "warm stone surface, diffuse light, restrained",
)

PROMPT_CURIOSITY: tuple[str, ...] = (
    "deep teal abstract background, soft ocean-like gradient, evocative",
    "dark indigo haze with faint luminous depth, no objects",
    "deep blue-green gradient, subtle texture, calm centre",
    "midnight blue with a faint distant glow near the horizon",
    "abstract dark cyan depth, soft grain, no recognisable shapes",
    "deep petrol blue wash, gentle vignette, quiet middle",
    "dark slate blue gradient, soft mist, evocative but abstract",
    "deep sea-green haze, low light, generous empty space",
    "navy to teal blend, soft focus, atmospheric",
    "dark turquoise depth, faint light from above, no detail",
    "abstract dark blue fog, muted, spacious",
    "deep ocean gradient, very soft luminance variation",
    "cold dark green-blue wash, subtle grain, empty centre",
    "dark cerulean haze, blurred, contemplative",
    "deep marine blue, faint upper glow, unadorned",
    "abstract twilight blue, soft edges, wide clear area",
    "dark aquamarine gradient, low contrast, atmospheric",
    "deep blue mist with faint depth cues, no objects",
    "dark teal surface, diffuse light, restrained",
    "abstract cold blue depth, soft noise, calm",
    "deep steel blue wash, gentle gradient, spare",
    "dark green-teal haze, soft focus, evocative",
    "midnight teal with a barely visible horizon",
    "abstract deep blue field, uniform and quiet",
)

PROMPT_WORD: tuple[str, ...] = (
    "aged paper texture, warm editorial tone, soft even light",
    "old book page texture, cream tone, very soft shadow",
    "parchment surface, faint fibres, calm and even",
    "warm beige paper with a subtle deckled feel",
    "vintage editorial paper, soft grain, uncluttered",
    "ivory page texture, gentle warm light from the left",
    "aged cream paper, faint foxing at the edges only",
    "matte writing paper, warm tone, generous empty centre",
    "soft antique paper wash, low contrast, quiet",
    "warm linen paper texture, diffuse light",
    "pale straw-coloured page, subtle fibre detail",
    "old dictionary page tone, warm, no printed marks",
    "smooth cream card stock, soft even illumination",
    "warm buff paper, faint texture, restrained",
    "aged ivory surface, gentle vignette",
    "soft parchment gradient, warm and still",
    "warm oatmeal paper tone, minimal texture",
    "vintage cream page, very soft top light",
    "faded manuscript paper without any writing",
    "warm sand-coloured page, plain and calm",
    "soft bone-white paper, faint grain",
    "aged editorial stock, warm tone, empty centre",
    "pale honey paper wash, quiet and even",
    "warm neutral page texture, understated",
)

PROMPT_HISTORY: tuple[str, ...] = (
    "archival sepia haze, aged tone, evocative and abstract",
    "faded brown gradient, old photographic grain, no subject",
    "warm sepia mist, soft vignette, empty middle",
    "aged amber wash, dust-like grain, atmospheric",
    "antique brown tone with soft light falloff",
    "faded ochre haze, archival feel, no recognisable object",
    "warm umber gradient, gentle scratches suggestion, abstract",
    "old-photograph brown, soft focus, quiet centre",
    "sepia fog with faint depth, unadorned",
    "aged tobacco tone, soft grain, spacious",
    "faded chestnut wash, archival mood, abstract",
    "warm brown-grey haze, soft top light",
    "antique bistre gradient, dusty texture",
    "aged copper tone, very soft contrast",
    "faded walnut wash, atmospheric and empty",
    "old-print brown, gentle vignette, restrained",
    "warm sepia depth, no shapes, contemplative",
    "aged russet haze, soft grain, calm",
    "faded coffee tone, archival, uncluttered",
    "warm dust-brown gradient, soft edges",
    "antique amber fog, low contrast",
    "aged clay-brown wash, quiet middle band",
    "faded bronze tone, soft archival grain",
    "warm brown mist, spare and evocative",
)

PROMPT_QUESTION: tuple[str, ...] = (
    "very dark minimal background, near-black gradient, soft centre glow",
    "deep charcoal wash, barely visible light from above",
    "near-black surface with a faint soft halo in the middle",
    "dark graphite gradient, extremely low contrast",
    "black-blue depth, minimal, generous empty space",
    "very dark grey haze, soft and quiet",
    "deep charcoal fog, faint centre luminance",
    "near-black matte surface, subtle grain",
    "dark slate wash, soft top-down light",
    "black with a faint warm glow low in the frame",
    "deep ink-coloured gradient, uncluttered",
    "very dark neutral field, soft vignette",
    "charcoal to black blend, calm and spare",
    "dark ash-grey haze, minimal texture",
    "near-black depth with a soft diagonal light",
    "deep soot tone, faint centre brightening",
    "dark basalt wash, low contrast, still",
    "black-grey gradient, soft focus, empty",
    "very dark background with a whisper of blue",
    "deep shadow field, gentle grain, quiet",
    "near-black smooth surface, restrained",
    "dark obsidian tone, faint upper glow",
    "charcoal mist, soft edges, contemplative",
    "very dark minimal field, uniform and calm",
)

PROMPT_POOLS: dict[str, tuple[str, ...]] = {
    "philosophical_thought": PROMPT_PHILOSOPHY,
    "world_curiosity": PROMPT_CURIOSITY,
    "word_of_the_day": PROMPT_WORD,
    "today_in_history": PROMPT_HISTORY,
    "daily_question": PROMPT_QUESTION,
}

#: Moods, per type. ``today_in_history`` and ``word_of_the_day`` shipped with a
#: single mood for all 1.000 items, which flattens both the music selection and
#: the background palette.
MOOD_POOLS: dict[str, tuple[str, ...]] = {
    "today_in_history": ("reflective", "focused", "resilient", "serene",
                         "determined", "grateful", "courageous"),
    "word_of_the_day": ("reflective", "calm", "serene", "focused",
                        "tender", "grateful"),
}

#: Mood is a *presentation* attribute: it picks the background palette and the
#: music category, not a claim about the content. `world_curiosities` mapped
#: each category to exactly one mood, so three categories covering 46% of the
#: dataset all carried ``focused`` and no reordering could break the run. Each
#: category now draws from a small set of moods that suit it.
CATEGORY_MOOD_POOLS: dict[str, dict[str, tuple[str, ...]]] = {
    "world_curiosity": {
        "geografia": ("calm", "serene", "focused"),
        "natura": ("serene", "calm", "grateful"),
        "scienza": ("focused", "inspirational", "reflective"),
        "animali": ("calm", "tender", "serene"),
        "lingue": ("reflective", "focused", "tender"),
        "architettura": ("focused", "grateful", "inspirational"),
        "tradizioni": ("grateful", "tender", "serene"),
        "invenzioni": ("inspirational", "determined", "focused"),
        "spazio": ("inspirational", "reflective", "courageous"),
        "storia": ("reflective", "resilient", "grateful"),
    },
}

#: Rotating hashtags per page, appended to the item's own topic tags. Mirrors
#: ``EXTRA_TAGS`` in the individual builders, kept here so the in-place refresh
#: and the builders cannot drift apart.
EXTRA_TAGS: dict[str, tuple[str, ...]] = {
    "philosophical_thought": (
        "#filosofia", "#riflessioni", "#pensieri", "#consapevolezza",
        "#lentezza", "#introspezione", "#silenzio", "#quotidiano",
        "#pensierodelgiorno", "#pausa"),
    "daily_question": (
        "#unadomandaalgiorno", "#domandadelgiorno", "#riflessioni", "#pensieri",
        "#conversazioni", "#introspezione", "#confronto", "#parliamone",
        "#dimmilatua", "#duechiacchiere"),
    "world_curiosity": (
        "#curiositàdalmondo", "#curiosità", "#sapevatelo", "#mondo",
        "#imparareognigiorno", "#divulgazione", "#pianeta", "#scopriamoinsieme",
        "#loscoprioggi", "#piccolegrandicose"),
    "word_of_the_day": (
        "#paroladelgiorno", "#lessico", "#vocabolario", "#lingua",
        "#curiositàlinguistiche", "#scrittura", "#leggere", "#etimologia",
        "#linguaitaliana", "#belleparole"),
    "today_in_history": (
        "#ogginellastoria", "#storia", "#accaddeoggi", "#memoria",
        "#anniversario", "#divulgazione", "#passato", "#dateimportanti",
        "#questogiorno", "#storiaitaliana"),
}

#: Total hashtags per post (topic tags + rotating extras).
HASHTAGS_PER_POST = 6

#: Stride used to walk a pool. Coprime with every pool size in use (all pools
#: are 24, 32 or 33 long), so ``index * STRIDE % len(pool)`` visits every entry
#: before repeating, while consecutive days land far apart in the pool.
STRIDE = 7


def from_pool(pool: tuple[str, ...], index: int) -> str:
    """Deterministic, well-spread choice from ``pool`` for ``sequence_index``."""
    if not pool:
        return ""
    from math import gcd
    stride = STRIDE if gcd(STRIDE, len(pool)) == 1 else 1
    return pool[(index * stride) % len(pool)]


def cta_for(content_type: str, index: int) -> str:
    return from_pool(CTA_POOLS.get(content_type, ()), index)


def prompt_for(content_type: str, index: int) -> str:
    return from_pool(PROMPT_POOLS.get(content_type, ()), index)


def mood_for(content_type: str, index: int, default: str,
             category: str | None = None) -> str:
    per_category = CATEGORY_MOOD_POOLS.get(content_type) or {}
    pool = per_category.get(category or "") or MOOD_POOLS.get(content_type)
    return from_pool(pool, index) if pool else default


#: Cumulative days before each month, used to turn ``MM-DD`` into a position in
#: the publication year (29 February included, so the mapping covers 366 days).
_MONTH_OFFSET = (0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335)


def rotation_index(content_type: str, item: dict) -> int:
    """The item's position in the order a reader actually sees it.

    Cyclic pages publish in ``sequence_index`` order, so that is the position.
    ``today_in_history`` publishes by calendar: keying its rotation on
    ``sequence_index`` scrambles it against the reader's timeline and brings
    back exactly the clustering these pools exist to prevent. Its position is
    the day of the year.
    """
    if content_type == "today_in_history":
        key = str(item.get("calendar_key") or "")
        if len(key) == 5 and key[2] == "-":
            try:
                month, day = int(key[:2]), int(key[3:])
            except ValueError:
                return int(item.get("sequence_index") or 0)
            if 1 <= month <= 12:
                return _MONTH_OFFSET[month - 1] + day - 1
    return int(item.get("sequence_index") or 0)


def hashtags_for(content_type: str, index: int, topic_tags: list[str]) -> list[str]:
    """``topic_tags`` (the item's own subject) plus a rotating slice of extras."""
    pool = EXTRA_TAGS.get(content_type, ())
    out: list[str] = []
    for tag in topic_tags:
        if tag and tag not in out:
            out.append(tag)
    if pool:
        start = (index * STRIDE) % len(pool)
        doubled = list(pool) + list(pool)
        for tag in doubled[start:start + len(pool)]:
            if len(out) >= HASHTAGS_PER_POST:
                break
            if tag not in out:
                out.append(tag)
    return out[:HASHTAGS_PER_POST]
