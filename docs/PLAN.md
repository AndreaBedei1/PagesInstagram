# Piano Tecnico — Instagram Content Engine

> Documento vivo. Aggiornato durante lo sviluppo. Le sezioni "Stato" riflettono
> ciò che è **realmente** implementato e testato, non ciò che è pianificato.

## 1. Analisi dell'ambiente (rilevata il 2026-07-21)

| Elemento | Stato reale | Note operative |
|---|---|---|
| Directory progetto | `C:\Users\andrea.bedei3\Desktop\Instagram` (era vuota) | Nuovo repository |
| OS | Windows 11 Enterprise 26200 | Target unico: Windows |
| Python | 3.14.2 (`C:\Python314\python.exe`) | Molto recente → si usano solo dipendenze con wheel; niente torch/scikit nel motore |
| Gestione ambiente | `uv` 0.11.28 (scelto), `conda` 25.11.1 | `.venv` creato con `uv`; install verificata, tutte le wheel presenti |
| GPU | **NVIDIA RTX 6000 Ada (48 GB)** | Ampiamente sufficiente per ComfyUI/SD |
| FFmpeg di sistema | **ASSENTE** | Si usa il binario incluso in `imageio-ffmpeg` (ffmpeg 7.1). Nessun install manuale richiesto |
| ComfyUI | Installato in `F:\AI\ComfyUI`, **da avviare** | Launcher `F:\AI\start_comfyui.bat`, env `F:\AI\envs\immagini\python.exe`, porta 8188 |
| Modelli ComfyUI | `DreamShaper_8_pruned.safetensors` (**SD 1.5**) | Unico checkpoint; workflow txt2img SD1.5. Output in `F:\AI\output` |
| git | 2.52.0 | Branch `feature/instagram-content-engine` |

**Assunzioni documentate:**
- A1. Si usa `uv` + `.venv` (più veloce e riproducibile di venv puro; più leggero di conda per il solo motore). ComfyUI mantiene il proprio env separato `immagini`.
- A2. La generazione video usa il ffmpeg incluso in `imageio-ffmpeg` (nessuna dipendenza di sistema). Se l'utente installa ffmpeg di sistema, viene preferito quello se `ICE_FFMPEG_PATH` è impostato.
- A3. Background con SD 1.5 (DreamShaper 8): si genera a risoluzione nativa (es. 640×800 / 576×1024) e si effettua upscale deterministico con Pillow al formato finale (1080×1350 / 1080×1920). Nessun testo generato dal modello.
- A4. Nessuna dipendenza pesante (torch/scikit/Java): la similarità semantica usa TF-IDF/char-n-gram + coseno (numpy) e RapidFuzz; la grammatica usa euristiche + indice Gulpease (adatto all'italiano). Backend semantico e grammaticale sono **plug-in**: se in futuro si installa `sentence-transformers` o `language-tool-python`, vengono usati automaticamente.

## 2. API ufficiali Meta — capacità e limitazioni REALI (fonti ufficiali)

Fonte: developers.facebook.com/docs/instagram-platform (Content Publishing, Overview).

**Flusso di pubblicazione (3 passi):**
1. `POST /{ig-user-id}/media` → crea un *container*. Parametri per tipo:
   - IMAGE: `image_url` (solo JPEG), `caption`, `alt_text`
   - VIDEO: `video_url`, `media_type=VIDEO`
   - REELS: `video_url`, `media_type=REELS`
   - **STORIES**: `image_url` **o** `video_url`, `media_type=STORIES`
   - CAROUSEL: `media_type=CAROUSEL`, `children=<id,id,...>` (max 10)
   Ritorna `{ "id": "<container-id>" }`.
2. `GET /{container-id}?fields=status_code` → `IN_PROGRESS | FINISHED | ERROR | EXPIRED | PUBLISHED`.
   Consigliato: polling 1×/minuto, max 5 minuti. Il container scade dopo 24 h.
3. `POST /{ig-user-id}/media_publish` con `creation_id=<container-id>` → ritorna `{ "id": "<media-id>" }`.

**Limiti e requisiti:**
- **Limite di pubblicazione: 100 post via API in una finestra mobile di 24 h** (carosello = 1). Verificabile con `GET /{ig-user-id}/content_publishing_limit`. Il nostro fabbisogno (1/giorno/pagina) è ampiamente sotto soglia.
- **Account**: deve essere un *Instagram professional account* (Business o Creator).
- **Due configurazioni API**:
  - *Instagram API with Instagram Login* → base URL `graph.instagram.com`, permessi `instagram_business_basic`, `instagram_business_content_publish`. Non richiede Pagina Facebook. **Scelta consigliata** per questo progetto.
  - *Instagram API with Facebook Login* → base URL `graph.facebook.com`, permessi `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`. Richiede Pagina Facebook collegata.
- **Token**: short-lived (1 ora) → long-lived (**60 giorni**, rinnovabile prima della scadenza). Il motore controlla la scadenza e supporta il rinnovo.
- **Le URL dei media devono essere PUBBLICHE (https)**: la Graph API scarica il media da un URL; **non** accetta file locali/upload diretto per immagini. Serve un hosting pubblico della cartella `generated/` (vedi README → `ICE_PUBLIC_MEDIA_BASE_URL`). Questo è un **blocco reale** per la pubblicazione in produzione (richiede hosting + token).

**LIMITAZIONI CRITICHE su Stories e Musica (confermate):**
- ❗ **Le Stories via API NON supportano musica, sticker, link o audio di tendenza dell'app.** Nessun brano dalla libreria musicale Instagram è accessibile via API.
- ❗ **I Reels via API NON hanno accesso a suoni di tendenza/catalogo Instagram.** Se un Reel/Storia deve avere musica, **la musica va incorporata nel file video prima dell'upload.**
- ➡️ **Conseguenza architetturale (già prevista dalla specifica):** la musica è una libreria **locale con licenza**, montata nel video con FFmpeg (fade-in/out, normalizzazione) e *bakizzata* nel file. È l'unico modo conforme e ufficiale. Nessun uso di Selenium/automazioni non ufficiali.
- Requisiti tecnici video (container): MP4/MOV, `moov atom` all'inizio (`+faststart`), no edit list; audio **AAC 48 kHz**, 1–2 canali; video **H.264** progressivo, closed GOP, 23–60 FPS. Il generatore video rispetta questi vincoli.

**Modalità operative** (per non pubblicare durante lo sviluppo): `dry_run` (default, nessuna chiamata di pubblicazione) → `test` (pubblica solo su comando esplicito con account di test) → `production`.

## 3. Architettura

Motore unico multi-account. Nessuna duplicazione per pagina: una pagina = 1 file YAML in `accounts/` + contenuti nel DB + eventuali template/musica.

```
Config (YAML+ENV) ─┐
                   ├─► Core (settings, paths, logging, registry pagine)
Dataset (importer) ┤
                   ├─► Content service  ─► dedup + quality (score)
ComfyUI client ────┤            │
                   ├─► Rendering (Pillow) ─► Quality validator (contrasto, margini, ...)
Music library ─────┤            │
                   ├─► Video (ffmpeg: Ken Burns + musica bakizzata + faststart)
                   │            │
Scheduler/worker ──┼─► Publication jobs (state machine, idempotente, retry/backoff)
                   │            │
Publishing client ─┴─► Meta Graph API (dry_run|test|production) + mock
CLI / Dashboard ──────► operatività e review
```

**State machine job** (idempotente, una sola pubblicazione):
`DRAFT → VALIDATED → BACKGROUND_GENERATED → RENDERED → MEDIA_READY → SCHEDULED → UPLOADING → CONTAINER_CREATED → PUBLISHING → PUBLISHED` con rami `FAILED / RETRY_PENDING / SKIPPED / REJECTED / NEEDS_REVIEW`.

## 4. Modello dati (SQLite, migrazioni versionate)

`sqlite3` stdlib (nessuna dipendenza ORM) + runner di migrazioni numerate in `src/database/migrations/`.
Tabelle: `schema_migrations`, `pages`, `contents`, `media_assets`, `publication_jobs`, `publication_logs`, `music_tracks`, `music_usage`. Campi come da specifica (§4). I token **non** sono mai salvati nel DB né nei log.

## 5. Ordine di sviluppo e stato

| Fase | Contenuto | Stato |
|---|---|---|
| 1 | Analisi, architettura, config, DB schema, README iniziale | ✅ fatto |
| 2 | Dataset motivazionale (100) + citazioni verificate (82), dedup, qualità | ✅ fatto+testato |
| 3 | Client ComfyUI, workflow SD1.5, retry, metadati | ✅ fatto+testato LIVE (RTX 6000) |
| 4 | Rendering deterministico post/story, template, autore | ✅ fatto+verificato visivamente |
| 5 | Validazione qualità immagine (contrasto/margini/score) + auto-fix | ✅ fatto+testato |
| 6 | Libreria musicale + metadati/licenze + mood matching + video ffmpeg | ✅ fatto+video reale validato |
| 7 | Scheduler, worker persistente, recovery, Task Scheduler | ✅ fatto (Task Scheduler: script ok, registrazione richiede sessione utente) |
| 8 | Client pubblicazione Meta + mock + dry-run + idempotenza/retry | ✅ fatto+testato (mock) |
| 9 | Dashboard locale (review/approvazione) | ✅ fatto+testato |
| 10 | Verifica finale: ambiente pulito, test, pipeline dry-run, docs | ✅ in corso |

## 6. Punti che richiedono credenziali/risorse reali (blocchi noti)

- **Pubblicazione reale**: token long-lived Meta + IG user id + hosting pubblico dei media (`ICE_PUBLIC_MEDIA_BASE_URL`). Finché mancano → tutto testato in `dry_run`/mock; comando reale pronto e documentato.
- **Musica reale**: file audio con licenza compatibile (non inclusi nel repo). La pipeline è testata con toni sintetici generati localmente + metadati/licenze registrati.
- **ComfyUI**: reale e disponibile su questo PC; il client lo avvia e lo interroga. In CI/ambienti senza GPU si usa uno sfondo di test deterministico.
