# Installazione e gestione delle cinque pagine

Guida operativa completa: da repository appena clonato a produzione.
Tutti i comandi presuppongono la radice del progetto come directory corrente e
`.venv\Scripts\python.exe` come interprete (su Linux/macOS `.venv/bin/python`).

---

## 1. Installare il progetto

```powershell
scripts\bootstrap_five_pages.ps1
```

Lo script porta un'installazione pulita fino al dry-run funzionante:
dipendenze → `.env` → validazione ambiente → database → validazione dataset →
import contenuti → controllo segreti → buffer → dry-run → anteprime.
È idempotente: può essere rieseguito quando serve.

Equivalente manuale:

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe -m src.cli validate
.venv\Scripts\python.exe -m src.cli init-db
.venv\Scripts\python.exe -m src.cli validate-datasets
.venv\Scripts\python.exe -m src.cli import-content
.venv\Scripts\python.exe -m src.cli security-check
```

## 2. Installare il modello locale

```powershell
scripts\install_local_model.ps1 -ComfyUIRoot "F:\AI\ComfyUI"
```

Dettagli, licenza e checksum: [LOCAL_MODEL_SETUP.md](LOCAL_MODEL_SETUP.md).

## 3. Avviare ComfyUI

L'engine parla con ComfyUI tramite le sue API locali (`/prompt`, `/history`,
`/view`). Con `comfyui.auto_start: true` e `ICE_COMFYUI_LAUNCH_BAT` impostato,
l'engine avvia ComfyUI da solo e attende che sia pronto.

```powershell
$env:ICE_COMFYUI_URL       = "http://127.0.0.1:8188"
$env:ICE_COMFYUI_LAUNCH_BAT= "F:\AI\start_comfyui.bat"
.venv\Scripts\python.exe -m src.cli comfyui status
```

Verifica che il modello generi davvero (fallisce se ricade sul fallback):

```powershell
.venv\Scripts\python.exe -m src.cli comfyui test-generation --page pensiero_essenziale_it
```

## 4. Cambiare nomi, handle e orari delle pagine

Ogni pagina è **un solo file YAML** in `accounts/`. Nessun codice da toccare.

| Cosa cambiare | Campo |
|---|---|
| Nome mostrato | `display_name` |
| Watermark sull'immagine | `visual.logo_text` (e `visual.logo_enabled`) |
| Orario di pubblicazione | `publishing.feed_time` |
| Fuso orario | `publishing.timezone` |
| Attivare le Storie | `publishing.publish_story: true` (+ `story_time`) |
| Sospendere una pagina | `enabled: false` |
| Stile dello sfondo | `visual.background_profile` |
| Template tipografico | `visual.template` |

Dopo una modifica: `python -m src.cli init-db` (riallinea la tabella `pages`).

> **Attenzione:** cambiare `page_id` cambia anche il prefisso delle variabili
> d'ambiente (`ICE_<PAGE_ID_MAIUSCOLO>_...`) e la chiave di idempotenza dei job.

## 5. Dove inserire account ID e token

Esclusivamente in `.env` (mai negli YAML, mai nel database, mai nei log):

```
ICE_PENSIERO_ESSENZIALE_IT_IG_USER_ID=
ICE_PENSIERO_ESSENZIALE_IT_ACCESS_TOKEN=
ICE_CURIOSITA_MONDO_IT_IG_USER_ID=
ICE_CURIOSITA_MONDO_IT_ACCESS_TOKEN=
ICE_PAROLA_GIORNO_IT_IG_USER_ID=
ICE_PAROLA_GIORNO_IT_ACCESS_TOKEN=
ICE_OGGI_NELLA_STORIA_IT_IG_USER_ID=
ICE_OGGI_NELLA_STORIA_IT_ACCESS_TOKEN=
ICE_DOMANDA_GIORNO_IT_IG_USER_ID=
ICE_DOMANDA_GIORNO_IT_ACCESS_TOKEN=
META_APP_ID=
META_APP_SECRET=
```

`IG_USER_ID` è l'id numerico dell'account professional; `ACCESS_TOKEN` è un
token **long-lived** (60 giorni, rinnovabile).

## 6. Inizializzare il database

```powershell
.venv\Scripts\python.exe -m src.cli init-db
```

Crea o aggiorna lo schema applicando le migrazioni in `src/database/migrations/`
e registra le cinque pagine. Nessun token viene mai scritto nel database.

## 7. Validare i dataset

```powershell
.venv\Scripts\python.exe -m src.cli validate-datasets
```

Exit code diverso da zero se anche un solo requisito non è rispettato. Il report
JSON finisce in `reports/datasets_validation.json`. Schema e regole:
[DATASET_SCHEMA.md](DATASET_SCHEMA.md).

## 8. Generare le anteprime

```powershell
.venv\Scripts\python.exe -m src.cli preview-pages --per-page 5
.venv\Scripts\python.exe -m src.cli sample-review --count 25
```

- `reports/previews/index.html` — cinque rendering reali per pagina, affiancati.
- `reports/sample_review.html` — campione di 25 contenuti per pagina con fonti.

Aggiungi `--comfyui` a `preview-pages` per usare gli sfondi reali (più lento).

## 9. Impostare la data iniziale del ciclo

In ogni YAML:

```yaml
content:
  selection_policy: cyclic_ordered
  cycle_anchor_date: "2026-01-01"
  cycle_length: 1000
```

`cycle_anchor_date` è il **giorno 0**: quel giorno viene pubblicato
`sequence_index = 0`. Spostare l'ancora sposta l'intero calendario editoriale.
Dettagli: [CONTENT_ROTATION.md](CONTENT_ROTATION.md).

## 10. Preparare il buffer

```powershell
.venv\Scripts\python.exe -m src.cli schedule --days 60
.venv\Scripts\python.exe -m src.cli worker --once
```

Il worker mantiene automaticamente 60 giorni di job pianificati e 30 giorni di
media già generati (`generation.*` nello YAML della pagina).

## 11. Eseguire il dry-run

```powershell
$env:ICE_MODE = "dry_run"
.venv\Scripts\python.exe -m src.cli schedule --days 7
.venv\Scripts\python.exe -m src.cli worker --once
.venv\Scripts\python.exe -m src.cli status
```

In `dry_run` non parte alcuna chiamata di rete verso Meta: il job diventa
`PUBLISHED` con un `remote_media_id` che inizia per `DRYRUN-`.

## 12. Attivare la produzione

Prima esegui l'intera [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md), poi:

```powershell
# in .env
ICE_MODE=production
```

oppure, solo per la sessione corrente:

```powershell
$env:ICE_MODE = "production"
.venv\Scripts\python.exe -m src.cli worker
```

In `production` il fallback dello sfondo è **disabilitato**: se ComfyUI non
risponde, il job va in `NEEDS_REVIEW` invece di pubblicare media degradati.

## 13. Verificare i token

```powershell
.venv\Scripts\python.exe -m src.cli instagram health-check --all
```

Controlla, senza pubblicare nulla e senza stampare mai un token: presenza delle
credenziali, validità del token, scadenza nota (con preavviso), raggiungibilità
dell'account, tipo di account, limite di pubblicazione 24 h e metodo di upload.
Exit code: `0` tutto verde, `1` almeno un errore, `2` solo avvisi.

## 14. Fermare il worker

```powershell
scripts\stop_worker.ps1                     # stop controllato tramite il lock
Stop-ScheduledTask -TaskName InstagramContentEngineWorker
scripts\unregister_task_scheduler.ps1       # rimuove l'avvio automatico
```

## 15. Riprendere dopo un crash

Il worker è progettato per essere riavviato in qualsiasi momento:

1. i job sono idempotenti sulla chiave `page:media_type:data`;
2. all'avvio `recover()` elenca i job in volo e il publisher li riprende
   riusando `container_id` e `upload_offset` già salvati;
3. il buffer viene ricostruito automaticamente al primo tick;
4. l'assegnazione contenuto/giorno è deterministica: lo stesso giorno produce
   sempre lo stesso contenuto anche se il database viene ricreato.

```powershell
.venv\Scripts\python.exe -m src.cli status
.venv\Scripts\python.exe -m src.cli retry-failed
.venv\Scripts\python.exe -m src.cli worker --once
```

## 16. Eseguire i backup

Da salvare (in questo ordine di importanza):

```powershell
Copy-Item database\content.sqlite "backup\content_$(Get-Date -Format yyyyMMdd).sqlite"
Copy-Item -Recurse accounts  backup\accounts
Copy-Item -Recurse datasets  backup\datasets
Copy-Item .env "backup\.env"      # in un luogo sicuro e NON nel repository
```

I media in `generated/` sono rigenerabili e non vanno nei backup.

## 17. Aggiornare i dataset

I file JSON sono generati dalle sorgenti in `tools/dataset_build/`:

```powershell
# 1. modifica/aggiungi voci in tools/dataset_build/data/<pagina>/part_NN.py
python tools\build_datasets.py            # rigenera i cinque JSON
python -m src.cli validate-datasets       # deve terminare con exit code 0
python -m src.cli import-content          # importa solo le novità (dedup)
```

L'import è incrementale: i contenuti già presenti vengono riconosciuti come
duplicati esatti e saltati.

## 18. Cambiare checkpoint senza toccare il codice

```
ICE_COMFYUI_MODEL_FAMILY=sdxl                 # sdxl | sd15
ICE_COMFYUI_CHECKPOINT=altro_modello.safetensors
ICE_COMFYUI_WORKFLOW=sdxl_background.json
```

Le stesse chiavi esistono in `config/settings.yaml` sotto `comfyui:`.

## 19. Perché Claude non serve a runtime

Claude è stato usato **solo** in fase di sviluppo e per preparare i dataset. A
regime il sistema non contatta alcun servizio di modelli linguistici:

- i contenuti sono file JSON versionati nel repository;
- la scelta del contenuto del giorno è aritmetica pura (modulo e rotazione);
- le didascalie sono precomposte nei dataset e assemblate localmente;
- gli sfondi sono generati da ComfyUI in locale;
- il testo è disegnato con Pillow;
- il video è prodotto da FFmpeg;
- la pubblicazione usa le API ufficiali Meta.

L'unico traffico in uscita è verso ComfyUI (localhost) e verso Meta.
