# Policy sui segreti

## Dove vivono le credenziali

Solo in `.env`, letto dal motore all'avvio. Mai in YAML, mai nel database, mai
nei log, mai in un report, mai in un file tracciato da Git.

`.gitignore` esclude `.env`, `.env.*` (tranne `.env.example`), `secrets/`,
`*.safetensors`, i database runtime, i log, i report e `.cache/`. Lo scanner
fallisce se uno di questi finisce fra i file tracciati, **qualunque cosa
contenga oggi**: un database pulito adesso è una fuga in attesa del prossimo run.

## Che cosa cerca lo scanner interno

`python -m src.cli security-check` analizza i file tracciati e cerca:

- token Meta (`EAA…`) e Instagram (`IGQ…`);
- assegnazioni a nomi sensibili (`ACCESS_TOKEN`, `APP_SECRET`, `CLIENT_SECRET`, …);
- app secret esadecimali;
- **token in query string** — è così che una credenziale finisce in un log o in
  un ticket senza che nessuno l'abbia scritta di proposito;
- **header `Authorization` serializzati** (`OAuth`, `Bearer`, `Basic`);
- campi `access_token` / `refresh_token` in JSON;
- percorsi vietati fra i file tracciati.

Ogni reperto viene **redatto**: dello stesso valore restano tre caratteri
iniziali, due finali e la lunghezza. Il valore intero non viene mai stampato né
salvato.

## Le fixture dei test

Nessun file di test può contenere una stringa che assomigli a una credenziale.
Le fixture sono composte a runtime da frammenti innocui, e
`test_this_test_file_does_not_itself_trip_the_scanner` fallisce se qualcuno ne
scrive una letterale.

Il motivo è concreto: una fixture con la forma di un token Meta ha generato un
incidente GitGuardian su questo repository. Non era una credenziale, ma un
allarme falso insegna a ignorare gli allarmi.

## Scansioni indipendenti

```powershell
# scanner interno: working tree e file tracciati
python -m src.cli security-check

# detect-secrets, sui file tracciati
python -m detect_secrets scan --exclude-lines '"verified_content_hash"|"content_hash"' `
    $(git ls-files)
```

L'esclusione è **mirata a due nomi di campo**, non a file o directory. Quei
campi contengono SHA-256 dei testi dei contenuti: sono pubblici per costruzione,
servono a dimostrare che un contenuto non è cambiato dopo la verifica, e
detect-secrets li segnala come «Hex High Entropy String». Escludere i due campi
non nasconde nient'altro; escludere i file interi lo farebbe.

Gitleaks e TruffleHog non sono installabili su questa macchina senza privilegi
amministrativi e non sono stati eseguiti. Non fingere il contrario in un
rapporto: lo scanner interno e detect-secrets sono due strumenti indipendenti,
ed entrambi coprono anche la cronologia raggiungibile dal branch.

## Cronologia

Un segreto rimosso dal working tree resta nella cronologia finché un commit che
lo contiene è raggiungibile. In quel caso:

1. crea un branch pulito dalla base corretta;
2. importa il lavoro con uno **squash**, senza la cronologia problematica;
3. elimina il branch remoto che tiene vivi quei commit;
4. verifica con `git rev-list --objects <branch>` che il blob non sia più
   raggiungibile;
5. **considera comunque compromessa la credenziale** e ruotala.

Non riscrivere con force-push un branch condiviso o quello predefinito.

## Rotazione dei token

I token long-lived durano 60 giorni. `instagram health-check --all` avvisa
quando ne mancano meno di dieci. Il rinnovo automatico non è implementato di
proposito: un rinnovo non provato su un account reale rischia di invalidare
token funzionanti. Sostituisci il valore in `.env` e riavvia il worker; i job
già pianificati non si perdono.
