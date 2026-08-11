# Runbook di produzione

Da qui in avanti serve solo inserire le credenziali Meta ed eseguire il canary
guidato. Tutto il resto è già fatto e verificabile con un comando.

---

## 1. Le variabili da compilare

Copia `.env.example` in `.env` e riempi **queste undici righe**. Sono le sole
necessarie per pubblicare.

```
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

E **due facoltative**, che il preflight non pretende:

```
META_APP_ID=
META_APP_SECRET=
```

Perché facoltative, e perché conviene comunque metterle. La documentazione
ufficiale è esplicita su chi usa cosa:

| Operazione | Host | Servono app id/secret? |
|---|---|---|
| pubblicare (`/media`, `/media_publish`) | `graph.instagram.com` | **no** |
| identità del token (`/me`) | `graph.instagram.com` | **no** |
| scadenza e permessi (`/debug_token`) | `graph.facebook.com` | **sì** |
| rinnovo a 60 giorni (`ig_refresh_token`) | `graph.instagram.com` | **no** |
| da breve a lungo (`ig_exchange_token`) | `graph.instagram.com` | **sì** (secret) |

Quindi: senza di esse si pubblica benissimo, ma nessuno può dire quanti giorni
di vita resti al token. `health-check` lo segnala come avviso, e per il primo
go-live un avviso sulle credenziali è bloccante — quindi in pratica, per il
primo avvio, mettile.

> **Sono le credenziali dell'app *Meta*, non quelle Instagram.** Il Dashboard
> ne mostra due coppie in due punti diversi, e non sono intercambiabili:
>
> | dove | quale | a che serve |
> |---|---|---|
> | *App settings → Basic* | App ID / App secret **Meta** | `debug_token` — scadenza e permessi |
> | *Instagram → API setup with Instagram login* | Instagram app ID / secret | `ig_exchange_token` — da token breve a long-lived |
>
> Passare l'ID dell'app Instagram a `debug_token` risponde «Error validating
> application. Cannot get application info due to a system error» (code 190):
> quell'ID non è un nodo di `graph.facebook.com`. Verificato su un account reale
> l'11 agosto 2026, insieme al fatto che `/me` e
> `content_publishing_limit` rispondono comunque 200 — pubblicare non dipende da
> nessuna delle due coppie.

### E anche con le credenziali giuste, la scadenza non si legge

Con le credenziali **Meta** corrette — `client_credentials` risponde 200, quindi
app e secret sono validi — `debug_token` risponde comunque `(#2) Service
temporarily unavailable`, su tentativi ripetuti e con entrambe le forme di
autenticazione. Non è transitorio: quell'endpoint non introspeziona un token di
Instagram Login.

Quindi **la scadenza non è leggibile da nessun endpoint**, e il preflight non
può pretenderla. L'unica fonte che resta sei tu: il Dashboard mostra la data in
cui hai generato il token, e la durata documentata è di 60 giorni.

```
ICE_<PAGINA>_TOKEN_ISSUED_AT=2026-08-11
```

Il sistema calcola il countdown da lì e blocca sotto i 10 giorni rimanenti — la
protezione che serviva resta intatta. Ma il dato è **dichiarato, non
verificato**, e `health-check` lo scrive a ogni esecuzione accanto al numero:

```
scadenza  ok (59 gg)
pensiero_essenziale_it: scadenza dichiarata, non verificata
pensiero_essenziale_it: permessi da sonda: il nodo di pubblicazione risponde
```

Anche i permessi, senza `debug_token`, non si leggono come elenco di scope: il
health check interroga `content_publishing_limit`, lo stesso nodo su cui vive
`/media`. Se risponde, il token arriva dove deve arrivare; se rifiuta, è un
fallimento, perché rifiuterebbe anche la pubblicazione. Anche questo è
etichettato per quello che è — una sonda, non un elenco.

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
.\scripts\preflight.ps1                # profilo canary (predefinito)
.\scripts\preflight.ps1 -Production    # quello che serve allo scheduler
```

Senza credenziali elenca solo le variabili mancanti ed esce con codice 2 — è
l'esito atteso, non un errore. Con le credenziali controlla account, token,
scadenze, permessi, versione Graph API, ComfyUI, buffer, disco, database,
corpus e segreti. Non pubblica nulla e non stampa mai un token.

**Sulle credenziali non esistono avvisi.** Il `health-check` esce 0, 1 o 2, e
sia 1 sia 2 fanno fallire il preflight: token scaduto o vicino alla scadenza,
account non business, permessi non verificabili sono tutte ragioni per non far
partire cinque account.

I due profili differiscono su quanto serve *dopo* il primo post:

| | `-Canary` | `-Production` |
|---|---|---|
| ComfyUI spento | avviso | **fallimento** |
| buffer < 30 giorni | avviso | **fallimento** |
| health check in avviso | fallimento | fallimento |

Il canary pubblica un media che esiste già, quindi non ha bisogno del
generatore; lo scheduler sì, e una pagina il cui generatore è fermo tace nel
giro di pochi giorni.

### Canary: un solo post, su una sola pagina

Tre livelli, e la differenza è dichiarata:

```powershell
.\scripts\go_live_canary.ps1 -WhatIf       # 0 chiamate Meta
.\scripts\go_live_canary.ps1 -UploadOnly   # upload reale, nessuna pubblicazione
.\scripts\go_live_canary.ps1               # una sola media_publish
```

- **`-WhatIf`** sceglie il job, misura il file contro le specifiche pubblicate,
  mostra didascalia e account di destinazione, elenca i passi e si ferma.
  Nessun container, nessun upload, nessuna pubblicazione: non costruisce
  nemmeno il client Graph.
- **`-UploadOnly`** crea davvero il container, carica i byte, attende
  `FINISHED` e si ferma lì. Stampa `UPLOAD META RIUSCITO` e `MEDIA NON
  PUBBLICATO`. Sull'account non compare niente.
- **senza parametri** riusa quel container — non ne crea un secondo e non
  ricarica lo stesso Reel — ti mostra cosa uscirà, e pubblica solo dopo che
  scrivi esattamente `PUBBLICA`.

Il job scelto è **il primo futuro** della pagina con il media pronto: mai uno
già scaduto. Se il processo muore fra l'upload e la pubblicazione, il canary
successivo riconosce il container e propone di finire, invece di crearne un
altro.

Il canary **non arma la pagina**. È l'unico percorso del progetto che può
pubblicare senza armamento, e resta irraggiungibile per sbaglio: pagina
esplicita, job esplicito, container già `FINISHED`, audit verde, health check
verde, processo interattivo, la parola, e una volta sola.

Per sapere a che punto è:

```powershell
python -m src.cli instagram canary-status --page pensiero_essenziale_it
```

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

### Il buffer, prima di lasciare il worker da solo

```powershell
python -m src.cli buffer-status --days 30
python -m src.cli prepare-buffer --from 2026-08-11 --days 30 --all-pages --require-comfyui
```

`buffer-status` misura la finestra **dalla data odierna**, non da quella in cui
il buffer fu costruito: quanti dei giorni davanti hanno il media pronto, quanti
job sono rimasti indietro non pubblicati, e quali file generati non sono più
referenziati da alcun job. `--prune-stale` rimuove solo questi ultimi, e solo se
fermi da almeno un giorno: un file che un job scheduled ancora referenzia non
viene toccato, qualunque sia la sua data.

`prepare-buffer` è idempotente — ripeterlo non duplica nulla e dice quanti job
esistevano già — e senza `--from` parte da oggi nel fuso di ciascuna pagina.
`--require-comfyui` fa fallire il comando invece di ripiegare sullo sfondo
deterministico: un buffer costruito sul fallback sembra a posto in un report e
sbagliato sull'account.

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
| Specifiche Reel | `pytest tests/unit/test_reel_specs.py` | 3 s – 15 min, 300 MB, un'unica fonte |
| Percorso canary | `pytest tests/unit/test_go_live_guards.py` | pubblica una volta, non arma |
| Wrapper PowerShell | `pytest tests/unit/test_powershell_behaviour.py` | preflight, `-WhatIf`, `-UploadOnly` |

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
