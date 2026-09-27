# BALL x PIT Companion — Español (España)

[🌐 Languages](../../README.md#choose-your-language)

Asistente no oficial para BALL x PIT en Windows. Lee el estado del juego y aconseja sobre subidas de nivel, fusiones, desbloqueos de la enciclopedia, recolección y distribución de la base. Tú controlas el juego.

## Instalación

Necesitas Windows 10/11 y tu propia instalación de BALL x PIT en Steam. Si hay un ZIP en [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases), extráelo completo y ejecuta `BallxPitCompanion.exe`, conservando la carpeta `_internal` a su lado. Si no hay ninguna versión publicada, usa el código fuente como se indica abajo. El programa no está firmado y puede activar SmartScreen.

Descarga el repositorio, abre PowerShell en su carpeta, instala uv y ejecuta la configuración:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Después inicia `run_overlay.bat`. La configuración descarga dependencias y BepInEx, y extrae textos e iconos de tu juego. Cierra el juego normalmente para instalar o actualizar la conexión; el instalador espera mientras esté abierto.

## Uso

- Comprueba la conexión en los ajustes y abre una pantalla de subida de nivel o fusión.
- Activa el modo enciclopedia en los ajustes de visualización para priorizar descubrimientos; viene desactivado.
- Compara la distribución actual con la propuesta y mueve los edificios manualmente en el orden indicado.
- Las trayectorias y cantidades recolectadas son estimaciones. Poder llegar desde varios ángulos no significa recogerlo todo en un solo lanzamiento.

## Privacidad y límites

La conexión es de solo lectura: sin parches Harmony, cambios de partidas guardadas ni entradas al juego. Los registros y datos extraídos permanecen en `%LOCALAPPDATA%\BallxPitCompanion`; no se suben registros de juego. Versión inicial probada principalmente con Windows 11, 1920×1080, coreano y juego 1.301. No garantiza distribuciones óptimas ni DPS futuros exactos. No todas las traducciones tienen revisión nativa. No es un producto oficial.

## Problemas e informes

Si no aparece la superposición, comprueba su visibilidad, la ventana y la conexión. En [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues), indica versiones, idioma, resolución, pasos y resultados esperado y real. Elimina información privada de imágenes y registros; no subas partidas guardadas ni recursos extraídos.

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
