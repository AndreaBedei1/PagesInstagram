# Fonti e metodo di verifica dei dataset

Questo documento spiega **da dove vengono** i contenuti verificati, **come** sono
stati controllati e **quali limiti** restano. È scritto per essere riletto da chi
dovrà aggiornare i dataset in futuro.

Data di verifica di questa release: **2026-08-04** (campo `verified_at` di ogni
elemento).

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

I pattern di URL sono **canonici e stabili**: sono la forma con cui quelle
enciclopedie indirizzano una voce, non link costruiti a caso. Il builder li
genera dalle funzioni `treccani_vocabolario()`, `treccani_enciclopedia()` e
`wikipedia_it()` in `tools/dataset_build/common.py`, così un errore di battitura
in un URL è strutturalmente impossibile.

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

## 4. Verifica automatica

`python -m src.cli validate-datasets` controlla, per ogni elemento dei tre dataset
verificati:

- `verification_status` uguale a `verified`;
- `source_name` non vuoto;
- `source_url` formalmente valido (schema `https`, host con punto);
- `verified_at` in formato `YYYY-MM-DD` e data reale.

Il report elenca anche tutte le fonti usate con la loro frequenza, così si vede
subito se un dataset dipende troppo da un'unica origine.

## 5. Limiti noti — da leggere

1. **La validazione degli URL è formale, non di rete.** Il validatore controlla
   che l'indirizzo sia ben formato, non che risponda `200`. Le enciclopedie
   rinominano o uniscono voci nel tempo, quindi qualche link può diventare un
   reindirizzamento o una pagina mancante. Verifica periodicamente con uno
   strumento di link checking; è un'attività di manutenzione, non un difetto
   della pipeline.
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
