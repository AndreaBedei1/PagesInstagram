# Meta Resumable Upload — riferimento ufficiale (Instagram Content Publishing)

> Fonte primaria: **solo** documentazione ufficiale Meta. Nessun blog / nessuna
> implementazione non ufficiale usata come fonte.
> Prima verifica: 2026‑07‑22 · riverifica: 2026‑08‑04 · riverifica dell'audit
> di pre-produzione: 2026‑08‑05 · **riverifica pre-credenziali: 2026‑08‑11.**

## Riverifica del 2026‑08‑11 — chi usa quali credenziali

Il repository si contraddiceva: `.env.example` diceva che `META_APP_ID` e
`META_APP_SECRET` servono «solo per generare/rinnovare i token», mentre
`preflight.ps1` si rifiutava di partire senza. Ogni riga qui sotto è stata
riletta oggi sulla documentazione ufficiale, una pagina alla volta.

| Operazione | Endpoint | Host | App id/secret |
|---|---|---|---|
| Creare il container | `POST /<IG_ID>/media` | `graph.instagram.com` | **no** |
| Pubblicare | `POST /<IG_ID>/media_publish` | `graph.instagram.com` | **no** |
| Identità del token | `GET /me?fields=user_id,username` | `graph.instagram.com` | **no** |
| Scadenza e scope | `GET /debug_token` | **`graph.facebook.com`** | **sì** |
| Rinnovo a 60 giorni | `GET /refresh_access_token`, `grant_type=ig_refresh_token` | `graph.instagram.com` | **no** |
| Da breve a lungo | `GET /access_token`, `grant_type=ig_exchange_token` | `graph.instagram.com` | **sì** (`client_secret`) |

Fonti: la pagina *get started* di Instagram Login mostra le chiamate su
`https://graph.instagram.com/v25.0/`; la pagina *refresh_access_token* elenca
come parametri richiesti soltanto `grant_type` e `access_token`; la pagina
*access_token* elenca `client_secret` come **Required**; il riferimento di
`debug_token` richiede «un token d'accesso dell'app o un token d'accesso
dell'utente di uno sviluppatore di app» e mostra `Host: graph.facebook.com`.

**Conseguenza sul codice.** `GraphClient.debug_token()` chiamava `debug_token`
sull'host Instagram passando il token Instagram come proprio `access_token`:
endpoint sbagliato e autorità sbagliata. Non poteva funzionare, e il
`health-check` leggeva il dizionario vuoto che ne usciva come «scadenza non
nota». Ora esistono due metodi distinti — `verify_token()` (solo token,
`/me`) e `debug_token(app_id, app_secret)` (host Facebook, app access token) —
e `health-check` distingue *token non verificabile* da *token invalido*.

**Conseguenza sul `.env`.** Le due variabili d'app non sono richieste per
pubblicare e il preflight non le pretende più. Restano necessarie per leggere
scadenza e permessi: senza, il health check resta in avviso, e per il primo
go-live un avviso sulle credenziali è bloccante.

### Specifiche Reel confermate lo stesso giorno

Rilette letteralmente dal riferimento IG User `/media`:

| Aspetto | Citazione |
|---|---|
| Durata | «Minimum of 3 seconds; maximum of 15 mins» |
| Dimensione file | «300MB maximum» |
| Pixel orizzontali | massimo 1920 |
| Frame rate | «23-60 FPS» |
| Codec video | «HEVC or H264, progressive scan, closed GOP, 4:2:0 chroma subsampling» |
| Codec audio | «AAC, 48khz sample rate maximum, 1 or 2 channels» |
| Contenitore | «MOV or MP4 (MPEG-4 Part 14), no edit lists, moov atom at the front of the file» |

Questo corregge due affermazioni precedenti di questo documento: che il limite
di dimensione non fosse pubblicato (lo è: 300 MB, ora verificato dal publisher)
e — di nuovo — i 90 s, che `publisher.py` continuava a imporre in un letterale
proprio nonostante la tabella qui sopra. Adesso ogni controllo legge
`src/core/meta_api.py`, e un test cerca nell'albero una seconda copia del
limite.

## Riferimenti ufficiali consultati il 2026‑08‑05
- Changelog Graph API: https://developers.facebook.com/docs/graph-api/changelog
- Content Publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing
- **Resumable Uploads**: https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/
- IG User `/media` reference (parametri e specifiche video): https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media/
- Instagram Login — get started (token long-lived): https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started

## Esito della riverifica del 2026‑08‑05

| Aspetto | Esito |
|---|---|
| Flusso resumable (container → upload binario → polling → publish) | **invariato** |
| Host dell'upload binario | `rupload.facebook.com`. La pagina ufficiale mostra il path **senza** segmento di versione (`/ig-api-upload/<CONTAINER_ID>`); il progetto usa comunque l'URI restituito da Meta, quindi il punto è ininfluente |
| Header obbligatori | invariati: `Authorization: OAuth <TOKEN>`, `offset`, `file_size` |
| Parametro `upload_type=resumable` sulla creazione container | invariato |
| `media_type` | il riferimento IG User `/media` elenca ora **`CAROUSEL`, `REELS`, `STORIES`** |
| Ripresa di un upload interrotto (`bytes_transferred` → nuovo `offset`) | invariato |
| Versione Graph API | **aggiornata**: vedi sezione dedicata |
| `share_to_feed` | confermato, boolean, «For Reels only. When true, indicates that the reel can appear in both the Feed and Reels tabs» |
| Durata massima dei Reel | **corretta**: la documentazione precedente del progetto indicava 90 s, il riferimento ufficiale indica **15 minuti** (minimo 3 s) |
| Limite di pubblicazione | confermato: «Instagram accounts are limited to 100 API-published posts within a 24-hour moving period», verificabile su `GET /<IG_ID>/content_publishing_limit` |
| Token long-lived | confermato: 60 giorni |
| Necessità di URL pubblici | **nessuna**: il file locale viaggia direttamente verso i server Meta |

Nessuna modifica funzionale al client è risultata necessaria. In particolare
**non** è stato reintrodotto alcun requisito di `ICE_PUBLIC_MEDIA_BASE_URL` per
il provider resumable: quella variabile resta usata soltanto dal provider
opzionale e inattivo `hosted_url`.

### Business e Creator
La documentazione ufficiale distingue i tipi di account professionale
(`Business` e `Media_Creator`) ma **non** pubblica, nelle pagine consultate, una
tabella di differenze di capacità per il content publishing. Il progetto continua
quindi a richiedere account **Business** come requisito prudenziale, senza
affermare che i Creator non possano pubblicare.

### Rinnovo automatico del token

La documentazione corrente descrive lo scambio short‑lived → long‑lived e il
rinnovo del token long‑lived, ma richiede credenziali applicative e un flusso di
autorizzazione che dipende dalla configurazione dell'app. **Il rinnovo automatico
non è stato implementato**: il sistema si limita a segnalare in anticipo la
scadenza con `instagram health-check --all`, che avvisa quando mancano meno di
dieci giorni. È una scelta prudente: un rinnovo automatico non testato su un
account reale rischierebbe di invalidare token funzionanti.

## Perché resumable (vincolo di rete)
Il resumable upload invia il **file binario locale direttamente ai server Meta**
via HTTPS in uscita. **Non serve** un `video_url` pubblico, né hosting, né porte
aperte, né storage esterno. È l'unico metodo ufficiale conforme al vincolo
"solo connessioni in uscita".

## Host
- Creazione container / status / publish: `graph.instagram.com` (Instagram Login)
  oppure `graph.facebook.com` (Facebook Login).
- **Upload binario: sempre `rupload.facebook.com`** (l'endpoint esatto viene
  restituito nel campo `uri` della risposta di creazione container: usare quello).

## Versione Graph API

**Default del progetto: `v25.0`** (era `v23.0`).

Dal changelog ufficiale, al 2026‑08‑05:

| Versione | Rilascio | Disponibile fino a |
|---|---|---|
| v26.0 | 29 luglio 2026 | non ancora indicata |
| **v25.0** | 18 febbraio 2026 | 29 luglio 2028 |
| v24.0 | 8 ottobre 2025 | 18 febbraio 2028 |
| v23.0 | 29 maggio 2025 | 8 ottobre 2027 |

**Perché `v25.0` e non `v26.0`.** Alla data di verifica v26.0 era disponibile da
una settimana e nessuna pagina di Instagram Content Publishing la usava negli
esempi: le pagine su content publishing, sul riferimento IG User `/media` e su
Instagram Login mostrano tutte `v25.0`. Allinearsi alla versione che Meta stessa
usa negli esempi riduce il rischio di comportamenti non documentati, e v25.0
resta disponibile fino al 29 luglio 2028: c'è ampio margine prima di un
aggiornamento forzato.

`v23.0` non era sbagliata — resta valida fino all'8 ottobre 2027 — ma era la
versione con la scadenza più vicina fra quelle supportate, senza alcun vantaggio.

La versione resta configurabile con `META_GRAPH_API_VERSION` (formato `vNN.N`,
validato all'avvio: un valore malformato è un errore di configurazione, non un
default silenzioso). Il valore predefinito è definito **in un unico punto**,
`src/core/meta_api.py`, da cui lo leggono le impostazioni Python e il client; un
test verifica che `config/settings.yaml`, `config/settings.example.yaml`,
`.env.example` e questo documento non divergano.

Per l'upload binario si usa **l'URI restituito da Meta**, quindi la versione lì
è sempre coerente con quanto deciso dal server.

## Flusso (3 + 1 passi)

```
file MP4 locale
  → validazione (esiste, dimensione>0, mp4/h264/aac, durata nei limiti)
  → 1) creazione container resumable      POST graph.*/{ver}/{ig-user-id}/media
  → acquisizione upload URI               (campo "uri" nella risposta)
  → 2) upload binario diretto (stream)    POST {uri}  su rupload.facebook.com
  → verifica risposta upload              {"success": true}
  → 3) polling elaborazione               GET  graph.*/{ver}/{container}?fields=status_code
  → 4) pubblicazione                      POST graph.*/{ver}/{ig-user-id}/media_publish
```

### 1) Creazione container resumable
`POST https://graph.instagram.com/<VER>/<IG_USER_ID>/media`

Parametri (query/body):
| parametro | REEL | STORY | note |
|---|---|---|---|
| `upload_type` | `resumable` | `resumable` | obbligatorio per l'upload diretto |
| `media_type` | `REELS` | `STORIES` | obbligatorio |
| `caption` | ✅ (max 2200, 30 hashtag, 20 @) | ❌ (scelta di prodotto: niente caption su Story) | |
| `share_to_feed` | `true` | — | boolean, **solo Reels**: `true` = compare in Feed **e** tab Reels; `false` = solo tab Reels |
| `access_token` | ✅ | ✅ | mai nei log |

Risposta:
```json
{ "id": "<IG_CONTAINER_ID>",
  "uri": "https://rupload.facebook.com/ig-api-upload/<VER>/<IG_CONTAINER_ID>" }
```

### 2) Upload binario (rupload.facebook.com)
`POST <uri>`  (l'URI del passo 1)

Header **obbligatori**:
- `Authorization: OAuth <ACCESS_TOKEN>`  ← prefisso **`OAuth`**, non `Bearer`
- `offset: <primo byte da inviare>`  (di norma `0`; per resume = byte già trasferiti)
- `file_size: <dimensione totale del file in byte>`
- corpo = **byte binari del file** (streaming, `--data-binary`)

Risposta di successo:
```json
{ "success": true, "message": "Upload successful." }
```

(È documentato anche un header alternativo `file_url: <https>` per far scaricare
il file a Meta da un URL pubblico — **non usato**: noi carichiamo il binario locale.)

### 3) Polling elaborazione
`GET https://graph.instagram.com/<VER>/<IG_CONTAINER_ID>?fields=status_code`

`status_code` ∈ `IN_PROGRESS | FINISHED | ERROR | EXPIRED | PUBLISHED`.
Consigliato: 1 richiesta/minuto, max ~5 minuti. Il container scade dopo 24 h.

### 4) Pubblicazione
`POST https://graph.instagram.com/<VER>/<IG_USER_ID>/media_publish`  con
`creation_id=<IG_CONTAINER_ID>` → `{ "id": "<IG_MEDIA_ID>" }`.

## Resume di un upload interrotto
Interrogare lo stato di upload:
`GET https://graph.instagram.com/<VER>/<IG_CONTAINER_ID>?fields=status,video_status`

```json
"video_status": { "uploading_phase": { "status": "in_progress", "bytes_transferred": 50002 } }
```

Riprendere: reinviare l'upload al medesimo `uri` con header
`offset: <bytes_transferred>` (invia solo i byte mancanti). Il container e l'URI
restano validi finché il container non scade (24 h) → **non ricreare** il container
dopo un'interruzione: riprendere.

## Limiti (documentati / specifiche media)

Dal riferimento IG User `/media`, verificato il 2026‑08‑05 (citazioni letterali):

| Aspetto | Valore documentato |
|---|---|
| Durata Reel | «15 mins maximum, 3 seconds minimum» |
| Proporzioni | «Required aspect ratio is between 0.01:1 and 10:1 but we recommend 9:16» |
| Codec video | «HEVC or H264» |
| Contenitore | «MOV or MP4 (MPEG-4 Part 14)» |
| Frame rate | «23-60 FPS» |
| Codec audio | «AAC, 48khz sample rate maximum» |

> **Correzione rispetto alla versione precedente di questo documento**, che
> indicava una durata massima di 90 s per i Reel: il riferimento ufficiale
> indica **15 minuti**. Il progetto genera comunque clip di ~8 s, quindi
> l'errore non ha mai influito sulla produzione, ma la tabella era sbagliata.

- **Stories** (video): 9:16, 1080×1920, fino a **60 s** per clip.
- Requisiti container video: `moov atom` all'inizio (`+faststart`),
  video progressivo, closed GOP.
- **Rate limit pubblicazione: 100 post/24 h** (finestra mobile) — verificabile con
  `GET /<IG_USER_ID>/content_publishing_limit`. Cinque post al giorno su cinque
  account distinti restano ampiamente sotto la soglia, che è per account.
- Dimensione file: **300 MB** («300MB maximum»), verificata dal publisher prima
  di creare il container.

Questi valori sono replicati come costanti in `src/core/meta_api.py`, così che i
controlli sul video li leggano dalla stessa fonte della documentazione.

## Errori e retry
- Errore Graph standard: `{ "error": { "code", "message", ... } }`.
- Errore upload (rupload): `{ "debug_info": { "retriable": <bool>, "type", "message" } }`.
  → usare `retriable` per decidere il retry.
- Trattare come **transitori/ritentabili**: HTTP ≥ 500, timeout/connessione interrotta,
  `retriable: true`, rate‑limit (code 4/17/32/613). Backoff esponenziale, tetto ai tentativi.
- Trattare come **terminali**: token/permessi (190, 10, 200, 803), file/asset non valido,
  container `ERROR`/`EXPIRED` (in tal caso il container va ricreato al retry successivo).

## Requisiti account (per pubblicare + Stories)
- Account **Instagram professional Business** (Creator ha limiti su alcune feature).
- Permessi: `instagram_business_basic`, `instagram_business_content_publish`
  (Instagram Login) oppure `instagram_basic` + `instagram_content_publish`
  (Facebook Login, richiede Pagina collegata).
- Token long‑lived (60 giorni), rinnovabile.

## Mappatura interna del progetto
- Contenuto principale → **REEL** con `share_to_feed=true` (rappresentazione pulita:
  `MediaType.REEL`, "REEL_WITH_FEED_SHARE"). 9:16, caption nel container.
- Storia → **STORY_VIDEO** (`media_type=STORIES`), 9:16, **senza caption**, stesso
  contenuto/frase/autore/musica del giorno.
- `upload_method=resumable` è il **default**; `hosted_url` resta provider secondario
  opzionale (richiede `ICE_PUBLIC_MEDIA_BASE_URL`) e **non** è necessario.
