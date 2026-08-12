# Audit di pre-produzione — cinque pagine evergreen

Audit editoriale, tecnico e di pre-produzione eseguito sul branch
`fix/preproduction-editorial-audit`, che parte da `feature/five-evergreen-pages`.

Lo scopo non è riprogettare il sistema, ma **verificare criticamente** quanto
dichiarato nel rapporto precedente, correggere i problemi reali e portare il
branch a uno stato onesto rispetto a una prima prova Meta controllata.

Nessuna pubblicazione reale è stata effettuata. `ICE_MODE` è rimasto
`dry_run` per tutta la lavorazione.

---

## 0. Verifica delle affermazioni del rapporto precedente

| Affermazione | Esito della verifica |
|---|---|
| «Undici commit» | **Falsa.** `git rev-list --count fix/direct-meta-upload..feature/five-evergreen-pages` restituisce **10**. Il rapporto contava male il proprio elenco. |
| «Documentazione Meta riverificata» | **Parzialmente vera.** Il flusso resumable era descritto correttamente, ma la versione predefinita era rimasta indietro e i limiti di durata dei Reel erano sbagliati (vedi §1). |
| «5.000 approvati, 0 scartati» | **Vera ma fuorviante.** Dimostra conformità strutturale, non correttezza fattuale: il validatore controllava solo che un URL fosse formalmente ben scritto. |
| «Generazione ComfyUI reale» | **Vera**, e ricontrollata in questo audit (§10). |
| «135 test verdi» | **Vera in locale.** Nessuno stato CI osservabile era però associato al commit finale (§9). |
| «Dry-run cinque pagine» | **Vera ma non riproducibile**: eseguito su una copia locale del database poi ripristinata, senza artifact versionato (§8). |

---

## 1. Versione Graph API — `[x]`

- [x] Versione corrente verificata sul changelog ufficiale
- [x] Versione usata dagli esempi ufficiali di Instagram Content Publishing
- [x] Endpoint, header e host del resumable upload riverificati
- [x] Specifiche video dei Reel riverificate (il valore precedente era errato)
- [x] Default allineato in `src/core/settings.py`, `config/settings.yaml`,
      `config/settings.example.yaml`, `.env.example`, `src/publishing/graph_client.py`
- [x] `docs/META_RESUMABLE_UPLOAD.md` aggiornato con la data di verifica
- [x] Test che impedisce default discordanti fra codice, YAML, `.env.example` e documentazione

## 2. Audit di raggiungibilità delle fonti — `[x]`

- [x] Comando `python -m src.cli audit-sources`
- [x] Cache locale su disco, ripresa dell'audit, rate limiting, timeout, retry limitati
- [x] Report `reports/source_audit.json` e `reports/source_audit.html`
- [x] Cache e report esclusi da Git
- [x] Una fonte irraggiungibile **non** marca il contenuto come falso, ma ne blocca la pubblicazione

## 3. Stati editoriali — `[x]`

- [x] Migrazione `0004_editorial_states.sql`
- [x] `source_audit_status`, `source_audited_at`, `source_audit_note`, `editorial_status`
- [x] L'importer non approva più un contenuto fattuale solo perché ha un URL formalmente valido
- [x] La selezione di produzione pubblica soltanto contenuti `production_ready`
- [x] Modalità `development_dataset` per l'anteprima dei contenuti non ancora revisionati

## 4. Controlli linguistici — `[x]`

- [x] Controlli automatici su tutti i 5.000 contenuti (`validate-datasets`)
- [x] Errori reali corretti nei dataset
- [x] Test di regressione sugli errori scoperti

## 5. Audit fattuale stratificato — `[x]`

- [x] Campione manuale su curiosità, eventi storici e parole
- [x] Superlativi e affermazioni assolute trattati con attenzione specifica
- [x] Contenuti non supportati corretti o sostituiti, non conservati per gonfiare il conteggio
- [x] Elementi non controllati lasciati in `needs_review`, non dichiarati verificati

## 6. Ripetitività editoriale — `[x]`

- [x] Comando `python -m src.cli editorial-stats`
- [x] Comando `python -m src.cli editorial-sample`
- [x] Pool di CTA ampliati e differenziati per pagina
- [x] Soglie di ripetitività verificate da test

## 7. Distribuzione temporale — `[x]`

- [x] Analisi su finestre di 7 / 30 / 90 / 365 giorni
- [x] Test sulla varietà in finestra

## 8. Dry-run riproducibile — `[x]`

- [x] Comando `python -m src.cli preproduction-smoke-test --date 2026-08-07`
- [x] Database temporaneo, cinque job, zero Storie, idempotenza
- [x] Report `reports/preproduction_smoke_test.json` (artifact, non committato)

## 9. CI — `[x]`

- [x] Sintassi del workflow validata localmente
- [x] Test, validazione dataset, security check e smoke test in CI
- [x] `compileall`
- [x] Nessun download SDXL, nessun ComfyUI nella CI standard
- [x] Report caricati come artifact

## 10. Media e modello locale — `[x]`

- [x] Audit ffprobe su un video per pagina (`reports/media_audit.json`)
- [x] Controlli geometrici sulle safe area
- [x] `allow_fallback_in_production: false` garantito da un test

## 11. Sicurezza — `[x]`

- [x] Scanner esteso (bearer, OAuth, query string con token, header serializzati, file di modello)
- [x] `security-check` verde
- [x] Nessun segreto, nessun modello, nessun report runtime committato

## 12. Documentazione — `[x]`

- [x] `docs/EDITORIAL_REVIEW_WORKFLOW.md`
- [x] `README.md`, `docs/PRODUCTION_CHECKLIST.md`, `docs/DATASET_SOURCES.md` aggiornati

---

## 13. Esiti misurati

| Verifica | Comando | Esito |
|---|---|---|
| Compilazione | `python -m compileall src tools` | 0 |
| Suite di test | `pytest -q` | **216 test verdi** (135 → 216) |
| Ambiente | `src.cli validate` | 0 |
| Dataset | `src.cli validate-datasets` | 0 errori bloccanti, 25 avvisi |
| Fonti | `src.cli audit-sources` | 2.820 URL unici, 2.788 raggiungibili, **32 rotti bloccati** |
| Ripetitività | `src.cli editorial-stats --strict` | 0 segnalazioni |
| Campione | `src.cli editorial-sample --per-dataset 100` | 500 contenuti, 5 dataset |
| Sicurezza | `src.cli security-check` | 226 file, nessun segreto, nessun artefatto runtime |
| Smoke test | `src.cli preproduction-smoke-test --date 2026-08-07` | 10 controlli su 10 |
| Media | `src.cli media-audit` | 5 video conformi |

### Il numero che conta

| Stadio | Contenuti |
|---|---|
| `total_items` | 5.000 |
| `structurally_valid` | 5.000 |
| `fact_checked` (letti da una persona) | 282 |
| `editorially_approved` | 502 |
| **`production_ready`** | **482** |

Prima dell'audit il rapporto diceva «5.000 approvati». Nessun contenuto è stato
perso: 482 sono stati letti e approvati, gli altri 4.518 non sono stati letti, e
adesso il sistema distingue le due cose invece di confonderle.

### I difetti trovati

| Difetto | Quanti | Esito |
|---|---|---|
| URL di fonte inesistenti | 152 | 140 sostituiti dopo revisione, 12 bloccati |
| Soft 404 di Treccani scambiati per redirect validi | 75 | rilevatore corretto, 67 lemmi risolti, 7 bloccati |
| Errori di lingua | 3 | corretti uno per uno |
| Lemma con ortografia sbagliata (`distoglere`) | 1 | corretto in `distogliere` |
| Affermazioni non sostenute dalla fonte | 6 | 4 riformulate, 2 bloccate |
| Eventi storici con data o titolo incoerenti | 2 | 1 corretto, 1 con nota di calendario |
| Frasi identiche su contenuti diversi | 8 | riscritte |
| CTA ripetuta ogni 5-6 giorni | 5 dataset | pool da 31-32 voci per pagina |
| Prompt di sfondo ripetuto ogni 5-6 giorni | 5 dataset | 24 prompt per pagina |
| Categoria ripetuta per 31 giorni consecutivi | 1 dataset | ordinamento ridistribuito |
| Mood unico per 1.000 elementi | 2 dataset | pool per categoria |
| Campionamento «stratificato» che restituiva 100 elementi consecutivi | 1 | chiave di batch corretta |
| Versione Graph API divergente fra codice e documentazione | 4 file | fonte unica + test |
| Durata massima dei Reel documentata male (90 s invece di 15 min) | 1 | corretta **solo nella documentazione** (vedi nota) |
| Smoke test che pubblicava 6 elementi invece di 5 | 1 | `plan_enabled` sul worker |

> **Nota aggiunta l'11 agosto 2026.** La riga sulla durata dei Reel diceva
> «corretta» e non lo era del tutto: erano state corrette la tabella e la
> costante in `src/core/meta_api.py`, mentre `src/publishing/publisher.py`
> continuava a imporre `3 <= dur <= 90` in un letterale proprio. Restava quindi
> il difetto che contava — un Reel valido di due minuti veniva rifiutato prima
> di raggiungere Meta — mentre l'audit lo registrava come risolto. È stato
> chiuso nella revisione pre-credenziali, e un test cerca ora nell'albero una
> seconda copia del limite proprio perché correggere la prosa non basta.

---

## Limiti dichiarati di questo audit

Sono elencati esplicitamente nel rapporto finale e in
[DATASET_SOURCES.md](DATASET_SOURCES.md). In sintesi: l'audit fattuale è
**campionario**, non esaustivo; i contenuti non campionati restano
`needs_review` e non sono pubblicabili in produzione finché non vengono
revisionati.
