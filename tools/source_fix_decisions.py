"""Reviewed replacements for the source URLs the audit found broken.

162 links were dead. They were not typos in real references — they were titles
invented from the subject line and never opened: ``Diffusione_di_Rayleigh``
instead of ``Scattering_di_Rayleigh``, ``Petra_(sito_archeologico)`` instead of
``Petra_(Giordania)``, ``Cavo_telegrafico_transatlantico`` for an article that
does not exist in Italian at all.

Every entry below is a decision taken by reading the claim and the candidate
article, one at a time. Two outcomes only:

``FIXED``
    An existing article that is genuinely *about the subject of the claim*. The
    URL is replaced and re-audited; that makes it ``reachable``, which is still
    not ``manually_verified`` — supporting the claim is a separate judgement,
    recorded during the factual review.

``NO_SOURCE``
    No article covers the claim. Nothing is invented to fill the gap: the item
    keeps its broken source, stays blocked from production, and is listed as
    what it is. Several of these are claims that would need a specific study
    rather than an encyclopedia entry ("a spoonful of soil holds billions of
    micro-organisms", "chlorination is one of the most effective public-health
    measures ever adopted") — the right fix there is a better claim, not a
    better link.

Keys are ``sequence_index`` within each dataset.
"""
from __future__ import annotations

WIKI = "https://it.wikipedia.org/wiki/"


def w(title: str) -> str:
    return WIKI + title.replace(" ", "_")


#: sequence_index -> replacement article title (Italian Wikipedia)
CURIOSITIES_FIXED: dict[int, str] = {
    55: "Petra (Giordania)",
    63: "Chadō",
    152: "Terrazzamenti di Banaue",
    154: "Diga di Ma'rib",
    207: "Uccelli migratori",
    224: "Scattering di Rayleigh",
    327: "Regata Storica",
    330: "Maiolica di Deruta",
    354: "Deserto florido",
    449: "Evoluzione degli uccelli",
    519: "Ignác Semmelweis",
    567: "Legatura",
    602: "Storia dell'illuminazione",
    646: "Rete fantasma",
    650: "Specie alloctona",
    685: "Meteora di Čeljabinsk",
    704: "Stato senza sbocco al mare",
    735: "Orario di lavoro",
    743: "Barriera di sicurezza",
    756: "Obelisco incompiuto di Assuan",
    791: "Sonno polifasico",
    808: "Visione del colore",
    856: "Corporazioni delle arti e mestieri",
    876: "Compressione dati lossy",
    884: "Controllo attivo del rumore",
    885: "Ecolocalizzazione",
    916: "Paleosismologia",
    930: "Radiosondaggi",
    955: "Erosione",
    967: "Neve rossa",
    969: "Cerchio delle streghe",
    989: "Calcara (fornace)",
    997: "Uperizzazione",
    560: "Storia del mosaico",
    721: "Unità di misura",
    782: "Ancoraggio",
    942: "Orientamento",
    972: "Samara",
    908: "Fiume",
    442: "Parco nazionale del fiume sotterraneo di Puerto Princesa",
    190: "Gigantismo abissale",
    775: "Carta da archivio",
    636: "Scienza del suolo",
    644: "Seed saving",
    631: "Potabilizzazione dell'acqua",
    881: "Disco stroboscopico",
    247: "Lingua siciliana",
    43: "Colore primario",
    454: "Laetoli",
    464: "Domesticazione",
    998: "Etichetta nutrizionale",
}

#: Claims for which no encyclopedia article covers the specific statement.
CURIOSITIES_NO_SOURCE: dict[int, str] = {
    106: "nessuna voce copre l'assenza di prove sulla previsione animale dei terremoti",
    133: "manca una voce italiana sul cavo telegrafico transatlantico",
    155: "nessuna voce sui ponti di radici viventi del Meghalaya",
    246: "nessuna voce sull'ordine dei costituenti nelle lingue del mondo",
    337: "nessuna voce sulle marionette kathputli",
    375: "nessuna voce sul problema della longitudine",
    665: "affermazione generica, nessuna voce specifica",
    748: "nessuna voce sulle tariffe di trasporto a zone",
    763: "nessuna voce sull'analisi isotopica applicata alla dieta",
    770: "nessuna voce sull'analisi dei pigmenti nei falsi d'autore",
    783: "nessuna voce italiana sul paradosso della scelta",
    784: "nessuna voce italiana sull'effetto cocktail party",
    794: "nessuna voce sulla rugoscopia palatina",
    800: "nessuna voce sulla corsa di resistenza negli esseri umani",
    814: "nessuna voce sulla percezione del coriandolo",
    836: "nessuna voce sulla storia della numerazione delle pagine",
    845: "nessuna voce sulla disposizione dei banchi scolastici",
    868: "nessuna voce sul salvataggio automatico",
    899: "nessuna voce sull'inquinamento acustico subacqueo",
    912: "nessuna voce sui sistemi di allerta meteorologica",
    968: "nessuna voce sugli alberi bandiera",
    976: "nessuna voce sulle piante pirofite",
}

HISTORY_FIXED: dict[int, str] = {
    5: "Vulcano (astronomia)",
    44: "Inondazione di melassa di Boston",
    54: "Storia dell'illuminazione",
    57: "Parlamento di De Montfort",
    59: "XX emendamento della Costituzione degli Stati Uniti d'America",
    76: "Prima Flotta",
    84: "Benz Patent Motorwagen",
    87: "Concerto dei Beatles sul tetto",
    88: "Ascesa al potere di Adolf Hitler",
    96: "Ulisse (Joyce)",
    114: "Meteorite",
    174: "DNA",
    187: "Servitù della gleba in Russia",
    232: "Isabella Stewart-Gardner Museum",
    233: "Carrosse à cinq sols",
    250: "Exxon Valdez",
    262: "2 Pallas",
    295: "Sinagoga Shearith Israel di New York",
    297: "Appomattox Court House",
    345: "DNA",
    352: "Elezioni generali in Sudafrica del 1994",
    363: "Grande Esposizione di Londra del 1851",
    378: "Esposizione universale di Parigi (1889)",
    383: "Capitolazione della Germania nazista",
    386: "Fine della seconda guerra mondiale in Europa",
    390: "First Transcontinental Railroad",
    408: "Premi Oscar 1929",
    438: "Pena di morte nel Regno Unito",
    467: "First Transcontinental Railroad",
    469: "Conferenza delle Nazioni Unite sull'ambiente umano",
    494: "Miranda warning",
    496: "Alcock e Brown",
    514: "Stemma degli Stati Uniti d'America",
    550: "Zeppelin",
    552: "Benz Patent Motorwagen",
    560: "Bikini",
    565: "Pancarré",
    574: "Telstar",
    592: "Trinity (test nucleare)",
    595: "Programma test Apollo-Sojuz",
    596: "Punch",
    607: "John Scopes",
    613: "Telstar",
    640: "Storia della schiavitù",
    674: "Sue (dinosauro)",
    699: "Vincenzo Peruggia",
    713: "Great Moon Hoax",
    722: "Storia della schiavitù",
    723: "Motocicletta",
    747: "Spedizione di Ferdinando Magellano",
    789: "Spedizione di Ferdinando Magellano",
    805: "Carta dei Diritti degli Stati Uniti d'America",
    808: "Stockton & Darlington Railway",
    852: "Lunedì nero",
    853: "1I/ʻOumuamua",
    864: "Battaglia di Balaklava",
    874: "Guerra dei mondi",
    876: "Papa Leone X",
    900: "Luna 17",
    901: "Trapezio (circo)",
    902: "Massacro del giorno di san Brizio",
    907: "Brasile",
    911: "Lunochod",
    950: "Blue Marble",
    954: "The mother of all demos",
    966: "Carta dei Diritti degli Stati Uniti d'America",
    969: "Terremoti di New Madrid del 1811-1812",
    23: "Impero coloniale tedesco",
    92: "Ham",
    101: "3 febbraio",
    184: "Rifornimento in volo",
    265: "Impero britannico",
    299: "Salon",
    407: "Stati con armi nucleari",
    443: "Traversate transatlantiche",
    444: "Programma Mercury",
    509: "Giordano Bruno",
    581: "Espulsioni ed esodi degli ebrei",
    624: "Traversate transatlantiche",
    651: "Traversate transatlantiche",
    684: "Traversate transatlantiche",
    687: "Albuquerque",
    871: "Prospero",
    887: "Selden Motor Vehicle Company",
    973: "Score",
    977: "Storia dell'energia nucleare",
}

HISTORY_NO_SOURCE: dict[int, str] = {
    148: "nessuna voce italiana sull'eruzione dell'Huaynaputina del 1600",
    525: "nessuna voce italiana sull'epidemia di coreomania del 1374",
    733: "nessuna voce italiana su Emma Nutt",
}


def _bad_choices() -> dict[str, set[int]]:
    """Entries kept deliberately weak, flagged so the review report can show them.

    These point at an article that covers the *area* of the claim but not the
    claim itself. They are fixed enough to stop being dead links and are
    explicitly **not** eligible for ``manually_verified``.
    """
    return {
        "world_curiosity": {560, 721, 782, 942, 972, 908, 442, 190, 775, 636,
                            644, 631, 881, 247, 43},
        "today_in_history": {23, 92, 101, 184, 265, 299, 407, 443, 444, 509,
                             525, 581, 624, 651, 684, 687, 733, 871, 887, 973,
                             977, 114, 174, 345, 640, 722, 723, 907, 596, 560},
    }


WEAK = _bad_choices()

DECISIONS = {
    "world_curiosities_it.json": (CURIOSITIES_FIXED, CURIOSITIES_NO_SOURCE),
    "today_in_history_it.json": (HISTORY_FIXED, HISTORY_NO_SOURCE),
}
