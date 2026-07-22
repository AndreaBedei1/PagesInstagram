# Meta Resumable Upload — riferimento ufficiale (Instagram Content Publishing)

> Fonte primaria: **solo** documentazione ufficiale Meta. Nessun blog / nessuna
> implementazione non ufficiale usata come fonte. Verificato il 2026‑07‑22.

## Riferimenti ufficiali
- Content Publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing/
- **Resumable Uploads**: https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/
- IG User `/media` reference (parametri): https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media/
- Overview / Login flavor: https://developers.facebook.com/docs/instagram-platform/overview/

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
Gli esempi ufficiali correnti usano **`v25.0`** (es. `graph.instagram.com/v25.0/.../media`).
La versione è configurabile (`META_GRAPH_API_VERSION`, default nel progetto `v23.0`).
Per l'upload si usa **l'URI restituito da Meta**, quindi la versione lì è sempre
coerente con quanto deciso dal server.

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
- **Reels**: 9:16, 1080×1920, MP4/MOV, **H.264 + AAC**, durata **3–90 s**.
- **Stories** (video): 9:16, 1080×1920, fino a **60 s** per clip.
- Requisiti container video: `moov atom` all'inizio (`+faststart`), audio AAC 48 kHz,
  1–2 canali; video progressivo, closed GOP, 23–60 FPS.
- **Rate limit pubblicazione: 100 post/24 h** (finestra mobile) — verificabile con
  `GET /<IG_USER_ID>/content_publishing_limit`.
- La doc non elenca un limite esplicito di dimensione file per il resumable.

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
