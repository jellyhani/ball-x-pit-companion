# BALL x PIT Companion — Deutsch

[🌐 Languages](../../README.md#choose-your-language)

Ein inoffizieller Windows-Begleiter für BALL x PIT. Er liest den Spielzustand und gibt Hinweise zu Aufstiegen, Fusionen, Enzyklopädie-Freischaltungen, Ernte und Basisaufstellung. Du steuerst das Spiel selbst.

## Installation

Du brauchst Windows 10/11 und deine eigene Steam-Installation von BALL x PIT. Falls unter [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases) eine ZIP-Datei verfügbar ist, entpacke sie vollständig und starte `BallxPitCompanion.exe`. Der Ordner `_internal` muss daneben bleiben. Ohne Release nutze die Quellcode-Installation unten. Nicht signierte Programme können eine SmartScreen-Warnung auslösen.

Lade das Repository herunter, öffne PowerShell in seinem Ordner, installiere uv und führe die Einrichtung aus:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Starte danach `run_overlay.bat`. Die Einrichtung lädt Abhängigkeiten und BepInEx herunter und extrahiert Texte und Symbole aus deinem Spiel. Beende das Spiel regulär, um die Anbindung zu installieren oder zu aktualisieren. Während das Spiel läuft, wartet das Installationsprogramm.

## Verwendung

- Prüfe die Verbindung in den Einstellungen und öffne die Aufstiegs- oder Fusionsauswahl.
- Aktiviere den Enzyklopädie-Modus in den Anzeigeeinstellungen, um Entdeckungen zu bevorzugen; standardmäßig ist er aus.
- Vergleiche die aktuelle und vorgeschlagene Aufstellung und verschiebe Gebäude manuell in der angegebenen Reihenfolge.
- Flugbahnen und Erntemengen sind Schätzungen. Erreichbarkeit aus mehreren Winkeln bedeutet keine vollständige Ernte mit einem einzigen Schuss.

## Datenschutz und Grenzen

Die Anbindung liest nur: keine Harmony-Patches, Spielstandänderungen oder Spieleingaben. Protokolle und extrahierte Daten bleiben in `%LOCALAPPDATA%\BallxPitCompanion`; Spielaufzeichnungen werden nicht hochgeladen. Diese frühe Version wurde hauptsächlich mit Windows 11, 1920×1080, Koreanisch und Spielversion 1.301 geprüft. Optimale Aufstellungen und exakte zukünftige DPS sind nicht garantiert. Nicht alle Übersetzungen sind muttersprachlich geprüft. Kein offizielles Produkt.

## Fehlersuche und Meldungen

Fehlt das Overlay, prüfe Sichtbarkeit, Spielfenster und Verbindung. Nenne unter [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues) Versionen, Sprache, Auflösung, Schritte sowie erwartetes und tatsächliches Ergebnis. Entferne private Angaben aus Bildern und Protokollen; lade keine Spielstände oder extrahierten Spielinhalte hoch.

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
