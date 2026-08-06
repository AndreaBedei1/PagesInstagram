# Fonti e metodo di verifica dei dataset

Questo documento spiega **da dove vengono** i contenuti verificati, **come** sono
stati controllati e **quali limiti** restano. È scritto per essere riletto da chi
dovrà aggiornare i dataset in futuro.

Data di verifica di questa release: **2026-08-04** (campo `verified_at` di ogni
elemento). **Audit di pre-produzione: 2026-08-05** — vedi
[PREPRODUCTION_AUDIT.md](PREPRODUCTION_AUDIT.md) e
[EDITORIAL_REVIEW_WORKFLOW.md](EDITORIAL_REVIEW_WORKFLOW.md).

> **Che cosa è cambiato dopo l'audit.** La versione precedente di questo
> documento affermava che i pattern di URL sono «canonici e stabili» e che «un
> errore di battitura in un URL è strutturalmente impossibile». Le due frasi
> erano vere e insieme fuorvianti: l'URL era ben formato, e in 152 casi su 2.826
> la pagina non esisteva. Un URL generato correttamente a partire da un titolo
> sbagliato è comunque un URL sbagliato. Le sezioni §4 e §5 sono state riscritte
> di conseguenza.

---

## 1. Contenuti originali (nessuna fonte esterna)

| Dataset | Natura |
|---|---|
| `philosophical_thoughts_it.json` | pensieri originali scritti per il progetto |
| `daily_questions_it.json` | domande originali scritte per il progetto |

Per questi due dataset:

- **non esiste** e non deve esistere alcuna attribuzione;
- l'importer rifiuta di approvare un pensiero che contenga un autore, un'opera o
  formule tipiche della citazione (`come diceva`, `citazione di`, …);
- `verification_status` vale `original`, non `verified`: non c'è nulla da
  verificare perché non si afferma alcun fatto sul mondo.

Questa è una scelta editoriale precisa: **nessuna falsa citazione**. È molto più
onesto un pensiero anonimo che un aforisma attribuito a un filosofo che non l'ha
mai scritto.

## 2. Contenuti verificati con fonte

| Dataset | Fonte prevalente | Pattern URL |
|---|---|---|
| `words_of_the_day_it.json` | Treccani — Vocabolario della lingua italiana | `https://www.treccani.it/vocabolario/<lemma>/` |
| `world_curiosities_it.json` | Wikipedia in italiano, Treccani, UNESCO, NASA/ESA | `https://it.wikipedia.org/wiki/<Titolo>` e simili |
| `today_in_history_it.json` | Wikipedia in italiano, Treccani | idem |

I pattern di URL sono la forma con cui quelle enciclopedie indirizzano una voce,
generati dalle funzioni `treccani_vocabolario()`, `treccani_enciclopedia()` e
`wikipedia_it()` in `tools/dataset_build/common.py`.

**Questo garantisce la sintassi, non l'esistenza.** Il generatore costruisce
l'indirizzo dal titolo dell'argomento: se il titolo non corrisponde a una voce
reale, il risultato è un link perfettamente formato che non porta da nessuna
parte. L'audit del 2026-08-05 ne ha trovati 152 su 2.826 URL distinti, il 5,4%.
La verifica di raggiungibilità (`audit-sources`) non è un extra: è l'unico
controllo che distingue le due cose.

### Perché queste fonti

- **Treccani** è il riferimento lessicografico e enciclopedico italiano di
  riferimento, con URL stabili nel tempo.
- **Wikipedia in italiano** è verificabile, versionata e raggiungibile da
  chiunque: chi legge il post può controllare in un clic. Non è una fonte
  primaria, ed è per questo che le voci scelte riguardano fatti consolidati e
  ampiamente documentati, non ricostruzioni controverse.
- Per alcuni temi (patrimonio, spazio, conservazione) sono usate le fonti
  istituzionali dirette: UNESCO, NASA, ESA, IUCN.

## 3. Criteri editoriali applicati

### Curiosità

- solo contenuti **evergreen**: nessun dato che possa diventare obsoleto nel giro
  di pochi anni;
- niente statistiche instabili o valori numerici che cambiano di continuo;
- niente miti di internet: dove una credenza diffusa è falsa, il contenuto lo
  dice esplicitamente (per esempio il sale su Cartagine o la Grande Muraglia
  visibile dalla Luna);
- i superlativi (`il più grande`, `l'unico`, `il primo`) compaiono solo quando la
  fonte citata li afferma;
- **gli sfondi sono astratti o illustrativi**: il modello generativo non viene mai
  usato per produrre una finta fotografia documentaria di un luogo o di un evento
  reale, perché inventerebbe dettagli.

### Parole

- solo lemmi italiani reali, presenti nel vocabolario;
- la **definizione è riscritta** con parole nostre: non è una copia di una voce di
  dizionario protetta da diritto d'autore;
- l'**esempio d'uso è originale**, scritto per il progetto;
- l'**etimologia compare solo quando è ben consolidata**; nel dubbio viene omessa
  invece di essere ipotizzata.

### Storia

- la data (`calendar_key`) e l'anno sono la parte più delicata: ogni evento è
  associato al giorno in cui è accaduto, e il motore non può in nessun caso
  pubblicarlo in un altro giorno;
- dove la datazione è convenzionale o discussa (fondazione di Roma, caduta
  dell'Impero d'Occidente, eruzione del Vesuvio del 79, idi di marzo) il testo lo
  dichiara invece di presentare la data come certa;
- descrizioni equilibrate: niente celebrazione né condanna, solo il fatto e il suo
  effetto documentato.

## 4. Gerarchia della qualità delle fonti

Le fonti sono classificate, dalla più forte alla più debole:

| Livello | Che cos'è | Esempi |
|---|---|---|
| `primary` | il documento o la misurazione stessa | atti, pubblicazioni originali, dati di missione |
| `institutional` | enti pubblici e organizzazioni internazionali | UNESCO, NASA, ESA, OMS, ISTAT, musei nazionali |
| `academic` | università e pubblicazioni con revisione paritaria | riviste peer-reviewed, atti accademici |
| `authoritative_reference` | opere di riferimento riconosciute | **Treccani**, vocabolari nazionali, enti normativi |
| `general_encyclopedia` | enciclopedie collaborative | **Wikipedia** |
| `secondary` | stampa di qualità, riviste specializzate | |
| `weak` | tutto il resto | |

Il livello è dedotto dall'host e salvato in `source_tier`
(`src/content/editorial.py`).

### Criteri di accettazione

- l'affermazione è sostenuta **esplicitamente** dalla pagina citata, non solo
  dall'argomento di cui la pagina parla;
- l'URL risponde e non è un soft 404;
- per un'affermazione forte (superlativo, primato, unicità, «visibile dallo
  spazio») serve un livello `authoritative_reference` o superiore, oppure una
  formulazione più limitata.

### Criteri di rifiuto

- la pagina copre l'argomento ma non l'affermazione → `needs_review`, marcata
  come debole e non pubblicabile;
- la pagina contraddice l'affermazione → `unsupported`;
- la pagina non esiste → `broken_source`;
- nessuna fonte adeguata reperibile → il contenuto resta bloccato. **Non si
  inventa un link per chiudere la casella.**

### Dove siamo oggi

I dataset poggiano quasi interamente su `general_encyclopedia` (Wikipedia) e
`authoritative_reference` (Treccani). Va bene per la divulgazione — chi legge può
controllare in un clic — ed è insufficiente per le affermazioni più forti. Quelle
sono state riformulate o bloccate durante l'audit, non lasciate in piedi con una
fonte debole.

## 4-bis. Verifica automatica

`python -m src.cli validate-datasets` controlla, per ogni elemento dei tre dataset
verificati:

- `verification_status` uguale a `verified`;
- `source_name` non vuoto;
- `source_url` formalmente valido (schema `https`, host con punto);
- `verified_at` in formato `YYYY-MM-DD` e data reale;
- **che nessun elemento pubblicabile abbia una fonte rotta, non supportata o mai
  controllata** — è l'unico invariante bloccante fra quelli sulle fonti.

`python -m src.cli audit-sources` interroga i server: schema, host consentito,
stato HTTP, redirect, soft 404, duplicati. Usa l'API ufficiale MediaWiki per
Wikipedia (50 titoli per richiesta, voci mancanti e redirect dichiarati
esplicitamente) e HTTPS con limitazione di frequenza per host altrove, con cache
su disco per poter essere interrotto e ripreso.

Il report elenca anche tutte le fonti usate con la loro frequenza, così si vede
subito se un dataset dipende troppo da un'unica origine.

## 4-ter. Convenzione sul calendario storico

Il dataset `today_in_history_it.json` registra le date **come le riportano
comunemente le fonti moderne**, cioè in calendario gregoriano proiettato
all'indietro dove è la convenzione consolidata.

Quando la fonte storica originale usa un calendario diverso e la differenza è
rilevante, l'elemento porta `metadata.calendar_note` che dichiara entrambe le
date. Esempio: la rivoluzione di febbraio è archiviata all'**8 marzo 1917**
(gregoriano), con la nota che nel calendario giuliano allora in uso in Russia
corrisponde al **23 febbraio**.

`src/content/history_check.py` applica la regola: una data scritta nel titolo che
non coincide con `calendar_key` è un **errore**, a meno che `calendar_note` non
sia presente. Segnala inoltre per revisione tutti gli eventi anteriori al 1582 e
quelli fra il 1582 e il 1923, periodo in cui l'adozione del calendario gregoriano
non era ancora universale.

La scelta è dichiarata qui perché non deve restare implicita: le tre convenzioni
possibili — data storica originale, data convertita, data comunemente riportata —
danno risultati diversi, e il progetto usa la terza.

## 5. Copertura effettiva dell'audit — i numeri reali

Questa sezione esiste perché il rapporto precedente lasciava intendere una
copertura che non c'era.

### Raggiungibilità delle fonti (automatica, completa)

| Dataset | URL distinti | Raggiungibili | Rotti |
|---|---|---|---|
| `world_curiosities_it.json` | 977 | 955 | 22 |
| `words_of_the_day_it.json` | 997 | 990 | 7 |
| `today_in_history_it.json` | 846 | 843 | 3 |
| **Totale** | **2.820** | **2.788** | **32** |

Si è partiti da 152 link rotti. 140 sono stati sostituiti dopo revisione manuale;
75 soft 404 di Treccani sono emersi solo dopo aver corretto il rilevatore, e 67
lemmi sono stati risolti verificando la forma reale dell'entrata. I 32 restanti
non hanno una voce adeguata e restano **bloccati**, non nascosti.

### Verifica fattuale (manuale, campionaria)

| Dataset | Letti | Su | `manually_verified` | Bloccati dopo lettura |
|---|---|---|---|---|
| `world_curiosities_it.json` | 103 | 1.000 | 95 | 8 |
| `words_of_the_day_it.json` | 100 | 1.000 | 96 | 4 |
| `today_in_history_it.json` | 104 | 1.000 | 91 | 13 |
| `philosophical_thoughts_it.json` | 100 | 1.000 | — (originali) | 0 |
| `daily_questions_it.json` | 100 | 1.000 | — (originali) | 0 |

**482 contenuti su 5.000 sono pubblicabili in produzione.** Gli altri 4.518 non
sono stati rifiutati: non sono stati letti, che è una cosa diversa, e il sistema
lo dichiara invece di lasciarlo intendere.

### Limiti noti — da leggere

1. **L'audit fattuale è campionario.** È stato letto il 10% del corpus. Il
   restante 90% è strutturalmente valido e con fonte raggiungibile, e questo non
   dice nulla sulla sua correttezza. Non trattarlo come verificato.
2. **Wikipedia è una fonte terziaria.** Per un uso divulgativo su Instagram è
   adeguata e verificabile dal pubblico, ma non è una fonte primaria. Se un
   contenuto dovesse diventare oggetto di discussione, sostituisci l'URL con la
   fonte primaria corrispondente e aggiorna `verified_at`.
3. **`verified_at` è la data del controllo, non una garanzia perpetua.** Un fatto
   evergreen resta valido, ma la fonte che lo attesta può cambiare.
4. **Le date convenzionali restano convenzionali.** Alcune ricorrenze storiche
   sono tradizionali e non documentate con precisione: il testo lo segnala, ma
   chi aggiorna i dataset deve mantenere questa cautela.
5. **La deduplicazione semantica è euristica.** Riconosce le riformulazioni più
   evidenti, non ogni possibile parafrasi. Chi aggiunge contenuti deve comunque
   leggere ciò che scrive.
6. **45 fonti sostituite sono deboli per costruzione.** Coprono l'argomento
   dell'affermazione ma non l'affermazione stessa. Sono marcate `needs_review`,
   non sono pubblicabili, e vanno rimpiazzate con una fonte pertinente oppure
   accompagnate da un'affermazione più limitata.
7. **Il rilevamento dei soft 404 è specifico per host.** Riconosce il redirect
   alla home — il comportamento di Treccani — e i marcatori testuali noti. Un
   sito che restituisse `200` con una pagina d'errore diversa passerebbe: quando
   aggiungi un host nuovo, controlla prima come si comporta con un URL
   inesistente.

## 6. Come aggiungere una fonte diversa

1. aggiungi la voce nel file `tools/dataset_build/data/<pagina>/part_NN.py`
   usando l'helper URL corretto (o l'URL completo, se la fonte è istituzionale);
2. se introduci un nuovo dominio, aggiungilo a `source_name_for()` nel builder
   della pagina, così il nome leggibile della fonte viene riconosciuto;
3. rigenera e valida:

```powershell
python tools\build_datasets.py
python -m src.cli validate-datasets
python -m src.cli import-content
```
