# Checklist di produzione

Da completare **prima** di impostare `ICE_MODE=production`. Ogni voce ha un
comando che la verifica: se un comando esce con codice diverso da zero, la voce
non è soddisfatta.

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
- [ ] 5.000 contenuti importati e approvati
      → `python -m src.cli status` (5.000 in `approved_for_publication`)
- [ ] Nessun contenuto fattuale approvato senza fonte
      → verificato dal validatore e dai test `test_fact_checked_datasets_*`
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
- [ ] `META_APP_ID` e `META_APP_SECRET` presenti (servono per `debug_token`)
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
- [ ] Dry-run di un giorno → esattamente 5 pubblicazioni principali
- [ ] Dry-run di sette giorni → esattamente 35 job principali
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
- [ ] Nessun contenuto fattuale approvato senza fonte
- [ ] `comfyui.allow_fallback_in_production` a `false`

## Manutenzione ricorrente

| Cadenza | Attività |
|---|---|
| Settimanale | `python -m src.cli status`; controllo job in `NEEDS_REVIEW` |
| Mensile | `python -m src.cli instagram health-check --all`; backup del database |
| Ogni 45 giorni | rinnovo dei token long-lived prima della scadenza |
| Semestrale | revisione dei link delle fonti; aggiornamento `verified_at` |
| A ogni aggiornamento | `git pull` → `init-db` → `validate-datasets` → `pytest -q` |
