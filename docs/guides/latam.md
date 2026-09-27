# BALL x PIT Companion — Español (Latinoamérica)

[🌐 Languages](../../README.md#choose-your-language)

Asistente no oficial para BALL x PIT en Windows. Lee el estado del juego y ofrece consejos sobre subidas de nivel, fusiones, desbloqueos de la enciclopedia, recolección y distribución de la base. Tú manejas el juego.

## Instalación

Necesitas Windows 10/11 y tu propia instalación de BALL x PIT en Steam. Si hay un ZIP en [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases), descomprímelo completo y ejecuta `BallxPitCompanion.exe`, manteniendo la carpeta `_internal` a su lado. Si todavía no hay una versión publicada, usa el código fuente con los pasos de abajo. El programa no está firmado y puede activar SmartScreen.

Descarga el repositorio, abre PowerShell en esa carpeta, instala uv y ejecuta la configuración:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Luego inicia `run_overlay.bat`. La configuración descarga dependencias y BepInEx, y extrae textos e íconos de tu juego. Cierra el juego de forma normal para instalar o actualizar la conexión; el instalador espera mientras esté abierto.

## Cómo usarlo

- Revisa la conexión en la configuración y abre una pantalla de subida de nivel o fusión.
- Activa el modo enciclopedia en las opciones de visualización para priorizar descubrimientos; está desactivado inicialmente.
- Compara la distribución actual y la sugerida, y mueve los edificios a mano en el orden indicado.
- Las trayectorias y cantidades recolectadas son estimaciones. Llegar desde varios ángulos no implica recoger todo en un solo lanzamiento.

## Privacidad y límites

La conexión solo lee datos: no utiliza parches Harmony, modifica partidas guardadas ni envía controles al juego. Los registros y datos extraídos quedan en `%LOCALAPPDATA%\BallxPitCompanion`; no se cargan registros de juego a internet. Versión inicial probada principalmente con Windows 11, 1920×1080, coreano y juego 1.301. No garantiza distribuciones óptimas ni DPS futuros exactos. No todas las traducciones fueron revisadas por hablantes nativos. No es un producto oficial.

## Problemas y reportes

Si la superposición no aparece, revisa su visibilidad, la ventana del juego y la conexión. En [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues), incluye versiones, idioma, resolución, pasos y resultados esperado y real. Borra información privada de capturas y registros; no subas partidas guardadas ni recursos extraídos.

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
