# Runbook di produzione

## Come esce un post, in pratica

Queste pagine pubblicano **testo**, quindi il formato è un **post immagine 4:5
(1080×1350)**: nessun MP4, nessuna traccia audio, nessun ffmpeg nel percorso di
pubblicazione. Non è solo una semplificazione — un video di otto secondi
richiede comunque un flusso audio, e il difetto che ha motivato la migrazione
viveva in un componente che una immagine ferma non ha.

`pensiero_essenziale_it` pubblica con **Facebook Login + hosted_url + Cloudflare
Quick Tunnel**. La sequenza è interamente automatica e dura quanto una singola
pubblicazione:

```
PNG locale 1080×1350
 -> server HTTP che conosce UN file a UN path casuale, su 127.0.0.1
 -> cloudflared tunnel --url http://127.0.0.1:<porta libera>
 -> https://<random>.trycloudflare.com/media/<uuid>.png
 -> verifiche: GET 200, HEAD 200, Range 206, image/png, dimensione esatta,
    e 404 su /, /.env, /.git/, /database/content.sqlite, /stories/
 -> POST /media  media_type=IMAGE  image_url=...  caption
 -> polling fino a FINISHED
 -> UNA media_publish
 -> cloudflared spento, server spento, URL morto
```

`share_to_feed` non viene inviato: è un parametro dei Reel e Meta lo rifiuta su
un contenitore IMAGE. Il percorso video resta nel codice e resta testato — una
pagina configurata con `feed_media_type: REELS` lo usa ancora — ma nessuna delle
cinque pagine lo configura.

**A riposo non c'è niente acceso**: nessun tunnel, nessun server, nessuna porta.
Il worker li apre solo nel momento in cui deve pubblicare.

Perché non il resumable upload, che sarebbe più semplice: è un protocollo per
video — un'immagine non ha byte da caricare, Meta la preleva da `image_url` — e
in ogni caso su questo account `rupload.facebook.com` rispondeva
`ProcessingFailedError` con zero byte accettati, con qualunque file e qualunque
client. La matrice in `src/core/meta_api.py` impedisce di riconfigurare per
sbaglio una combinazione che Meta non implementa, e `health-check` rifiuta
`feed_media_type: IMAGE` accoppiato a `upload_method: resumable`.

Cosa **non** serve: account Cloudflare, carta di credito, dominio, DNS, hosting
persistente, porte aperte sul router, `ICE_PUBLIC_MEDIA_BASE_URL`.

`cloudflared` è un binario singolo scaricato una volta in `runtime/`
(gitignored, mai committato, nessuna installazione di sistema).

Due tempi tecnici che il codice attende di proposito: il nome DNS del tunnel
non esiste per qualche secondo dopo l'annuncio — interrogarlo troppo presto fa
memorizzare a Windows un NXDOMAIN che poi risponde a tutti i tentativi
successivi — e l'edge Cloudflare restituisce 530 finché non ha una rotta.
Complessivamente circa settanta secondi prima che l'URL sia utilizzabile.


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

```powershell
.\scripts\go_live_canary.ps1 -WhatIf       # 0 chiamate Meta
.\scripts\go_live_canary.ps1               # una sola media_publish
```

- **`-WhatIf`** sceglie il job, misura il file contro le specifiche pubblicate
  del suo formato — una immagine contro le specifiche immagine, un Reel contro
  quelle Reel — mostra didascalia, formato, trasporto e account di destinazione,
  elenca i passi e si ferma. Nessun tunnel, nessun container, nessuna
  pubblicazione: non costruisce nemmeno il client Graph.
- **senza parametri** ti mostra cosa uscirà e pubblica solo dopo che scrivi
  esattamente `PUBBLICA`. La conferma viene **prima** che il tunnel si apra.

**`-UploadOnly` non esiste per una pagina con Quick Tunnel**, e lo script lo
dice invece di fingere: il contenitore nasce da un URL che vive solo dentro la
sessione del tunnel, quindi "carica adesso, pubblica dopo" pubblicherebbe da un
indirizzo che non risponde più. Creazione, prelievo e pubblicazione sono un atto
solo. Per una pagina con trasporto `resumable` i livelli restano tre.

Il job scelto è **il primo futuro** della pagina con il media pronto: mai uno
già scaduto.

Il canary **non arma la pagina**. È l'unico percorso del progetto che può
pubblicare senza armamento, e resta irraggiungibile per sbaglio: pagina
esplicita, job esplicito, audit verde, health check verde, processo interattivo,
la parola, e una volta sola. Un container rimasto da un tentativo precedente lo
ferma invece di essere riusato — il suo URL è morto e vale la pena guardare
l'account prima di riprovare.

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
.\scripts\install_worker.ps1     # fa ripartire il worker a ogni accesso
.\scripts\start_worker.ps1       # in primo piano; -Once per un solo ciclo
.\scripts\status.ps1             # modalità, armamento, job, worker
.\scripts\stop_worker.ps1        # ferma subito; -Disarm disarma anche le pagine
.\scripts\unregister_task_scheduler.ps1  # smette di ripartire da solo
.\scripts\rollback.ps1           # ferma, disarma, riporta a dry_run
```

`install_worker.ps1` prova prima l'**attività pianificata** — è il meccanismo
migliore dove si può creare: riavvia il worker se muore e recupera se il PC era
spento all'orario previsto. Dove la policy della macchina la vieta, sia
`Register-ScheduledTask` sia `schtasks.exe` rispondono `Accesso negato`
(0x80070005), e non c'è modo di aggirarlo con più tentativi. In quel caso lo
script **ripiega da solo** sull'**Esecuzione automatica dell'utente**, che non
richiede privilegi: un `.vbs` nella cartella Startup lancia
`scripts\worker_loop.cmd`, senza finestra, e il ciclo riavvia il worker se il
processo muore. Lo script dice **quale** dei due meccanismi ha usato, e
`status.ps1` lo ripete: "installato" senza dire come è la frase che lascia una
macchina a non pubblicare niente.

In entrambi i casi il worker parte **all'accesso**, quindi il PC deve essere
acceso e l'utente collegato all'orario di pubblicazione. Se era spento, al primo
avvio si applica la `missed_job_policy` della pagina: 240 minuti di recupero,
oltre i quali il giorno viene saltato invece di uscire fuori tempo.

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
| Media | `media-audit --buffer --all` | immagini 1080×1350 conformi alle specifiche Meta |
| Versione API | `tools/check_meta_api_version.py` | v25.0 supportata fino al 2028-07-29 |
| Specifiche Reel | `pytest tests/unit/test_reel_specs.py` | 3 s – 15 min, 300 MB, un'unica fonte |
| Post immagine | `pytest tests/unit/test_image_post.py tests/unit/test_image_audit.py` | 4:5, niente video/audio, `image_url` |
| Canary immagine | `pytest tests/unit/test_image_canary.py` | una sola volta, tunnel solo dopo la conferma |
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

- Su `@pensiero_essenziale` sono già usciti **due Reel reali** (canary del
  trasporto resumable e canary del Quick Tunnel). Le altre quattro pagine non
  hanno mai effettuato una chiamata Meta autenticata: per ciascuna la prima
  prova reale sarà il proprio canary.
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
