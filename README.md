# Instagram Content Engine — cinque pagine evergreen

Motore **locale, deterministico e multi-account** che gestisce cinque pagine
Instagram evergreen e pubblica **un contenuto al giorno per pagina**, senza
servizi cloud, senza browser automation, senza API di modelli a pagamento e
**senza richiedere Claude a runtime**.

```
datasets/*.json  ──import──►  SQLite (sequence_index / calendar_key)
                                   │
              planner (60 gg)  ────┼──►  publication_jobs idempotenti
                                   │
   selezione deterministica per (page_id, data locale programmata)
                                   │
                ComfyUI SDXL locale → SOLO lo sfondo (seed deterministico)
                                   │
                     Pillow → testo (un template per pagina)
                                   │
              qualità (WCAG, margini, righe) + auto-riparazione
                                   │
                    PNG 4:5 1080×1350 (nessun video, nessun audio)
                                   │
   worker → tunnel effimero → POST /media IMAGE con image_url → media_publish
```

> Progettato e testato su Windows 11 + Python 3.14 + NVIDIA RTX 6000 Ada.

---

## Le cinque pagine

| Pagina | `page_id` | Contenuto | Orario | Policy |
|---|---|---|---|---|
| Pensiero Essenziale | `pensiero_essenziale_it` | pensieri filosofici originali | 08:30 | `cyclic_ordered` |
| Curiosità dal Mondo | `curiosita_mondo_it` | fatti verificati con fonte | 11:00 | `cyclic_ordered` |
| Parola del Giorno | `parola_giorno_it` | lessico italiano con fonte | 13:30 | `cyclic_ordered` |
| Oggi nella Storia | `oggi_nella_storia_it` | eventi del giorno corrente | 17:00 | `calendar_rotating` |
| Una Domanda al Giorno | `domanda_giorno_it` | domande originali | 20:30 | `cyclic_ordered` |

Ogni pagina è **un solo file YAML** in `accounts/`. Aggiungerne o modificarne una
non richiede di toccare il codice.

## I contenuti

**5.000 elementi, 1.000 per pagina, tutti pronti per la produzione.** Ogni
contenuto fattuale porta con sé la prova con cui è stato costruito: la frase
della fonte, l'URL, il titolo della pagina, la data del controllo e un hash che
lo lega al proprio testo. Modificare il testo dopo la verifica lo rende
automaticamente non pubblicabile — è il modo in cui il cancello impedisce di
approvare un corpus e poi cambiarne il contenuto.

| Pagina | Elementi | Metodo di verifica | Fonte |
|---|---|---|---|
| Pensiero Essenziale | 1.000 | `original_nonfactual` | scrittura originale |
| Una Domanda al Giorno | 1.000 | `original_nonfactual` | scrittura originale |
| Una Parola al Giorno | 1.000 | `authoritative_reference` | Treccani, voce del lemma |
| Oggi nella Storia | 1.000 | `structured_official_dataset` / `cross_checked_sources` | elenco del giorno + voce dell'evento |
| Curiosità dal Mondo | 1.000 | `structured_official_dataset` | incipit della voce citata |

I contenuti fattuali sono stati **costruiti a partire dalle fonti**, non scritti
per primi e corredati di fonte dopo: `verification_executor` vale
`automated_source_first`, e il campo esiste proprio per non chiamare "verifica
manuale" un lavoro che una persona non ha svolto.

```powershell
python -m src.cli corpus-final-gate        # 12 contatori, tutti a zero
python -m src.cli production-readiness --from 2026-08-07 --days 1000 --all-pages
```

Fonti, gerarchia di qualità, copertura effettiva dell'audit e limiti residui:
[docs/DATASET_SOURCES.md](docs/DATASET_SOURCES.md). Come si approva un contenuto:
[docs/EDITORIAL_REVIEW_WORKFLOW.md](docs/EDITORIAL_REVIEW_WORKFLOW.md).

---

## Avvio rapido

```powershell
scripts\bootstrap_five_pages.ps1        # da repo pulito a dry-run funzionante
scripts\install_local_model.ps1         # scarica SDXL Base 1.0 per ComfyUI
```

Poi, quando vuoi pubblicare davvero: compila `.env`, esegui
[docs/PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md) e imposta
`ICE_MODE=production`.

## Documentazione

| Documento | Contenuto |
|---|---|
| [FIVE_PAGES_SETUP.md](docs/FIVE_PAGES_SETUP.md) | installazione, gestione, backup, ripristino, aggiornamenti |
| [CONTENT_ROTATION.md](docs/CONTENT_ROTATION.md) | come viene scelto il contenuto del giorno |
| [DATASET_SCHEMA.md](docs/DATASET_SCHEMA.md) | struttura dei dataset e regole di validazione |
| [DATASET_SOURCES.md](docs/DATASET_SOURCES.md) | fonti, gerarchia di qualità, copertura reale dell'audit, limiti noti |
| [EDITORIAL_REVIEW_WORKFLOW.md](docs/EDITORIAL_REVIEW_WORKFLOW.md) | stati editoriali, come approvare, primo test Meta |
| [PREPRODUCTION_AUDIT.md](docs/PREPRODUCTION_AUDIT.md) | che cosa ha trovato l'audit di pre-produzione |
| [PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md) | **le variabili da compilare e i comandi, in ordine** |
| [LOCAL_MODEL_SETUP.md](docs/LOCAL_MODEL_SETUP.md) | ComfyUI, SDXL, licenza, profili di sfondo |
| [META_RESUMABLE_UPLOAD.md](docs/META_RESUMABLE_UPLOAD.md) | API ufficiali Meta, upload diretto |
| [PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md) | tutto ciò che va verificato prima della produzione |
| [FIVE_PAGES_IMPLEMENTATION_PLAN.md](docs/FIVE_PAGES_IMPLEMENTATION_PLAN.md) | piano di implementazione e stato |

---

## Architettura

```
accounts/*.yaml   ── una pagina = un file (nessun segreto)
config/           ── settings globali non-segreti (+ override da .env)
datasets/*.json   ── 5.000 contenuti versionati
tools/            ── sorgenti dei dataset + build_datasets.py
comfyui/          ── workflow SDXL (e SD1.5 legacy)
database/         ── SQLite con migrazioni versionate
generated/        ── output: sfondi, immagini, video
reports/          ── validazione dataset, anteprime, campione di revisione
src/
  core/        paths, settings, logging (redazione token), enum, tempo
  accounts/    modelli e registry delle pagine
  content/     normalizzazione, qualità, dedup, importer, selezione, validazione
  comfyui/     client API + generatore sfondi (SDXL/SD1.5, seed deterministici)
  rendering/   font, layout, template a blocchi, renderer
  quality/     WCAG, validazione immagine/video, auto-riparazione
  video/       ffmpeg + builder (Ken Burns leggero, traccia AAC silenziosa)
  publishing/  credenziali da env, Graph API, mock, publisher idempotente
  scheduling/  pipeline, planner, worker con buffer scorrevole, lock
  security/    scanner di segreti sui file tracciati
  monitoring/  stato, report JSON, anteprime HTML
  dashboard/   dashboard locale FastAPI
  cli/         interfaccia a riga di comando
```

**Stato del job** (idempotente, una sola pubblicazione):
`DRAFT → VALIDATED → BACKGROUND_GENERATED → RENDERED → MEDIA_READY → SCHEDULED →
UPLOADING → CONTAINER_CREATED → PUBLISHING → PUBLISHED`, con rami
`FAILED / RETRY_PENDING / SKIPPED / REJECTED / NEEDS_REVIEW`.

## Selezione deterministica

```
giorni  = (data_locale_programmata − cycle_anchor_date).days
indice  = giorni mod 1000
ciclo   = giorni div 1000
```

Stessa pagina + stessa data ⇒ **sempre** lo stesso contenuto, indipendentemente
da riavvii, ordine delle query SQL, inserimenti successivi, casualità e numero di
tentativi. Dopo 1.000 giorni il testo si ripete, ma `ciclo` entra nel seed dello
sfondo e l'immagine è nuova.

"Oggi nella Storia" usa invece la rotazione per calendario: filtra su
`calendar_key = MM-DD` e ruota per anno, quindi non pubblica **mai** un evento in
un giorno diverso da quello in cui è accaduto.

## Buffer scorrevole

```yaml
generation:
  prepare_ahead_days: 30
  planning_horizon_days: 60
  published_media_retention_days: 45
```

Il worker, a ogni tick: pianifica 60 giorni di job → genera i media dei 30 giorni
successivi → pubblica i job scaduti → riprende quelli interrotti → elimina i media
locali dei job **pubblicati** più vecchi della retention (database, log e metadati
restano; i file di job falliti o da revisionare non vengono mai toccati).
Il buffer si ricostruisce da solo dopo un riavvio.

## Rendering

Cinque template tipografici realmente distinti, non lo stesso layout ricolorato:

| Pagina | Struttura |
|---|---|
| Pensiero Essenziale | testo centrale grande in serif, filetto, watermark |
| Curiosità dal Mondo | occhiello con la località, filetto, fatto grande, categoria |
| Parola del Giorno | etichetta, parola molto grande, categoria grammaticale in corsivo, filetto, definizione |
| Oggi nella Storia | giorno e mese spaziati, anno grandissimo, filetto, titolo, descrizione |
| Una Domanda al Giorno | numero progressivo, domanda centrale, filetto, fondo scuro |

Un'unica ricerca binaria trova la dimensione base che fa entrare l'intera pila di
blocchi nel riquadro sicuro. Restano attivi controllo di contrasto WCAG, margini,
adattamento del font, wrapping, limite righe, auto-riparazione e validazione della
risoluzione.

Anteprime reali: `python -m src.cli preview-pages --per-page 5` →
`reports/previews/index.html`.

## Video

MP4 1080×1920, H.264 High, `yuv420p`, 30 fps, **8 secondi**, GOP chiuso,
`+faststart`, Ken Burns molto leggero (zoom 1.04). Le cinque pagine pubblicano
**senza musica**: il file porta comunque una traccia **AAC silenziosa** a 48 kHz
per compatibilità con il contenitore atteso da Meta. Nessuna musica di terzi,
nessuna dipendenza dalla libreria audio di Instagram.

## Pubblicazione

Il contenuto principale è un **post immagine 4:5 (1080×1350)**: queste pagine
pubblicano testo, e una immagine ferma non ha traccia audio, codec o durata da
sbagliare. Meta lo preleva da `image_url`.

Quell'URL non richiede hosting: il motore serve **un solo file** da `127.0.0.1`
dietro un **Cloudflare Quick Tunnel** che vive quanto la singola pubblicazione —
nessun account, nessun dominio, nessuna carta, niente acceso a riposo. Il
resumable upload (`rupload.facebook.com`) resta implementato per i video, ma è
un protocollo per video: un'immagine non ha byte da caricare.

Modalità: `dry_run` (default, nessuna rete) · `test` (pubblicazione solo manuale
e confermata) · `production` (worker automatico, fallback dello sfondo vietato).

## Sicurezza

- token **solo** in `.env`, mai negli YAML, mai nel database, mai nei log
  (redazione automatica);
- `python -m src.cli security-check` analizza i file **tracciati da Git** e cerca
  pattern compatibili con token Meta, app secret e credenziali, verificando anche
  che `.env`, `secrets/` e `*.safetensors` siano esclusi;
- `python -m src.cli instagram health-check --all` verifica le credenziali senza
  pubblicare nulla e **senza stampare mai un token**;
- dashboard solo su `127.0.0.1`, protetta da token.

## Test

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -m "not integration and not e2e" -q
```

Oltre alla suite preesistente, `tests/unit/test_evergreen.py` e
`tests/integration/test_evergreen_worker.py` coprono i requisiti delle cinque
pagine: cinque YAML, cinque pagine attive, 1.000 contenuti per dataset e 5.000
totali, indici `0..999` senza buchi, wrap dal giorno 999 al giorno 0, stabilità
dopo riavvio e dopo nuovi inserimenti, sfondo nuovo al ciclo successivo, coerenza
`MM-DD`, 29 febbraio, almeno due eventi per data, nessun contenuto fattuale
approvato senza fonte, dedup esatta/fuzzy/semantica, i cinque template con
contrasto e margini, grafo SDXL, divieto di fallback in produzione, buffer 30/60
giorni, retention dei media, idempotenza per pagina e data, pubblicazione mock
per ciascuna pagina, ripristino dopo crash, assenza di segreti, 5 job in un
giorno e 35 in sette, nessuna Story pianificata, worker singolo.

`tests/unit/test_editorial_quality.py` e `tests/integration/test_preproduction.py`
aggiungono i controlli nati dall'audit di pre-produzione: coerenza della versione
Graph API fra codice, YAML, `.env.example` e documentazione; errori di lingua
sui 5.000 contenuti; soft 404 e redirect; nessun contenuto pubblicabile con
fonte non verificata; verifica manuale dichiarata e minoritaria; CTA, hashtag,
prompt, categorie e mood non ripetitivi su finestre di 7, 30, 90 e 365 giorni;
campionamento riproducibile e distribuito; date storiche e convenzione di
calendario; lemmi e URL lessicografici; smoke test delle cinque pagine con
idempotenza; `--no-publish` che resta tale; redazione dei token; fallback vietato
in produzione.

## Comandi CLI

```
validate            Controlla ambiente (ffmpeg, ComfyUI, font, DB, pagine)
init-db             Crea/aggiorna il database e registra le pagine
validate-datasets   Valida i cinque dataset (struttura, lingua, date, lemmi)
verify-corpus       Legge le fonti ed estrae la prova di ogni affermazione
rebuild-unverified-corpus  Ricostruisce dai sorgenti ciò che non supera la verifica
corpus-final-gate   Il cancello unico: 12 contatori, tutti a zero
production-readiness Simula 1.000 giorni x 5 pagine con il gate di produzione
prepare-buffer      Genera il buffer di media senza pubblicare (idempotente)
buffer-status       Copertura dei giorni davanti + artefatti non referenziati
arm-page / arming-status  Arma una pagina dopo il canary
audit-sources       Verifica che gli URL delle fonti esistano davvero
editorial-sample    Campione stratificato riproducibile da rivedere a mano
apply-review        Registra i verdetti umani (unico modo per approvare)
editorial-stats     Misura quanto le pagine sembrano generate
media-audit         Controlla i video contro le specifiche Meta (ffprobe)
preproduction-smoke-test  Dry-run riproducibile delle cinque pagine
security-check      Cerca segreti e artefatti runtime tracciati da Git
import-content      Importa i dataset (dedup + qualità + controllo fonti)
preview-pages       Anteprime reali per pagina + indice HTML comparativo
sample-review       Campione HTML di contenuti per la revisione umana
generate            Genera media per N contenuti di una pagina
render              Solo immagini (anteprima rapida)
schedule            Pianifica i job giornalieri
worker              Worker persistente (--once per un solo ciclo)
publish-next        Prepara e pubblica il prossimo job (--dry-run)
retry-failed        Rimette in coda i job FAILED
status              Riepilogo stato
preview             Galleria HTML dei media generati
export-report       Report JSON
dashboard           Avvia la dashboard locale
pages               Elenca le pagine

comfyui status                Modello locale configurato e raggiungibilità
comfyui test-generation       Genera uno sfondo reale (fallisce sul fallback)

instagram health-check --all  Credenziali: token, scadenza, permessi, account
instagram check-config        Configurazione publishing (offline)
instagram token-status        Identità del token; scadenza solo con app id/secret
instagram account-status      Tipo account / limite di pubblicazione
instagram upload-test         Container + upload di prova (non pubblica)
                              --publish richiede anche --confirm fuori da dry_run
instagram canary-plan         Il canary simulato: zero chiamate a Meta
instagram canary-upload       Container + upload reali, nessuna pubblicazione
instagram canary-status       A che punto è il canary (una volta sola)
instagram publish-canary      L'unica pubblicazione senza armamento, una volta
instagram publish-job         Pubblica un job (richiede --confirm e la pagina armata)
```

## Da cosa dipende il funzionamento continuo

Il sistema può funzionare in autonomia per lunghi periodi, ma **non è
autosufficiente**. Continua a dipendere da:

- **PC acceso** all'orario previsto, o riavviato entro la finestra di recupero
  (`missed_job_window_minutes`, predefinita 240 minuti);
- **ComfyUI funzionante** e checkpoint presente: in produzione un guasto manda il
  job in `NEEDS_REVIEW`, non pubblica media degradati;
- **validità dei token Meta**: scadono ogni 60 giorni e vanno rinnovati;
- **stabilità delle API Meta**: modifiche o interruzioni del servizio bloccano la
  pubblicazione;
- **spazio disponibile su disco** per il buffer dei media;
- **account Instagram attivi**, non bloccati né scollegati dall'app.

Con 1.000 contenuti per pagina il ciclo dura circa due anni e nove mesi prima di
ricominciare, con immagini nuove. Questo non equivale a un funzionamento
garantito senza manutenzione: le voci sopra vanno controllate periodicamente
(vedi la sezione *Manutenzione ricorrente* della checklist di produzione).

## Licenza

Codice: MIT. I contenuti dei dataset sono originali (pensieri, domande,
definizioni riscritte, esempi) oppure fatti verificati con fonte citata; il
modello generativo ha licenza propria (vedi
[LOCAL_MODEL_SETUP.md](docs/LOCAL_MODEL_SETUP.md)).
