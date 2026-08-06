# Runbook di produzione

Da qui in avanti serve solo inserire le credenziali Meta ed eseguire il canary
guidato. Tutto il resto è già fatto e verificabile con un comando.

---

## 1. Le variabili da compilare

Copia `.env.example` in `.env` e riempi **queste tredici righe**. Nessun'altra è
obbligatoria.

```
META_APP_ID=
META_APP_SECRET=
META_GRAPH_API_VERSION=v25.0

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
```

Requisiti degli account: **Instagram professional Business**, collegati all'app
Meta, con i permessi `instagram_business_basic` e
`instagram_business_content_publish`. I token devono essere **long-lived**
(60 giorni).

`ICE_MODE` resta `dry_run`. Non cambiarlo adesso: il canary lo alza da solo per
il tempo strettamente necessario e lo rimette a posto.

`.env` è escluso da Git e lo scanner fallisce se qualcuno lo aggiunge.

---

## 2. I comandi, in ordine

### Preflight

```powershell
.\scripts\preflight.ps1
```

Senza credenziali elenca solo le variabili mancanti ed esce con codice 2 — è
l'esito atteso, non un errore. Con le credenziali controlla account, token,
scadenze, versione Graph API, ComfyUI, FFmpeg, disco, database, corpus e
segreti. Non pubblica nulla e non stampa mai un token.

### Canary: un solo post, su una sola pagina

```powershell
.\scripts\go_live_canary.ps1 -WhatIf     # tutto tranne media_publish
.\scripts\go_live_canary.ps1
```

Carica davvero un Reel su `pensiero_essenziale_it`, attende l'elaborazione, si
ferma **prima** di pubblicare, ti mostra account, didascalia e file, e pubblica
solo dopo che scrivi `PUBBLICA`. Una singola chiamata `media_publish`.

### Armare le pagine

```powershell
.\scripts\arm_page.ps1 pensiero_essenziale_it     # dopo aver visto il post
.\scripts\arm_all_pages.ps1                       # dopo una settimana pulita
```

`ICE_MODE=production` **da solo non pubblica niente**. Ogni pagina ha anche un
interruttore persistente, e nasce disarmata. È la difesa contro l'errore più
costoso possibile: cinque account che partono insieme.

### Avviare, controllare, fermare

```powershell
.\scripts\install_worker.ps1     # attività pianificata (logon + recupero giornaliero)
.\scripts\start_worker.ps1       # in primo piano; -Once per un solo ciclo
.\scripts\status.ps1             # modalità, armamento, job, worker
.\scripts\stop_worker.ps1        # ferma subito; -Disarm disarma anche le pagine
.\scripts\rollback.ps1           # ferma, disarma, riporta a dry_run
```

`rollback.ps1` non può cancellare un post già pubblicato — l'API non lo
consente — ma garantisce che non ne esca un altro e ti elenca cosa è uscito.

---

## 3. Che cosa è già verificato

| Verifica | Comando | Esito |
|---|---|---|
| Corpus | `corpus-final-gate` | 5.000/5.000 pronti, 12 contatori a zero |
| Ciclo completo | `production-readiness --from 2026-08-07 --days 1000 --all-pages` | 5.000 job, 0 scoperti, 0 duplicati |
| Dataset | `validate-datasets` | 0 errori bloccanti |
| Ripetitività | `editorial-stats --strict` | 0 segnalazioni |
| Segreti | `security-check` | nessuno |
| Media | `media-audit --buffer --all` | conformi alle specifiche Meta |
| Versione API | `tools/check_meta_api_version.py` | v25.0 supportata fino al 2028-07-29 |

Rilanciali quando vuoi: sono deterministici e non toccano la rete, tranne il
controllo della versione API.

---

## 4. Manutenzione

| Cadenza | Cosa |
|---|---|
| Settimanale | `.\scripts\status.ps1`; job in `NEEDS_REVIEW` |
| Mensile | `instagram health-check --all`; backup di `database/content.sqlite` |
| Ogni 45 giorni | rinnovo dei token long-lived **prima** della scadenza |
| Semestrale | `audit-sources --refresh --apply`, poi `verify-corpus` e `corpus-final-gate` |
| Automatica | il workflow *Meta API watch* avvisa quando la versione si avvicina alla fine del supporto |

Il rinnovo automatico del token **non** è implementato: `health-check` avvisa
con dieci giorni di anticipo e il worker si ferma prima della scadenza invece di
accumulare errori. Un rinnovo automatico non provato su un account reale
rischierebbe di invalidare token funzionanti.

---

## 5. Se qualcosa va storto

| Sintomo | Causa probabile | Cosa fare |
|---|---|---|
| Job in `NEEDS_REVIEW` con "non è armata" | la pagina non è stata armata | `.\scripts\arm_page.ps1 <pagina>` |
| Job in `NEEDS_REVIEW` con "nessun contenuto approvato" | il corpus è cambiato senza ri-verifica | `verify-corpus` poi `corpus-final-gate` |
| Nessun media generato | ComfyUI spento | avvialo; in produzione il fallback è vietato di proposito |
| `upload error` ripetuto | token scaduto | `instagram health-check --page <pagina>`, poi sostituisci il token in `.env` |
| Tutto fermo dopo un riavvio | lock o attività pianificata disabilitata | `.\scripts\status.ps1` |
| Un post sbagliato è uscito | — | eliminalo dall'app, poi `.\scripts\rollback.ps1` |

---

## 6. Limiti dichiarati

- **Nessuna chiamata Meta autenticata è mai stata eseguita.** Il client è
  verificato contro la documentazione ufficiale e con mock; la prima prova reale
  è il canary.
- Il corpus è verificato **automaticamente a partire dalle fonti**
  (`verification_executor: automated_source_first`), non da una persona che ha
  letto cinquemila schede. Ogni elemento porta la prova con cui è stato
  costruito: `evidence_summary`, `source_url`, `source_title`,
  `source_checked_at` e un hash che lo lega al proprio testo.
- Le fonti sono in larga parte `general_encyclopedia` (Wikipedia) e
  `authoritative_reference` (Treccani). Per un uso divulgativo è adeguato; per
  un'affermazione forte serve una fonte più solida, e le affermazioni forti sono
  state escluse in fase di costruzione invece di essere sostenute male.
- Il sistema richiede il PC acceso all'orario di pubblicazione;
  `missed_job_policy: publish_within_window` recupera entro 240 minuti.
