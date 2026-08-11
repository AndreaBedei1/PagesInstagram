# Schema dei dataset

I cinque dataset vivono in `datasets/` e sono **generati** dalle sorgenti in
`tools/dataset_build/` tramite `python tools/build_datasets.py`. Il JSON è il
formato di runtime; le sorgenti Python sono l'originale editabile.

| File | `content_type` | Pagina | Policy |
|---|---|---|---|
| `philosophical_thoughts_it.json` | `philosophical_thought` | Pensiero Essenziale | `cyclic_ordered` |
| `world_curiosities_it.json` | `world_curiosity` | Curiosità dal Mondo | `cyclic_ordered` |
| `words_of_the_day_it.json` | `word_of_the_day` | Parola del Giorno | `cyclic_ordered` |
| `today_in_history_it.json` | `today_in_history` | Oggi nella Storia | `calendar_rotating` |
| `daily_questions_it.json` | `daily_question` | Una Domanda al Giorno | `cyclic_ordered` |

Ogni file contiene **esattamente 1.000 elementi approvati**; in totale **5.000**.

---

## Involucro del file

```json
{
  "schema_version": 1,
  "content_type": "philosophical_thought",
  "language": "it",
  "generated_by": "tools/build_datasets.py",
  "notes": "…",
  "items": [ … ]
}
```

## Campi comuni a ogni elemento

```json
{
  "id": "pt-0000-chi-decide-in-fretta",
  "sequence_index": 0,
  "text": "testo principale (compare sull'immagine)",
  "category": "categoria",
  "mood": "mood del vocabolario canonico",
  "caption": "didascalia originale, mai una ripetizione del testo",
  "call_to_action": "invito facoltativo, aggiunto in coda alla didascalia",
  "hashtags": ["#esempio", "#altro"],
  "background_prompt": "prompt privo di qualunque testo leggibile",
  "status": "approved_for_publication",
  "metadata": {}
}
```

| Campo | Regola verificata da `validate-datasets` |
|---|---|
| `sequence_index` | intero, unico, copre **0..999** senza buchi |
| `text` | non vuoto, lunghezza entro i limiti di rendering del tipo |
| `caption` | non vuota, ≤ 2200 caratteri, non identica al testo |
| `hashtags` | lista di 3–10 voci, senza duplicati |
| `background_prompt` | non vuoto |
| `status` | è una **richiesta**: la decisione finale spetta all'importer |

## Campi aggiuntivi per i contenuti verificati

`world_curiosity`, `word_of_the_day` e `today_in_history` devono avere:

```json
{
  "verification_status": "verified",
  "source_name": "Nome leggibile della fonte",
  "source_url": "https://…",
  "verified_at": "2026-08-04"
}
```

Senza **tutti e quattro** i campi l'importer non approva l'elemento: lo mette in
`needs_review` e lo rende visibile nella dashboard. Non esiste alcun percorso che
pubblichi un contenuto fattuale privo di fonte.

## Campi specifici per tipo

### `philosophical_thought`
- nessun `author`, `author_display_name` o `source_work`: la presenza di uno di
  questi campi blocca l'approvazione (i pensieri sono originali, non citazioni);
- `metadata.theme` = categoria.

### `daily_question`
- `text` deve terminare con `?`;
- `metadata.theme` e `metadata.number` (progressivo mostrato sull'immagine).

### `word_of_the_day`
- `text` è il **lemma** (una sola parola);
- `metadata.part_of_speech`, `metadata.definition`, `metadata.example`,
  `metadata.etymology` (facoltativa), `metadata.register`;
- la deduplicazione fuzzy/semantica è disattivata per questo tipo (molte parole
  italiane condividono la radice senza essere duplicati); resta attiva quella
  esatta sul lemma.

### `world_curiosity`
- `text` è il fatto principale (compare sull'immagine);
- `metadata.title`, `metadata.location`, `metadata.explanation`, `metadata.category`.

### `today_in_history`
- `calendar_key` in formato `MM-DD`, validato anche per il **29 febbraio**;
- `metadata.year`, `metadata.description`, `metadata.calendar_key`;
- copertura completa: tutte e **366** le chiavi presenti, ciascuna con **almeno
  due eventi**, così la rotazione annuale ha sempre un'alternativa.

## Controlli eseguiti da `validate-datasets`

1. numero esatto di elementi (`--expected`, default 1000);
2. `text` e `caption` non vuoti;
3. `sequence_index` unico e senza buchi da 0 a N-1;
4. duplicati **esatti** (hash del testo normalizzato);
5. duplicati **fuzzy** (RapidFuzz `token_sort_ratio` ≥ 0.88);
6. duplicati **semantici** (coseno su n-grammi di parole e caratteri ≥ 0.82);
7. lunghezze compatibili con i template di rendering;
8. hashtag presenti, nel numero corretto, senza ripetizioni;
9. `background_prompt` presente;
10. per i tipi verificati: `verification_status`, `source_name`, `source_url`
    formalmente valido, `verified_at` in formato ISO;
11. per `today_in_history`: `calendar_key` valido, anno plausibile, nessuna data
    scoperta, almeno due eventi per data, 29 febbraio incluso;
12. per `philosophical_thought`: nessuna attribuzione;
13. per `daily_question`: la frase termina con `?`.

Il comando esce con codice diverso da zero se **anche uno solo** di questi
requisiti non è rispettato, e scrive un report JSON con:

- conteggio per dataset e per categoria;
- elenco dei duplicati esatti e quasi-duplicati;
- elenco degli elementi non verificati;
- distribuzione degli eventi storici per data;
- lunghezze minime, medie e massime di testo e didascalia;
- statistiche di qualità;
- elenco delle fonti usate con la relativa frequenza;
- errori bloccanti e avvisi.

```powershell
python -m src.cli validate-datasets --report reports\datasets_validation.json
```

## Soglie di approvazione all'import

L'importer calcola un punteggio di qualità con euristiche adattate al tipo di
contenuto (un lemma di dizionario e un titolo storico non possono essere valutati
con i criteri di una frase motivazionale) e confronta il risultato con la soglia
del tipo:

| `content_type` | Soglia |
|---|---|
| `philosophical_thought` | 0.72 |
| `daily_question` | 0.70 |
| `world_curiosity` | 0.60 |
| `word_of_the_day` | 0.55 |
| `today_in_history` | 0.55 |

Sotto soglia l'elemento non viene perso: finisce in `needs_review`.
