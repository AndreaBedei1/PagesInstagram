# Instagram Content Engine

Motore **unico, modulare e multi-account** per generare e pubblicare
automaticamente contenuti su più pagine Instagram (un contenuto al giorno per
pagina, **feed + storia**), con:

- sfondi generati **localmente con ComfyUI** (Stable Diffusion),
- testo aggiunto in modo **deterministico** (Pillow) — il modello non scrive mai il testo,
- **controllo qualità misurabile** con auto-correzione,
- **musica coerente col mood**, con licenza, incorporata nel video,
- **Reel 9:16 (condiviso nel feed) + Story 9:16** dello stesso contenuto/musica del giorno,
- **scheduler persistente** con ripristino dopo riavvio (Windows Task Scheduler),
- **pubblicazione tramite API ufficiali Meta** con **resumable upload diretto**
  (il file locale viene caricato direttamente ai server Meta): **nessun hosting
  pubblico, nessuna porta aperta, nessuno storage esterno**. Modalità `dry_run` /
  `test` / `production`. Dettagli: [docs/META_RESUMABLE_UPLOAD.md](docs/META_RESUMABLE_UPLOAD.md).
- **dashboard locale** di revisione.

Aggiungere una pagina = aggiungere **un file YAML** in `accounts/`. Nessun codice duplicato.

> Progettato e testato su Windows 11 + Python 3.14 + NVIDIA RTX 6000 Ada. ComfyUI
> è opzionale a runtime: senza GPU/ComfyUI l'engine usa uno sfondo di fallback
> deterministico e la pipeline funziona comunque end-to-end.

---

## Indice
1. [Architettura](#architettura)
2. [Requisiti](#requisiti)
3. [Installazione (Windows)](#installazione-windows)
4. [Configurazione ComfyUI](#configurazione-comfyui)
5. [Configurazione FFmpeg](#configurazione-ffmpeg)
6. [Database](#database)
7. [Configurazione degli account](#configurazione-degli-account)
8. [Token Meta / Instagram](#token-meta--instagram)
9. [Modalità dry-run / test / produzione](#modalità-dry-run--test--produzione)
10. [Aggiungere una nuova pagina](#aggiungere-una-nuova-pagina)
11. [Aggiungere contenuti](#aggiungere-contenuti)
12. [Aggiungere musica e licenze](#aggiungere-musica-e-licenze)
13. [Scheduler e worker](#scheduler-e-worker)
14. [Gestione PC spento](#gestione-pc-spento)
15. [Ripristino dopo crash](#ripristino-dopo-crash)
16. [Dashboard](#dashboard)
17. [Log](#log)
18. [Test](#test)
19. [Sicurezza](#sicurezza)
20. [Limiti API Meta (reali)](#limiti-api-meta-reali)
21. [Backup e aggiornamento](#backup-e-aggiornamento)
22. [Troubleshooting](#troubleshooting)
23. [Comandi CLI](#comandi-cli)

---

## Architettura

```
accounts/*.yaml   ── una pagina = un file (config indipendente, niente segreti)
config/           ── settings globali non-segreti (+ override da .env)
datasets/*.json   ── contenuti seed (frasi motivazionali, citazioni verificate)
assets/           ── font, musica (con catalogo licenze), overlay, template
comfyui/          ── workflow SD1.5 di riferimento
database/         ── SQLite (schema versionato con migrazioni)
generated/        ── output: backgrounds, posts, stories, reels, failed
src/
  core/        paths, settings, logging (redazione token), enum, timeutils
  accounts/    modelli + registry delle pagine
  content/     normalizzazione, qualità testo, dedup, importer, caption
  comfyui/     client API + generatore sfondi (con fallback)
  rendering/   font, layout (wrap+fit), renderer tipografico
  quality/     WCAG, validazione immagine/video + loop di auto-riparazione
  music/       sintesi toni CC0, libreria/licenze, selezione per mood
  video/       ffmpeg + builder video (Ken Burns + musica bakizzata)
  publishing/  credenziali (solo env), Graph API client, mock, publisher
  scheduling/  pipeline generazione, planner giornaliero, worker persistente, lock
  monitoring/  status, report JSON, preview HTML
  dashboard/   dashboard FastAPI locale
  cli/         interfaccia a riga di comando (typer)
```

**Macchina a stati del job** (idempotente, una sola pubblicazione):
`DRAFT → VALIDATED → BACKGROUND_GENERATED → RENDERED → MEDIA_READY → SCHEDULED →
UPLOADING → CONTAINER_CREATED → PUBLISHING → PUBLISHED` con rami
`FAILED / RETRY_PENDING / SKIPPED / REJECTED / NEEDS_REVIEW`.

**Pipeline giornaliera:** planner crea i job → worker seleziona un contenuto
approvato (mai duplicato sulla pagina) → ComfyUI genera lo sfondo → rendering del
testo → validazione qualità con auto-fix (colore → posizione → overlay →
dimensione → wrapping → rigenera sfondo) → selezione musica per mood → video con
musica incorporata → pubblicazione (o dry-run) all'orario previsto.

---

## Requisiti

- **Windows 10/11**
- **Python ≥ 3.11** (testato su 3.14)
- **[uv](https://docs.astral.sh/uv/)** (gestione ambiente; consigliato)
- **FFmpeg**: non serve installarlo — è incluso via `imageio-ffmpeg`. Se ne hai
  uno di sistema, puoi usarlo con `ICE_FFMPEG_PATH`.
- **ComfyUI** (opzionale a runtime): per gli sfondi reali. GPU NVIDIA consigliata.
- Per la **pubblicazione reale**: account Instagram **professional** (Business/Creator),
  token Meta long-lived e un **hosting pubblico** per i media (vedi §20).

---

## Installazione (Windows)

```powershell
# dalla cartella del progetto
scripts\install_windows.ps1
```

Lo script: crea `.venv` con uv, installa le dipendenze, copia `.env.example`→`.env`,
valida l'ambiente, inizializza il DB e importa dataset + musica placeholder.

Installazione manuale equivalente:

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe -m src.cli validate
.venv\Scripts\python.exe -m src.cli init-db
.venv\Scripts\python.exe -m src.cli import-content
.venv\Scripts\python.exe -m src.cli music generate
.venv\Scripts\python.exe -m src.cli music sync
```

Verifica completa (env + test + pipeline dry-run):

```powershell
scripts\validate_installation.ps1
```

---

## Configurazione ComfyUI

L'engine parla con ComfyUI via **API locali ufficiali** (`/prompt`, `/history`,
`/view`). Configurazione in `config/settings.yaml` → `comfyui:` o via env:

```
ICE_COMFYUI_URL=http://127.0.0.1:8188
ICE_COMFYUI_LAUNCH_BAT=F:\AI\start_comfyui.bat   # per l'avvio automatico
ICE_COMFYUI_OUTPUT_DIR=F:\AI\output
```

- Se `auto_start: true` e il launcher esiste, l'engine **avvia ComfyUI** e attende
  che sia pronto (fino a `startup_timeout_seconds`).
- Il workflow usato è un **SD1.5 txt2img** (checkpoint `DreamShaper_8_pruned.safetensors`),
  costruito in `src/comfyui/workflow.py`; riferimento in `comfyui/workflows/sd15_background.json`.
- ComfyUI genera **solo lo sfondo**. Il testo è aggiunto dopo, in modo deterministico.
- Senza ComfyUI raggiungibile, l'engine usa uno **sfondo di fallback** (gradiente
  elegante per mood) e prosegue.

---

## Configurazione FFmpeg

Nessuna azione necessaria: viene usato il binario di `imageio-ffmpeg`. Per usare
un ffmpeg di sistema imposta `ICE_FFMPEG_PATH=C:\path\ffmpeg.exe` (o `video.ffmpeg_path`
in `settings.yaml`). I video rispettano i requisiti Meta: MP4/H.264 High, `yuv420p`,
GOP chiuso, **AAC 48 kHz stereo**, `+faststart`.

---

## Database

SQLite in `database/content.sqlite`, **schema versionato** con migrazioni in
`src/database/migrations/` applicate automaticamente all'apertura.
Tabelle: `pages, contents, media_assets, publication_jobs, publication_logs,
music_tracks, music_usage, schema_migrations`. **Nessun token** è mai salvato nel DB.

```powershell
.venv\Scripts\python.exe -m src.cli init-db     # crea/aggiorna schema + registra pagine
```

---

## Configurazione degli account

Ogni pagina è un file YAML in `accounts/` (vedi `motivational_page.yaml`,
`famous_quotes_page.yaml`). Campi principali: `page_id`, `content_type`,
`publishing` (orari feed/story, timezone, policy PC-spento), `visual` (stile,
`show_author`, logo), `music` (profilo, volume), `content` (soglia qualità).
I **segreti non vanno mai** nello YAML: si usano variabili d'ambiente.

---

## Token Meta / Instagram

L'engine usa l'**Instagram Content Publishing API** ufficiale. Consigliata la
configurazione *Instagram API with Instagram Login* (`graph.instagram.com`,
permessi `instagram_business_basic`, `instagram_business_content_publish`).

I segreti si impostano in `.env` (mai committato), **per pagina**:

```
# prefisso = ICE_<PAGE_ID_MAIUSCOLO>
ICE_MOTIVATIONAL_IT_IG_USER_ID=1789xxxxxxxxxxx
ICE_MOTIVATIONAL_IT_ACCESS_TOKEN=EAAG...        # long-lived (60 giorni)
ICE_FAMOUS_QUOTES_IT_IG_USER_ID=1789yyyyyyyyyyy
ICE_FAMOUS_QUOTES_IT_ACCESS_TOKEN=EAAG...
# in alternativa un token globale:
META_ACCESS_TOKEN=EAAG...
```

Ottenere il token (sintesi): crea un'app su developers.facebook.com, collega
l'account professional, ottieni un token short-lived (1h) e scambialo per uno
**long-lived (60 giorni)**, rinnovabile prima della scadenza. Vedi la
[doc ufficiale](https://developers.facebook.com/docs/instagram-platform/content-publishing/).

> **Nessun hosting pubblico richiesto.** Con `upload_method: resumable` (default)
> il video locale viene caricato **direttamente** ai server Meta
> (`rupload.facebook.com`). `ICE_PUBLIC_MEDIA_BASE_URL` NON serve — è usato solo
> dal provider opzionale/legacy `hosted_url`. Vedi [docs/META_RESUMABLE_UPLOAD.md](docs/META_RESUMABLE_UPLOAD.md).

---

## Modalità dry-run / test / produzione

Impostabile con `ICE_MODE` o `config/settings.yaml`:

- **`dry_run`** (default): nessuna credenziale, nessun upload reale. Genera tutto
  (sfondo, video, caption); il job diventa `PUBLISHED` con media id `DRYRUN-...` (idempotente).
- **`test`**: credenziali Instagram necessarie; **resumable upload reale**, ma la
  pubblicazione avviene **solo con comando esplicito e conferma**
  (`ice instagram publish-job --confirm`). Il worker **non** pubblica in automatico.
- **`production`**: upload resumable diretto e worker automatico. Nessun URL
  pubblico, nessuno storage esterno. Fallback ComfyUI **disabilitato** (una
  generazione fallita va in `NEEDS_REVIEW` invece di pubblicare media degradati).
  Richiede account business verificato e token valido.

```powershell
$env:ICE_MODE="dry_run"; .venv\Scripts\python.exe -m src.cli publish-next --dry-run
```

---

## Aggiungere una nuova pagina

1. Copia `accounts/example_future_page.yaml` → `accounts/la_mia_pagina.yaml`.
2. Cambia `page_id`, `display_name`, `content_type`, orari, stile.
3. Aggiungi i segreti in `.env` con prefisso `ICE_<PAGE_ID_MAIUSCOLO>_...`.
4. Assicurati che esistano contenuti approvati per quel `content_type`.
5. `init-db` (registra la pagina) e `schedule`. Fatto — **nessun codice** da toccare.

---

## Aggiungere contenuti

I contenuti vivono in `datasets/*.json` e vengono importati con dedup + qualità:

```powershell
.venv\Scripts\python.exe -m src.cli import-content            # tutti i dataset
.venv\Scripts\python.exe -m src.cli import-content --dataset datasets/mio.json
```

- **Motivazionali**: `text, category, mood, explanation, caption, call_to_action,
  hashtags, background_prompt`. Qualità ≥ soglia ⇒ `approved_for_publication`.
- **Citazioni**: in più `author, source_work, source_year, source_url,
  attribution_confidence, status`. Solo `verified` + `high` + fonte ⇒ approvate;
  le incerte restano `needs_review` (revisione manuale in dashboard).

Deduplicazione a 3 livelli (esatta/hash, fuzzy, semantica) con clustering:
niente due contenuti che dicono la stessa cosa con parole diverse.

---

## Aggiungere musica e licenze

Vedi `assets/music/README.md`. In sintesi:

- Ogni traccia è in `assets/music/catalog.json` con **licenza obbligatoria**;
  senza licenza ⇒ `instagram_safe=0` ⇒ **mai** selezionata.
- Toni segnaposto **CC0** generati (uno per mood): `python -m src.cli music generate`.
- Aggiungi tracce reali (royalty-free/pubblico dominio/licenziate), poi
  `python -m src.cli music sync`.
- La musica è **incorporata nel video** con fade-in/out e volume attenuato
  (l'API Meta non consente audio della libreria Instagram — vedi §20).

---

## Scheduler e worker

Il **worker** persistente: pianifica i job, genera i media in anticipo e pubblica
all'orario previsto.

```powershell
.venv\Scripts\python.exe -m src.cli schedule --days 7     # pianifica
.venv\Scripts\python.exe -m src.cli worker                # esegui (foreground)
.venv\Scripts\python.exe -m src.cli worker --once         # un solo ciclo
```

Avvio automatico all'accensione (Windows Task Scheduler — **eseguire dalla propria
sessione interattiva**, serve il permesso di registrare task):

```powershell
scripts\register_task_scheduler.ps1      # avvio al login, riavvio automatico
scripts\unregister_task_scheduler.ps1    # rimozione
scripts\stop_worker.ps1                  # stop controllato (via PID lock)
```

Il worker usa un **lock a livello di OS** per evitare due istanze; gestisce
timezone Europe/Rome con ora legale.

---

## Gestione PC spento

Se il PC è spento all'orario previsto, alla riaccensione il worker applica la
`missed_job_policy` della pagina:

- `publish_immediately` — pubblica subito;
- `publish_within_window` — pubblica solo se il ritardo ≤ `missed_job_window_minutes`, altrimenti salta;
- `skip` — salta il contenuto (stato `SKIPPED`);
- `reschedule` — riprogramma al giorno successivo.

**Architettura futura** (già predisposta): ComfyUI genera in anticipo sul PC
locale, i media vengono caricati su storage, e un **piccolo server sempre acceso**
si occupa solo della pubblicazione. Non incluso ora, ma il publisher è disaccoppiato
dalla generazione (basta puntare `ICE_PUBLIC_MEDIA_BASE_URL` allo storage e far
girare il worker in modalità sola-pubblicazione sul server).

---

## Ripristino dopo crash

- Job idempotenti: un contenuto non viene mai pubblicato due volte (chiave
  univoca per pagina/tipo/giorno; container e media id riusati).
- All'avvio il worker **riprende** i job in volo (`recover()`), riusando il
  `container_id` già creato.
- Retry con **backoff esponenziale** solo per errori transitori; nessun retry
  infinito; oltre il massimo → `FAILED` (rimettibili in coda con `retry-failed`).

---

## Dashboard

```powershell
.venv\Scripts\python.exe -m src.cli dashboard
# apri l'URL mostrato:  http://127.0.0.1:8765/?token=<TOKEN>
```

Solo localhost, protetta da token (`ICE_DASHBOARD_TOKEN`). Permette: panoramica,
**revisione/approvazione** contenuti, galleria media (immagini+video+musica),
lista job con errori, **pausa/riattivazione** pagina, retry.

---

## Log

`logs/engine.log` con **rotazione** (5×5 MB). I **token non compaiono mai** nei log
(redazione automatica). Il worker scrive anche `logs/worker_*.out.log`.
Report JSON: `python -m src.cli export-report`.

---

## Test

```powershell
.venv\Scripts\python.exe -m pytest -q            # tutto
.venv\Scripts\python.exe -m pytest -m "not integration and not e2e" -q   # solo unit
```

Coprono: config/YAML, DB/migrazioni, dedup/similarità, qualità testo, selezione
contenuti/musica, rendering, wrap/contrasto/margini, validazione immagine/video,
creazione video, scheduler/timezone, job duplicati, retry, dry-run, **mock delle
risposte Meta** (token scaduto, container fallito, rate limit, già pubblicato),
worker end-to-end e dashboard.

---

## Sicurezza

- Token **solo** in variabili d'ambiente; `.env` escluso da git; **mai** nei log né nel DB.
- URL validati; media serviti dalla dashboard solo da `generated/` (no path traversal).
- Escaping dell'HTML in dashboard/preview; lock del DB (WAL + busy_timeout).
- Dashboard su `127.0.0.1` con token; nessuna porta esposta pubblicamente di default.
- Backup periodici e rotazione dei log.

---

## Limiti API Meta (reali)

Rilevati dalla documentazione ufficiale (vedi anche `docs/PLAN.md` §2):

- **Le Stories via API NON supportano musica/sticker/link/audio di tendenza.**
- **I Reels via API non hanno accesso ai suoni di tendenza/catalogo Instagram.**
  ⇒ **La musica va incorporata nel file video** prima dell'upload (ciò che fa l'engine).
- **Limite: 100 post pubblicati via API in 24h** (un carosello = 1). Il nostro
  fabbisogno (1/giorno/pagina) è ampiamente sotto soglia.
- Account **professional** (Business/Creator) obbligatorio.
- Token long-lived **60 giorni**, rinnovabile.
- **Upload diretto (resumable)**: il video locale viene caricato ai server Meta
  senza URL pubblici. Endpoint: `POST graph.*/{ver}/{ig-user-id}/media?upload_type=resumable`
  → upload binario su `rupload.facebook.com` → `media_publish`. Nessun hosting,
  nessuna porta, nessuno storage esterno. Vedi [docs/META_RESUMABLE_UPLOAD.md](docs/META_RESUMABLE_UPLOAD.md).
- L'unico **blocco reale** per la produzione sono i **token Meta** + un **account
  business** di test; tutto il resto è testato in dry-run/mock.
- **Non** si usano Selenium/automazioni non ufficiali: solo API ufficiali.

---

## Backup e aggiornamento

**Backup**: copia `database/content.sqlite`, `accounts/`, `datasets/`,
`assets/music/catalog.json`, `.env` (in luogo sicuro). Esempio:

```powershell
Copy-Item database\content.sqlite "backup\content_$(Get-Date -Format yyyyMMdd).sqlite"
```

**Aggiornamento**:

```powershell
git pull
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m src.cli init-db     # applica eventuali migrazioni
.venv\Scripts\python.exe -m pytest -q
```

---

## Troubleshooting

| Sintomo | Causa / Rimedio |
|---|---|
| `ComfyUI not running` in `validate` | Avvia ComfyUI o imposta `auto_start`; l'engine usa comunque il fallback |
| Video senza audio | Nessuna traccia `instagram_safe`: aggiungi musica con licenza e `music sync` |
| `ICE_PUBLIC_MEDIA_BASE_URL non impostato` | Solo se hai impostato `upload_method: hosted_url`. Col default `resumable` non serve alcun hosting |
| Story fallisce con "account business" | Le Stories via API richiedono un account Instagram **business** (vedi `instagram account-status`) |
| `missing credentials` | Imposta `ICE_<PAGE>_IG_USER_ID` e `_ACCESS_TOKEN` in `.env` |
| Font brutto/di sistema | Metti un TTF in `assets/fonts/` (es. Inter/EB Garamond) |
| Task Scheduler "Accesso negato" | Esegui `register_task_scheduler.ps1` dalla **tua** sessione interattiva |
| Qualità immagine bassa | Il validatore auto-corregge; se persiste, rigenera lo sfondo o abbassa `min_score` |

---

## Comandi CLI

```
validate         Controlla ambiente (ffmpeg, ComfyUI, font, DB, pagine)
init-db          Crea/aggiorna il database e registra le pagine
import-content   Importa i dataset (dedup + qualità)
generate         Genera media (sfondo+immagini+video) per N contenuti
render           Solo immagini (anteprima rapida)
schedule         Pianifica i job giornalieri
worker           Worker persistente (--once per un ciclo)
publish-next     Prepara e pubblica il prossimo job (--dry-run)
retry-failed     Rimette in coda i job FAILED
status           Riepilogo stato
preview          Galleria HTML dei media
export-report    Report JSON
dashboard        Avvia la dashboard locale
pages            Elenca le pagine
music generate   Genera toni CC0 (uno per mood)
music sync       Carica catalog.json nel DB

instagram check-config   --page P     Verifica config publishing (offline)
instagram token-status   --page P     Validità del token (debug_token)
instagram account-status --page P     Tipo account / id / limite pubblicazione
instagram upload-test    --page P --file F [--no-publish]   Container+upload di prova (no publish)
instagram create-container --page P --job J   Crea solo il container resumable per un job
instagram publish-job    --page P --job J --confirm   Pubblica un job (richiede --confirm)
```

## Prima pubblicazione reale (account Business di test)

Non servono credenziali durante lo sviluppo (tutto testato in dry-run/mock).
Quando vuoi la prima prova reale, imposta in `.env`:

```
ICE_MOTIVATIONAL_IT_IG_USER_ID=...
ICE_MOTIVATIONAL_IT_ACCESS_TOKEN=...   # long-lived, account business di test
META_APP_ID=...
META_APP_SECRET=...
```

Poi, con `ICE_MODE=test`, esegui **in ordine**:

```powershell
$env:ICE_MODE="test"
.venv\Scripts\python.exe -m src.cli instagram token-status   --page motivational_it   # 1
.venv\Scripts\python.exe -m src.cli instagram account-status --page motivational_it   # 2 (business?)
.venv\Scripts\python.exe -m src.cli generate --page motivational_it --count 1          # 3 media (Reel 9:16)
.venv\Scripts\python.exe -m src.cli schedule --days 1                                  # crea i job
.venv\Scripts\python.exe -m src.cli worker --once                                      # 4 prepara media (test: NON pubblica)
# 5-8: upload di prova diretto, si ferma prima della pubblicazione
.venv\Scripts\python.exe -m src.cli instagram upload-test --page motivational_it --file generated\stories\<video>.mp4 --no-publish
# 9: pubblicazione manuale esplicita del job, poi 10: verifica del media_id
.venv\Scripts\python.exe -m src.cli instagram publish-job --page motivational_it --job <JOB_ID> --confirm
```

Solo dopo una prova reale riuscita su un account **business di test** il sistema
va considerato pronto per la produzione.

---

## Licenza

Codice: MIT (vedi header). I **contenuti** (frasi, citazioni) e la **musica** hanno
licenze proprie: rispetta le fonti indicate in `datasets/` e `assets/music/catalog.json`.
Le citazioni usano autori di **pubblico dominio** con attribuzione verificata.
```
