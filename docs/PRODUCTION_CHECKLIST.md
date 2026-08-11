# Checklist di produzione

Da completare **prima** di impostare `ICE_MODE=production`. Ogni voce ha un
comando che la verifica: se un comando esce con codice diverso da zero, la voce
non è soddisfatta.

---

## 0. Gli otto sbarramenti

Nessuno di questi è facoltativo, e vanno **in quest'ordine**. Gli ultimi due
esistono perché la tentazione di saltarli è forte e le conseguenze ricadono su
account reali.

| # | Sbarramento | Come si verifica |
|---|---|---|
| 1 | **CI verde** sul commit che stai per mettere in produzione | la spunta accanto al commit su GitHub, non un'esecuzione locale |
| 2 | **Graph API aggiornata** e coerente ovunque | `pytest tests/unit/test_meta_api_version.py` |
| 3 | **Security check verde** | `python -m src.cli security-check` |
| 4 | **Almeno un account di prova verificato** | `instagram health-check --page <id>` |
| 5 | **Upload resumable riuscito senza pubblicare** | `instagram upload-test --no-publish` |
| 6 | **Una sola pubblicazione reale, controllata** | `instagram publish-job --confirm`, guardata a occhio |
| 7 | **Revisione editoriale** con abbastanza contenuti pronti | `python -m src.cli status` |
| 8 | **Nessuna attivazione in massa** al primo tentativo | una pagina, poi una settimana, poi la successiva |

Sul punto 7: la produzione pubblica soltanto contenuti `production_ready`. Se ne
hai 482, hai 482 giorni-pagina di contenuto pubblicabile, non 5.000. Un giorno il
cui indice punta a un contenuto non pronto **si blocca** in `NEEDS_REVIEW`, non
ripiega su un altro contenuto. Rivedi il numero di contenuti pronti prima di
attivare una pagina, non dopo.

---

## A. Ambiente

- [ ] Python ≥ 3.11 e dipendenze installate
      → `.venv\Scripts\python.exe -m src.cli validate`
- [ ] FFmpeg risolto (bundle `imageio-ffmpeg` o `ICE_FFMPEG_PATH`)
      → riga *FFmpeg* del comando precedente
- [ ] Un font TTF decente in `assets/fonts/` (Inter, EB Garamond o equivalenti)
      → riga *Fonts*; senza font di sistema il rendering degrada
- [ ] Fuso orario delle pagine corretto (`publishing.timezone`)
- [ ] Spazio su disco: almeno **5 GB** liberi per il buffer dei media

## B. Modello locale

- [ ] Checkpoint installato e checksum registrato
      → `scripts\install_local_model.ps1`
- [ ] ComfyUI raggiungibile
      → `python -m src.cli comfyui status`
- [ ] **Generazione reale riuscita** (non il fallback)
      → `python -m src.cli comfyui test-generation --page pensiero_essenziale_it`
      deve uscire con codice `0` e stampare `sorgente=comfyui`
- [ ] Avvio automatico di ComfyUI configurato (`ICE_COMFYUI_LAUNCH_BAT`)

## C. Contenuti

- [ ] I cinque dataset validano senza errori bloccanti
      → `python -m src.cli validate-datasets`
- [ ] Le fonti citate esistono davvero
      → `python -m src.cli audit-sources`; i link rotti restano bloccati
- [ ] Nessun contenuto **pubblicabile** ha una fonte rotta o mai controllata
      → invariante bloccante di `validate-datasets` e
        `test_no_publishable_item_has_an_unverified_source`
- [ ] Contenuti `production_ready` sufficienti per il periodo che vuoi coprire
      → `python -m src.cli status`; ricorda che un giorno non coperto si blocca
- [ ] Le pagine non appaiono generate da uno script
      → `python -m src.cli editorial-stats --strict`
- [ ] Campione editoriale letto da una persona
      → `python -m src.cli editorial-sample --per-dataset 100 --seed <nuovo>`,
        poi `apply-review` con i verdetti
- [ ] Campione rivisto a occhio
      → `python -m src.cli sample-review --count 25` e apri
        `reports/sample_review.html`
- [ ] Anteprime grafiche riviste
      → `python -m src.cli preview-pages --per-page 5 --comfyui`

## D. Pagine

- [ ] Cinque YAML presenti e caricati
      → `python -m src.cli pages` elenca cinque righe
- [ ] Orari sfalsati e coerenti con il pubblico
      (08:30 / 11:00 / 13:30 / 17:00 / 20:30 nella configurazione predefinita)
- [ ] `publish_story: false` se non vuoi ancora le Storie
- [ ] `cycle_anchor_date` impostata in modo consapevole per ogni pagina
- [ ] Watermark (`visual.logo_text`) corrispondente all'handle reale

## E. Credenziali e sicurezza

- [ ] `.env` compilato con i cinque `IG_USER_ID` e i cinque `ACCESS_TOKEN`
- [ ] `META_APP_ID` e `META_APP_SECRET` presenti — **facoltativi per
      pubblicare**, necessari per `debug_token` (host `graph.facebook.com`), che
      è l'unico modo di leggere scadenza e permessi del token. Senza,
      `health-check` resta in avviso e il preflight del primo go-live si ferma.
- [ ] `.env` **non** tracciato da Git
      → `git ls-files .env` non restituisce nulla
- [ ] Nessun segreto nei file tracciati
      → `python -m src.cli security-check` esce con codice `0`
- [ ] Health check di tutte le pagine
      → `python -m src.cli instagram health-check --all`
      - credenziali presenti
      - token valido
      - scadenza nota con margine (> 10 giorni)
      - account raggiungibile e di tipo **business**
      - `upload_method` uguale a `resumable`

## F. Account Instagram

- [ ] Cinque account **professional Business** (non Creator, non personali)
- [ ] Ogni account collegato all'app Meta e alle sue autorizzazioni
      (`instagram_business_basic`, `instagram_business_content_publish`)
- [ ] Token **long-lived** (60 giorni) e procedura di rinnovo pianificata
- [ ] Nessun account con restrizioni o segnalazioni attive
- [ ] Limite di pubblicazione 24 h ampiamente sotto soglia
      (servono 5 post/giorno contro un limite documentato di 100)

## G. Prova end-to-end in `test`

Da eseguire **in ordine**, su un account di prova:

```powershell
$env:ICE_MODE = "test"
python -m src.cli instagram health-check --all
python -m src.cli schedule --days 1
python -m src.cli worker --once                 # prepara i media, NON pubblica
python -m src.cli instagram upload-test --page pensiero_essenziale_it `
      --file generated\stories\<video>.mp4 --no-publish
python -m src.cli instagram publish-job --page pensiero_essenziale_it `
      --job <JOB_ID> --confirm
```

- [ ] `upload-test` completa container + upload senza pubblicare
- [ ] `publish-job --confirm` produce un `media_id` reale
- [ ] Il post appare su Instagram con l'aspetto atteso
- [ ] La didascalia è corretta e gli hashtag sono quelli previsti

## H. Test automatici

- [ ] Suite completa verde
      → `python -m pytest -q`
- [ ] **CI verde sul commit in produzione** — la spunta su GitHub, non il
      risultato locale. Un test che gira solo sulla tua macchina non è una prova
      che il repository sia sano.
- [ ] Dry-run riproducibile delle cinque pagine
      → `python -m src.cli preproduction-smoke-test --date <YYYY-MM-DD>`
      (cinque reel, zero Storie, cinque pagine, idempotente)
- [ ] Video conformi alle specifiche Meta
      → `python -m src.cli media-audit`
- [ ] Nessuna Story pianificata con la configurazione predefinita
- [ ] Un solo worker può acquisire il lock

## I. Operatività

- [ ] Task Scheduler registrato
      → `scripts\register_task_scheduler.ps1`
- [ ] Il task ha `StartWhenAvailable` (recupero dopo PC spento)
- [ ] `missed_job_policy` scelta consapevolmente
      (predefinita: `publish_within_window`, finestra 240 minuti)
- [ ] Log ruotati e leggibili
      → `logs/engine.log`, `logs/worker_*.out.log`
- [ ] Backup pianificato di `database/content.sqlite`, `accounts/`, `datasets/`
- [ ] Dashboard raggiungibile solo su `127.0.0.1` con token
      → `python -m src.cli dashboard`

## J. Passaggio alla produzione

```powershell
# in .env
ICE_MODE=production
```

poi riavvia il worker:

```powershell
scripts\stop_worker.ps1
Start-ScheduledTask -TaskName InstagramContentEngineWorker
```

Nelle prime 48 ore controlla:

```powershell
python -m src.cli status
python -m src.cli export-report
Get-Content logs\engine.log -Tail 80
```

---

## Cosa NON deve essere vero

- [ ] Nessuna pagina con `upload_method: hosted_url`
      (il resumable non richiede alcun hosting pubblico)
- [ ] `ICE_PUBLIC_MEDIA_BASE_URL` **non** impostato
- [ ] Nessuna porta aperta verso l'esterno
- [ ] Nessuna chiave o token in file tracciati da Git
- [ ] Nessuna dipendenza da Claude o da altre API LLM nel funzionamento quotidiano
- [ ] Nessun contenuto **pubblicabile** senza fonte verificata da una persona
- [ ] `comfyui.allow_fallback_in_production` a `false`
- [ ] Nessun artefatto runtime tracciato da Git (database, log, report, cache,
      file di modello) → `python -m src.cli security-check`
- [ ] Cinque pagine attivate tutte insieme al primo tentativo

## Manutenzione ricorrente

| Cadenza | Attività |
|---|---|
| Settimanale | `python -m src.cli status`; controllo job in `NEEDS_REVIEW` |
| Mensile | `python -m src.cli instagram health-check --all`; backup del database |
| Ogni 45 giorni | rinnovo dei token long-lived prima della scadenza |
| Semestrale | revisione dei link delle fonti; aggiornamento `verified_at` |
| A ogni aggiornamento | `git pull` → `init-db` → `validate-datasets` → `pytest -q` |
