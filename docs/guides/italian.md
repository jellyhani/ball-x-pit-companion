# BALL x PIT Companion — Italiano

[🌐 Languages](../../README.md#choose-your-language)

Un assistente Windows non ufficiale per BALL x PIT. Legge lo stato del gioco e suggerisce scelte di livello, fusioni, sblocchi dell’enciclopedia, raccolta e disposizione della base. I comandi del gioco restano a te.

## Installazione

Servono Windows 10/11 e una tua installazione Steam di BALL x PIT. Se in [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases) è disponibile uno ZIP, estrailo interamente ed esegui `BallxPitCompanion.exe`, mantenendo accanto la cartella `_internal`. Se non ci sono versioni pubblicate, usa il sorgente qui sotto. Il programma non è firmato e può attivare SmartScreen.

Scarica il repository, apri PowerShell nella sua cartella, installa uv ed esegui la configurazione:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Avvia poi `run_overlay.bat`. La configurazione scarica dipendenze e BepInEx ed estrae testi e icone dalla tua copia del gioco. Chiudi normalmente il gioco per installare o aggiornare il collegamento; l’installazione attende mentre è in esecuzione.

## Uso

- Controlla la connessione nelle impostazioni e apri una schermata di avanzamento di livello o fusione.
- Attiva la modalità enciclopedia nelle impostazioni di visualizzazione per dare priorità alle scoperte; inizialmente è disattivata.
- Confronta la disposizione attuale con quella proposta e sposta manualmente gli edifici nell’ordine indicato.
- Traiettorie e raccolti sono stime. Raggiungere le risorse da più angoli non significa raccoglierle tutte con un solo lancio.

## Privacy e limiti

Il collegamento è di sola lettura: niente patch Harmony, modifiche ai salvataggi o input al gioco. Registri e dati estratti restano in `%LOCALAPPDATA%\BallxPitCompanion`; le partite non vengono caricate online. Versione preliminare verificata soprattutto su Windows 11, 1920×1080, coreano, gioco 1.301. Non garantisce disposizioni ottimali né DPS futuri esatti. Non tutte le traduzioni sono state revisionate da madrelingua. Non è un prodotto ufficiale.

## Problemi e segnalazioni

Se l’overlay non appare, controlla visibilità, finestra del gioco e connessione. In [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues) indica versioni, lingua, risoluzione, passaggi, risultato atteso e risultato effettivo. Rimuovi dati privati da immagini e registri; non caricare salvataggi o risorse estratte dal gioco.

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
