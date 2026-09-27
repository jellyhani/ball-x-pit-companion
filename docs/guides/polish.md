# BALL x PIT Companion — Polski

[🌐 Languages](../../README.md#choose-your-language)

Nieoficjalny pomocnik do BALL x PIT dla Windows. Odczytuje stan gry i doradza przy awansach, fuzjach, odkrywaniu encyklopedii, zbieraniu zasobów i układaniu bazy. Grą sterujesz samodzielnie.

## Instalacja

Potrzebujesz Windows 10/11 i własnej instalacji BALL x PIT ze Steam. Jeśli w [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases) jest plik ZIP, rozpakuj go w całości i uruchom `BallxPitCompanion.exe`, zachowując obok folder `_internal`. Jeśli nie ma wydania, skorzystaj z instalacji ze źródeł poniżej. Program nie jest podpisany i może wywołać ostrzeżenie SmartScreen.

Pobierz repozytorium, otwórz PowerShell w jego folderze, zainstaluj uv i uruchom konfigurację:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Następnie uruchom `run_overlay.bat`. Konfiguracja pobiera zależności i BepInEx oraz wyodrębnia teksty i ikony z Twojej gry. Przed instalacją lub aktualizacją połączenia zamknij grę w zwykły sposób; instalator czeka, gdy gra działa.

## Używanie

- Sprawdź połączenie w ustawieniach i otwórz ekran awansu lub fuzji.
- Włącz tryb encyklopedii w ustawieniach wyświetlania, aby priorytetowo odkrywać kombinacje; domyślnie jest wyłączony.
- Porównaj aktualny i proponowany układ, a potem ręcznie przestaw budynki w podanej kolejności.
- Tory i zbiory są szacunkami. Dostęp z kilku kątów nie oznacza zebrania wszystkiego jednym wystrzałem.

## Prywatność i ograniczenia

Połączenie tylko odczytuje dane: bez łatek Harmony, zmieniania zapisów czy sterowania grą. Dzienniki i wyodrębnione dane pozostają w `%LOCALAPPDATA%\BallxPitCompanion`; zapisy przebiegu rozgrywki nie są wysyłane. Wczesną wersję testowano głównie na Windows 11, 1920×1080, po koreańsku, w grze 1.301. Nie gwarantuje optymalnego układu ani dokładnego przyszłego DPS. Nie wszystkie tłumaczenia sprawdzili rodzimi użytkownicy języka. To produkt nieoficjalny.

## Problemy i zgłoszenia

Jeśli nakładki nie widać, sprawdź widoczność, okno gry i połączenie. W [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues) podaj wersje, język, rozdzielczość, kroki oraz oczekiwany i rzeczywisty wynik. Usuń prywatne dane z obrazów i dzienników; nie przesyłaj zapisów ani wyodrębnionych zasobów gry.

## Prawa, odpowiedzialność i prywatność

To nieoficjalne narzędzie jest udostępniane w stanie, w jakim się znajduje. Prawa do gry i materiałów osób trzecich należą do odpowiednich właścicieli. Prawa, których nie można wyłączyć zgodnie z prawem, pozostają nienaruszone.

[Full notice / English · 한국어](../../DISCLAIMER.md) · [Privacy / English · 한국어](../../PRIVACY.md)

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
