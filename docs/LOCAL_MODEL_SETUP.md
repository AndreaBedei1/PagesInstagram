# Modello locale (ComfyUI + SDXL)

Il motore genera **solo lo sfondo** con un modello eseguito in locale tramite
ComfyUI. Il testo non viene mai prodotto dal modello: è disegnato dopo con
Pillow, in modo deterministico.

---

## 1. Fonte ufficiale del checkpoint

Verificata il **2026-08-04** sulla pagina ufficiale del modello:

| Voce | Valore |
|---|---|
| Repository | <https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0> |
| File single-file | `sd_xl_base_1.0.safetensors` (~6.94 GB) |
| Variante alternativa | `sd_xl_base_1.0_0.9vae.safetensors` |
| Licenza | CreativeML Open RAIL++-M (tag Hugging Face `openrail++`) |
| Testo licenza | <https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md> |
| Accesso condizionato | **no** (`gated: false`): download diretto, nessun token Hugging Face necessario |

### Utilizzabilità per questo progetto

La licenza Open RAIL++-M consente l'uso, anche commerciale, e la redistribuzione
del modello, imponendo però restrizioni d'uso (allegato) che vanno riportate a
valle. Nel nostro caso il modello genera esclusivamente **sfondi astratti privi di
testo e privi di persone**, un impiego pienamente compatibile con quelle
restrizioni. Le immagini prodotte non sono soggette a rivendicazioni di
proprietà da parte del licenziante.

> Prima di cambiare modello, rileggi la licenza del nuovo checkpoint: non tutti i
> modelli consentono l'uso commerciale.

## 2. Installazione automatica

```powershell
scripts\install_local_model.ps1 -ComfyUIRoot "F:\AI\ComfyUI"
```

```bash
ICE_COMFYUI_ROOT=/opt/ComfyUI scripts/install_local_model.sh
```

Lo script:

1. **verifica se il checkpoint esiste già** e in tal caso non riscarica nulla;
2. lo scarica **solo dalla fonte ufficiale** indicata sopra;
3. calcola e registra lo **SHA-256** in `<file>.sha256`, così l'integrità è
   verificabile in seguito;
4. indica esattamente dove deve trovarsi il file per ComfyUI;
5. esegue una **generazione di prova** che fallisce se il modello non produce
   davvero l'immagine.

Se il download automatico non riesce (rete, proxy, licenza da accettare a mano),
lo script stampa le istruzioni per scaricare il file dalla pagina ufficiale e
copiarlo nella cartella corretta.

## 3. Dove va il file

```
<ComfyUI>/models/checkpoints/sd_xl_base_1.0.safetensors
<ComfyUI>/models/checkpoints/sd_xl_base_1.0.safetensors.sha256
```

I file `.safetensors`, `.ckpt`, `.pt`, `.pth` e i relativi `.sha256` sono in
`.gitignore`: **non finiscono mai nel repository**.

## 4. Configurazione

I default coincidono già con l'installazione sopra. Per cambiare modello non
serve toccare il codice:

```
ICE_COMFYUI_URL=http://127.0.0.1:8188
ICE_COMFYUI_LAUNCH_BAT=F:\AI\start_comfyui.bat
ICE_COMFYUI_MODEL_FAMILY=sdxl                    # sdxl | sd15
ICE_COMFYUI_CHECKPOINT=sd_xl_base_1.0.safetensors
ICE_COMFYUI_WORKFLOW=sdxl_background.json
```

Le stesse chiavi esistono in `config/settings.yaml` sotto `comfyui:`, insieme a
`steps`, `cfg`, `sampler_name` e `scheduler` (lasciandoli a `null` valgono i
default della famiglia).

## 5. Workflow e risoluzioni

`comfyui/workflows/sdxl_background.json` è il grafo di riferimento in formato API
(caricabile anche nell'interfaccia di ComfyUI). L'engine costruisce comunque il
grafo a runtime, così una modifica al file non rompe la pipeline.

| Famiglia | Bucket 9:16 | Steps | CFG | Sampler |
|---|---|---|---|---|
| `sdxl` | 768 × 1344 | 30 | 6.0 | `dpmpp_2m` / `karras` |
| `sd15` | 576 × 1024 | 26 | 6.5 | `dpmpp_2m` / `karras` |

768 × 1344 è uno dei bucket ufficiali di SDXL (area ≈ 1024²), quindi il modello
lavora nella risoluzione per cui è stato addestrato. L'immagine viene poi
riscalata a 1080 × 1920 in fase di rendering.

### Fallback esplicito su SD 1.5

Su GPU con poca memoria si può tornare al vecchio workflow, **in modo esplicito**:

```
ICE_COMFYUI_MODEL_FAMILY=sd15
ICE_COMFYUI_CHECKPOINT=DreamShaper_8_pruned.safetensors
ICE_COMFYUI_WORKFLOW=sd15_background.json
```

Non è un ripiego automatico: va scelto.

## 6. Profili di sfondo delle cinque pagine

Ogni pagina ha un profilo dichiarato in `visual.background_profile`:

| Profilo | Pagina | Direzione visiva |
|---|---|---|
| `philosophy_warm_minimal` | Pensiero Essenziale | astratto caldo, sabbia e crema, luce diffusa, centro vuoto |
| `world_deep_geographic` | Curiosità dal Mondo | illustrativo, teal e ardesia, forme topografiche stilizzate |
| `word_editorial_paper` | Parola del Giorno | carta editoriale invecchiata, avorio, grana di stampa |
| `history_archival` | Oggi nella Storia | texture d'archivio, ambra e terra d'ombra, grana fotografica |
| `question_dark_minimal` | Una Domanda al Giorno | fondo molto scuro, un solo bagliore lontano |

Tutti i prompt richiedono esplicitamente: composizione verticale 9:16, zona
centrale calma e a bassa complessità dietro il testo, e **nessun testo, nessuna
lettera, nessun watermark**. Il prompt negativo rafforza il divieto con oltre
venti termini (lettering, tipografia, numeri, firma, logo, poster, interfaccia…).

Per curiosità e storia i profili sono deliberatamente **astratti o illustrativi**:
il modello non viene mai usato per fingere una fotografia documentaria di un
luogo o di un evento reale.

## 7. Seed deterministici

```
seed = sha256(page_id | content_id | scheduled_date | cycle_number | media_type | attempt)
```

Rigenerare lo stesso giorno produce lo stesso sfondo; il ciclo successivo, con
`cycle_number` incrementato, ne produce uno nuovo.

## 8. Comportamento in produzione

| Modalità | Fallback deterministico |
|---|---|
| `dry_run` | consentito |
| `test` | consentito |
| `production` | **vietato** |

In produzione, se ComfyUI non è raggiungibile, il checkpoint manca o la
generazione fallisce dopo i tentativi previsti, il job entra in errore controllato
(`NEEDS_REVIEW`) e **non** viene pubblicato alcuno sfondo generico. Il
comportamento è verificato dal test
`test_production_never_falls_back_silently`.

## 9. Verifica

```powershell
python -m src.cli comfyui status
python -m src.cli comfyui test-generation --page pensiero_essenziale_it
```

`test-generation` esce con codice `0` solo se l'immagine è arrivata davvero da
ComfyUI; esce con `2` se ha dovuto usare il fallback e con `1` in caso di errore.
Un'anteprima ottenuta con il fallback **non** conta come prova che il modello
locale funzioni.
