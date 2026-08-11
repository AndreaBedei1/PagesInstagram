# Flusso di revisione editoriale

Come un contenuto passa da «esiste nel dataset» a «può essere pubblicato su un
account reale», e perché i due stati sono lontanissimi.

---

## 1. Validazione strutturale ≠ verifica fattuale

Sono due domande diverse e il progetto le teneva insieme.

| | Validazione strutturale | Verifica fattuale |
|---|---|---|
| Domanda | il contenuto è ben formato? | il contenuto è vero? |
| Chi risponde | `validate-datasets` | una persona |
| Cosa guarda | lunghezza, hashtag, duplicati, `sequence_index`, lingua, campi di provenienza presenti | l'affermazione e la fonte, lette insieme |
| Costo | secondi | minuti per contenuto |
| Copertura | 5.000 su 5.000 | quella dichiarata, mai «tutto» |

La prima release riportava «5.000 approvati, 0 scartati». Era vero, e misurava
soltanto la prima colonna. Un elemento diventava approvato appena portava la
stringa `verified`, un nome di fonte e un URL sintatticamente valido: nessuno
doveva aprire quel link, e il link non doveva esistere.

Quando l'audit ha chiesto ai server, **152 URL su 2.826 rispondevano 404**.

## 2. URL raggiungibile ≠ fonte pertinente

Anche questa distinzione costa cara.

- **Raggiungibile** è una risposta della macchina: la pagina esiste.
  `audit-sources` la ottiene, e non può ottenere altro.
- **Pertinente** è un giudizio umano: quella pagina sostiene *questa*
  affermazione.

Fra i due c'è più spazio di quanto sembri. La voce «Colore primario» esiste ed è
raggiungibile, ma non dice nulla sulla categorizzazione dei colori nelle diverse
lingue. Un link del genere è peggio di un link rotto: sembra a posto.

E c'è un terzo caso, il **soft 404**: Treccani risponde `200` per un lemma che
non ha e reindirizza alla propria home. Settantacinque link inventati sono
passati esattamente così, e con loro un errore di ortografia — `distoglere` per
`distogliere` — che un link funzionante avrebbe smascherato subito.

## 3. Gli stati

Tre assi indipendenti, perché rispondono a domande indipendenti.

### `verification_status` — che cosa dichiara il dataset

```
declared     una fonte è stata scritta, nulla è stato controllato
verified     la costruzione del dataset aveva un riferimento reale
original     scrittura originale, nessuna fonte esterna prevista
unverified
```

Autodichiarato: da solo non concede niente.

### `source_audit_status` — che cosa ha scoperto l'audit

```
not_checked         nessuno ha ancora guardato
reachable           la pagina risponde  ← risultato automatico
manually_verified   la pagina sostiene l'affermazione  ← solo umano
needs_review        esito incerto, serve una persona
unsupported         la pagina NON sostiene l'affermazione
broken_source       404, soft 404, host non consentito
```

**Nessun passaggio automatico può scrivere `manually_verified`.** L'importer lo
copia dai dataset; i dataset lo ricevono solo da `apply-review`; `apply-review`
legge solo un file di verdetti scritto da una persona.

### `editorial_status` — se qualcuno ha letto il testo

```
not_checked | approved | needs_revision | rejected
```

### La regola di pubblicazione

Per curiosità, parole ed eventi storici servono tutti e tre:

```
verification_status = verified
source_audit_status = manually_verified
editorial_status    = approved
```

Per i contenuti originali (pensieri, domande) basta `editorial_status = approved`:
non c'è una fonte da verificare, c'è un testo da leggere.

In produzione il filtro è **sempre** attivo, qualunque cosa dica la
configurazione. `ICE_DATASET_MODE=development_dataset` (predefinito) permette di
generare anteprime e dry-run su contenuti non ancora revisionati; non permette
di pubblicarli.

Se l'indice del giorno punta a un contenuto non pronto, **il giorno si blocca**:
il job va in `NEEDS_REVIEW`. Non viene pubblicato un contenuto vicino, non viene
scelto qualcosa a caso. La data resta una funzione pura della data.

## 4. Approvare un contenuto

```bash
# 1. estrai un campione riproducibile
python -m src.cli editorial-sample --per-dataset 100 --seed 20260805

# 2. apri reports/editorial_sample.html e leggi
#    (affermazione, didascalia, CTA, hashtag, fonte, stato, avvisi)

# 3. scrivi i verdetti
```

`review/miei_verdetti.json`:

```json
{
  "reviewer": "nome e cognome",
  "reviewed_at": "2026-09-01",
  "method": "come hai lavorato, in una frase onesta",
  "verdicts": {
    "world_curiosities_it.json": {
      "224": {"source": "manually_verified", "editorial": "approved",
              "note": "la voce enuncia esattamente questa spiegazione"},
      "631": {"source": "unsupported",
              "note": "la fonte non sostiene il superlativo"}
    }
  }
}
```

```bash
python -m src.cli apply-review review/miei_verdetti.json           # anteprima
python -m src.cli apply-review review/miei_verdetti.json --write   # applica
python -m src.cli validate-datasets
```

Le chiavi sono `sequence_index`. Solo gli elementi elencati vengono toccati:
tutti gli altri restano `not_checked`, ed è questo che rende onesti i conteggi.

## 5. Correggere un contenuto

Modifica il dataset, poi rilancia la catena:

```bash
python -m src.cli validate-datasets     # lingua, struttura, date, lemmi
python -m src.cli editorial-stats       # ripetitività
python -m src.cli audit-sources --apply # se hai toccato una fonte
```

Se cambi `sequence_index`, ricordati che CTA, prompt di sfondo e hashtag lo
seguono: `python tools/refresh_editorial_fields.py --write`.

Non usare sostituzioni globali. Ogni correzione di questo audit è stata fatta
su un elemento alla volta, dopo averlo letto.

## 6. Sostituire una fonte

1. `python -m src.cli audit-sources --apply` per sapere che cosa è rotto.
2. `python tools/propose_source_fixes.py` interroga l'API di ricerca ufficiale e
   scrive i candidati in `reports/source_fix_proposals.json`. **Propone, non
   decide.**
3. Leggi l'affermazione accanto ai candidati e registra la scelta in
   `tools/source_fix_decisions.py`.
4. `python tools/apply_source_fixes.py --write`
5. `python -m src.cli audit-sources --apply` per verificare la sostituzione.

Se nessuna fonte copre l'affermazione, **non inventarne una**: lascia il
contenuto bloccato e annotalo. Un'affermazione come «la clorazione è una delle
misure sanitarie più efficaci mai adottate» ha bisogno di uno studio, non di un
link migliore — e la correzione giusta è un'affermazione diversa.

## 7. Rigenerare i report

```bash
python -m src.cli validate-datasets          # reports/datasets_validation.json
python -m src.cli editorial-stats            # reports/editorial_stats.{json,html}
python -m src.cli editorial-sample           # reports/editorial_sample.{json,html}
python -m src.cli audit-sources              # reports/source_audit.{json,html}
python -m src.cli media-audit                # reports/media_audit.json
python -m src.cli preproduction-smoke-test --date 2026-08-07
```

Nessuno di questi file va committato: sono artefatti, cambiano a ogni
esecuzione e `.gitignore` li esclude. In CI vengono caricati come artifact.

## 8. Evitare la pubblicazione di contenuti non revisionati

Tre livelli, indipendenti:

1. **La modalità.** `ICE_MODE=dry_run` non chiama mai `media_publish`.
2. **Il filtro editoriale.** In produzione la selezione scarta tutto ciò che non
   è `production_ready`, e un giorno bloccato finisce in `NEEDS_REVIEW`.
3. **I test.** `test_no_publishable_item_has_an_unverified_source` fallisce se un
   elemento pubblicabile ha una fonte rotta, non supportata o mai controllata.

Verifica in qualsiasi momento quanti contenuti siano davvero pubblicabili:

```bash
python -m src.cli status
```

## 9. Audit semestrale

Ogni sei mesi, o dopo ogni modifica importante ai dataset:

```bash
python -m src.cli audit-sources --refresh --apply   # i link cambiano
python -m src.cli editorial-sample --per-dataset 100 --seed <nuovo>
# leggi il campione, scrivi i verdetti, applica
python -m src.cli validate-datasets
python -m src.cli editorial-stats
python -m pytest -q
```

Usa un **seed diverso** ogni volta: rileggere lo stesso campione non aggiunge
copertura. Aggiorna `verified_at` sugli elementi rivisti e annota in
[DATASET_SOURCES.md](DATASET_SOURCES.md) quanti ne hai controllati davvero.

## 10. Primo test Meta su un solo account

Non attivare cinque account insieme. L'ordine è:

```bash
# 1. credenziali di un solo account in .env, ICE_MODE ancora dry_run
python -m src.cli instagram health-check --page pensiero_essenziale_it

# 2. genera un media reale senza pubblicarlo
python -m src.cli preproduction-smoke-test --date 2026-08-07 --keep
#    (annota il percorso del file mp4 nella directory conservata)

# 3. passa a test e carica SENZA pubblicare
$env:ICE_MODE = "test"
python -m src.cli instagram upload-test `
      --page pensiero_essenziale_it --file <FILE_MP4> --no-publish

# 4. una sola pubblicazione reale, controllata
python -m src.cli instagram publish-job `
      --page pensiero_essenziale_it --job <JOB_ID> --confirm
```

Il passo 3 crea il container, carica il file e attende l'elaborazione senza mai
chiamare `media_publish`. Il passo 4 è l'unico che pubblica, e richiede
`--confirm`; in `dry_run` non pubblica comunque.

Poi guarda il post: didascalia, hashtag, testo non tagliato, watermark leggibile,
niente coperto dall'interfaccia di Instagram.

## 11. Abilitare le altre pagine

Una alla volta, con almeno **una settimana** di osservazione fra una e l'altra.

1. `enabled: false` sulle pagine non ancora attive, in `accounts/*.yaml`.
2. Attiva la seconda pagina solo dopo sette giorni puliti sulla prima.
3. Prima di ogni attivazione: `health-check`, `validate-datasets`,
   `editorial-stats`, e abbastanza contenuti `production_ready` per il periodo
   che vuoi coprire.
4. Controlla il limite di pubblicazione: è di 100 post per 24 ore **per
   account**, quindi cinque post al giorno su cinque account restano ampiamente
   sotto soglia. Il vincolo reale è la revisione editoriale, non l'API.

---

## Riepilogo dei comandi

| Comando | A cosa serve |
|---|---|
| `validate-datasets` | struttura, lingua, date storiche, lemmi, coerenza degli stati |
| `audit-sources` | i link citati esistono davvero |
| `editorial-sample` | campione stratificato riproducibile da leggere |
| `apply-review` | registra i verdetti umani (unico modo) |
| `editorial-stats` | quanto le pagine sembrano generate |
| `media-audit` | i video rispettano le specifiche Meta |
| `preproduction-smoke-test` | dry-run delle cinque pagine, riproducibile |
| `security-check` | nessun segreto e nessun artefatto runtime tracciato |
