# Piano di implementazione — cinque pagine evergreen

Branch di lavoro: `feature/five-evergreen-pages` (da `fix/direct-meta-upload`).

Obiettivo: trasformare l'engine attuale (2 pagine dimostrative, selezione
"consuma-e-scarta") in un sistema **locale, deterministico e multi-account** che
gestisce **5 pagine evergreen**, un contenuto al giorno ciascuna, senza servizi
cloud, senza browser automation e **senza dipendere da Claude o da API LLM a
runtime**.

---

## 0. Vincoli non negoziabili

| Vincolo | Come è rispettato |
|---|---|
| Nessun hosting pubblico | Resta `upload_method: resumable` (direct upload a `rupload.facebook.com`) |
| Nessuna dipendenza LLM a runtime | I dataset sono JSON versionati; la selezione è aritmetica pura |
| Nessuna pubblicazione reale non voluta | `ICE_MODE=dry_run` default; `production` richiede intervento esplicito |
| Nessun segreto in Git | Solo `.env` (ignorato); comando `security-check`; test dedicati |
| Nessun fallback silenzioso in produzione | `allow_fallback_in_production: false` → job in `NEEDS_REVIEW` |

---

## 1. Architettura runtime a regime

```
datasets/*.json  ──import──►  contents (SQLite, sequence_index / calendar_key)
                                   │
              planner (60 gg)  ────┼──►  publication_jobs (idempotenti)
                                   │
   selezione deterministica per (page_id, data locale programmata)
                                   │
                ComfyUI SDXL locale → SOLO lo sfondo (seed deterministico)
                                   │
                     Pillow → testo (template per content_type)
                                   │
              validatore qualità (WCAG, margini, righe) + auto-riparazione
                                   │
                 FFmpeg → MP4 9:16 1080×1920, 8 s, H.264/yuv420p, faststart
                                   │
     worker → Meta resumable upload → REELS con share_to_feed=true
```

---

## 2. Componenti: cosa si estende, cosa si aggiunge

Nulla di corretto viene riscritto da zero. Elenco puntuale:

### Estesi
- `src/accounts/models.py` — nuovi blocchi `content.selection_policy`,
  `content.cycle_anchor_date`, `content.cycle_length`, `generation.*`,
  `visual.template`.
- `src/database/repositories.py` — nuove query per selezione ciclica/calendario,
  retention media, colonne nuove in `CONTENT_COLUMNS`.
- `src/content/importer.py` — cinque nuovi `content_type`, campi verificati,
  `sequence_index`, `calendar_key`, `metadata_json`.
- `src/content/service.py` — dispatch fra le policy di selezione.
- `src/rendering/renderer.py` — motore a blocchi + 5 template strutturati.
- `src/comfyui/workflow.py` + `backgrounds.py` — SDXL configurabile, profili
  sfondo per pagina, seed deterministico esteso.
- `src/scheduling/planner.py` / `worker.py` — orizzonte 60 gg, buffer 30 gg,
  retention, selezione basata sulla **data programmata** del job.
- `src/cli/main.py` — `validate-datasets`, `security-check`, `preview-pages`.
- `src/cli/instagram_cmds.py` — `health-check --all`.
- `src/video/builder.py` — traccia AAC silenziosa quando la musica è disattivata.

### Nuovi
- `src/content/selection.py` — aritmetica ciclica e calendario.
- `src/content/dataset_validation.py` — validatore dataset + report JSON.
- `src/security/scan.py` — scanner segreti sui file tracciati.
- `src/rendering/templates.py` — definizione dichiarativa dei 5 layout.
- `src/database/migrations/0003_evergreen_content.sql`.
- `comfyui/workflows/sdxl_background.json`.
- `scripts/install_local_model.ps1` / `.sh`, `scripts/bootstrap_five_pages.ps1`.
- `accounts/*.yaml` × 5; le due pagine demo vanno in `accounts/_archive/`.
- `datasets/*_it.json` × 5 (1.000 elementi ciascuno).

---

## 3. Selezione deterministica

```
giorni  = (data_locale_programmata - cycle_anchor_date).days
indice  = giorni mod 1000              # policy cyclic_ordered
ciclo   = giorni // 1000
```

- Stesso `page_id` + stessa data ⇒ **sempre** lo stesso contenuto.
- Indipendente da riavvii, ordine SQL, inserimenti successivi, retry, RANDOM().
- `cycle_number` entra nel seed dello sfondo ⇒ dopo 1.000 giorni il testo si
  ripete ma con **immagine nuova**.
- `calendar_rotating` (solo `today_in_history`): filtra per `calendar_key`
  `MM-DD` uguale alla data locale, ordina in modo stabile per `sequence_index`,
  sceglie `(anno - anno_ancora) mod n_eventi_di_quella_data`.

Seed sfondo:
```
sha256(page_id | content_id | scheduled_date | cycle_number | media_type | attempt)
```

---

## 4. Checklist di avanzamento

- [x] Lettura completa del repository, README, `docs/META_RESUMABLE_UPLOAD.md`
- [x] Verifica fonte ufficiale checkpoint SDXL (HF `stabilityai/stable-diffusion-xl-base-1.0`)
- [x] Verifica documentazione Meta corrente (resumable upload, versione Graph API)
- [x] Piano scritto
- [x] Migrazione `0003_evergreen_content.sql`
- [x] `src/content/selection.py` + policy nel modello di pagina
- [x] Cinque YAML pagine + archiviazione delle due pagine demo
- [x] Importer esteso ai cinque `content_type`
- [x] `validate-datasets` + report JSON
- [x] Cinque template di rendering realmente distinti
- [x] Workflow SDXL configurabile + script di installazione modello
- [x] Profili sfondo per le cinque pagine
- [x] Video 9:16 di 8 s con audio silenzioso di compatibilità
- [x] Buffer scorrevole (60 gg pianificati / 30 gg generati / retention 45 gg)
- [x] `security-check` + test anti-segreti
- [x] `instagram health-check --all`
- [x] Dataset `philosophical_thoughts_it.json` — 1.000
- [x] Dataset `world_curiosities_it.json` — 1.000
- [x] Dataset `words_of_the_day_it.json` — 1.000
- [x] Dataset `today_in_history_it.json` — 1.000
- [x] Dataset `daily_questions_it.json` — 1.000
- [x] Anteprime HTML per pagina + indice comparativo
- [x] Campione HTML di revisione (≥25 contenuti per pagina)
- [x] Suite di test (30 requisiti) verde
- [x] `bootstrap_five_pages.ps1` + script Windows aggiornati
- [x] Documentazione completa (`README` + 6 documenti nuovi)
- [x] Dry-run end-to-end delle cinque pagine
- [x] Verifica finale segreti + commit + push

---

## 4-bis. Esito delle verifiche finali (2026-08-05)

| Verifica | Comando | Esito |
|---|---|---|
| Suite di test | `pytest -q` | 135 test verdi |
| Ambiente | `src.cli validate` | uscita `0` |
| Dataset | `src.cli validate-datasets` | 5 × 1.000 = **5.000**, 0 errori bloccanti, 14 avvisi non bloccanti |
| Segreti | `src.cli security-check` | 193 file, nessun segreto, uscita `0` |
| Import | `src.cli import-content` | 5.000 aggiunti, 5.000 approvati |
| Pianificazione | `src.cli schedule --days 7` | 35 job (5/giorno, solo reel) |
| Buffer | `src.cli worker --once` | 150 media pronti (30 gg × 5 pagine) |
| Modello locale | `install_local_model.ps1` | `sd_xl_base_1.0.safetensors`, 6,46 GB, SHA-256 registrato |
| Generazione reale | `src.cli comfyui test-generation` | `sorgente=comfyui 768x1344` — **non** una mock |
| Anteprime SDXL reali | `src.cli preview-pages --comfyui` | 15 anteprime, `reports/previews_sdxl/index.html` |
| Dry-run cinque pagine | `src.cli worker --once` | `published=5 failed=0`, un reel per pagina |

Il dry-run delle cinque pagine è stato eseguito anticipando gli orari dei cinque
job del 2026-08-07 (su una copia di sicurezza del database, poi ripristinata):
tutti e cinque sono passati a `PUBLISHED` con `upload_method=resumable`, indice
ciclico 218 per le quattro pagine cicliche e chiave di calendario `08-07` per
`oggi_nella_storia_it`.

---

## 5. Rischi noti e mitigazioni

| Rischio | Mitigazione |
|---|---|
| ComfyUI non avviato in produzione | Fallback vietato ⇒ `NEEDS_REVIEW`, mai media degradati |
| Token Meta scaduto | `instagram health-check --all` segnala la scadenza in anticipo |
| PC spento all'orario | `missed_job_policy: publish_within_window` (240 min) |
| Disco pieno | Retention 45 gg sui media pubblicati; log ruotati |
| Dataset con fatti obsoleti | Solo contenuti evergreen; `verified_at` su ogni elemento |
| Doppia pubblicazione | Chiave di idempotenza `page:media_type:data` + lock del worker |
