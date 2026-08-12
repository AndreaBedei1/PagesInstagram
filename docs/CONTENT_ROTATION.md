# Rotazione dei contenuti

Come il motore decide, ogni giorno e per ogni pagina, **quale** contenuto
pubblicare. La regola è aritmetica: nessuna casualità, nessuna dipendenza dal
database, nessuna chiamata a un modello.

---

## 1. Due policy

Ogni pagina dichiara la propria policy nello YAML:

```yaml
content:
  selection_policy: cyclic_ordered      # oppure calendar_rotating
  cycle_anchor_date: "2026-01-01"
  cycle_length: 1000
```

| Policy | Pagine | Criterio |
|---|---|---|
| `cyclic_ordered` | Pensiero Essenziale, Curiosità dal Mondo, Parola del Giorno, Una Domanda al Giorno | scorre il dataset in ordine di `sequence_index`, un elemento al giorno |
| `calendar_rotating` | Oggi nella Storia | filtra per `calendar_key` uguale a `MM-DD` della data locale |
| `unused_random` | (legacy, pagine archiviate) | vecchio comportamento "pesca un approvato mai usato" |

## 2. `cyclic_ordered`

```
giorni_trascorsi = (data_locale_programmata - cycle_anchor_date).days
sequence_index   = giorni_trascorsi mod cycle_length      # 0..999
cycle_number     = giorni_trascorsi div cycle_length      # 0, 1, 2, …
```

Esempi con ancora `2026-01-01` e ciclo 1.000:

| Data locale | `sequence_index` | `cycle_number` |
|---|---|---|
| 2026-01-01 | 0 | 0 |
| 2026-08-04 | 215 | 0 |
| 2028-09-26 | 999 | 0 |
| 2028-09-27 | 0 | **1** |

Dopo 1.000 giorni il testo **ricomincia dal primo elemento**.

### Proprietà garantite

La selezione dipende **solo** da `page_id` e dalla data locale programmata del
job. È quindi indipendente da:

- riavvii del processo e del PC;
- ordine delle query SQL (nessun `ORDER BY RANDOM()`);
- inserimenti successivi nel database;
- numero di tentativi di pubblicazione;
- qualunque forma di casualità.

Lo stesso account e la stessa data producono sempre lo stesso contenuto. È
verificato dai test `test_selection_is_stable_across_restart_and_inserts` e
`test_cycle_wraps_from_999_to_0`.

### Sfondo diverso al ciclo successivo

Il seed dello sfondo non dipende solo dal contenuto:

```
seed = sha256(page_id | content_id | scheduled_date | cycle_number | media_type | attempt)
```

`cycle_number` entra nel seed, quindi quando il testo si ripete dopo 1.000 giorni
l'immagine generata è **nuova**. Nello stesso ciclo, invece, rigenerare lo stesso
giorno produce esattamente lo stesso sfondo (utile per riprendere un job).

## 3. `calendar_rotating`

Usata da "Oggi nella Storia", che **non** segue il ciclo lineare.

1. si calcola `calendar_key = MM-DD` della data locale programmata;
2. si selezionano solo i contenuti approvati con quel `calendar_key`;
3. si ordinano in modo stabile per `sequence_index`, poi per `id`;
4. si sceglie `indice = (anno - anno_ancora) mod numero_eventi_di_quella_data`;
5. un controllo difensivo verifica che il `calendar_key` del contenuto scelto
   coincida davvero con la data: se non coincide, la selezione fallisce invece di
   pubblicare un evento nel giorno sbagliato.

Quindi il 4 agosto mostra **sempre** un evento del 4 agosto, e negli anni
successivi mostra un evento diverso finché la data ha alternative.

Il dataset copre tutte le **366** chiavi (29 febbraio incluso) con almeno **due**
eventi ciascuna, quindi la rotazione ha sempre almeno due anni di materiale.

## 4. Data programmata, non "adesso"

La selezione riceve la **data locale programmata del job**, non il momento in cui
il worker gira. Questo garantisce che:

- un job preparato con 30 giorni di anticipo usi il contenuto di quel giorno;
- un recupero dopo un'interruzione (PC spento) pubblichi il contenuto corretto;
- un backfill o una rigenerazione riproducano esattamente lo stesso risultato.

## 5. Assegnazione persistente del giorno

La prima volta che un giorno viene preparato, l'abbinamento pagina/data/contenuto
viene scritto nella tabella `daily_content` insieme a `cycle_number` e
`sequence_index`. Nei tick successivi il worker riusa quella riga.

È una **cache**, non la fonte di verità: per le policy deterministiche il valore
ricalcolato coincide sempre con quello memorizzato. Serve a garantire che
formati diversi dello stesso giorno (il post e, se attivata, la Story) usino lo stesso
contenuto e lo stesso video.

## 6. Spostare la data iniziale

`cycle_anchor_date` è il giorno 0. Cambiarla sposta l'intero calendario
editoriale:

```yaml
content:
  cycle_anchor_date: "2026-03-15"   # il 15 marzo 2026 esce sequence_index 0
```

Se sposti l'ancora dopo l'avvio della pubblicazione, i giorni futuri cambieranno
contenuto. Le righe già presenti in `daily_content` restano invece invariate: i
giorni già preparati non vengono riscritti.

## 7. Cosa succede se manca un contenuto

Se per la data richiesta non esiste un contenuto approvato (indice mancante o
data storica scoperta), la selezione solleva `SelectionError` e il worker manda i
job di quel giorno in `NEEDS_REVIEW` con il motivo. **Non** viene mai pubblicato
un contenuto arbitrario al suo posto.

```powershell
python -m src.cli status          # mostra i job in NEEDS_REVIEW
python -m src.cli dashboard       # revisione e approvazione manuale
```

## 8. Verificare la rotazione a mano

```python
from src.accounts import load_pages
from src.content.service import select_content_for_date
from src.core.settings import load_settings
from src.database import Database

s = load_settings(); db = Database.open(s.db_path()); reg = load_pages()

sel = select_content_for_date(db, reg.get("pensiero_essenziale_it"), "2026-08-04")
print(sel.sequence_index, sel.cycle_number, sel.content["text"])

sel = select_content_for_date(db, reg.get("oggi_nella_storia_it"), "2027-08-04")
print(sel.calendar_key, sel.content["text"])
```
