# SensFinder

Calibrador de sensibilidad de mouse para FPS. Mide tu puntería en un aim-trainer 3D (flicks y tracking) a distintas sensibilidades, a ciegas, y un modelo estadístico te recomienda una **zona óptima en cm/360**, convertida a la sensibilidad de varios juegos.

Solo para Windows. Hecho con Python estándar (tkinter + ctypes), sin dependencias externas.

## Descarga

Ve a [Releases](../../releases/latest) y descarga `SensFinder.exe`. No necesita instalación.

> El ejecutable no está firmado digitalmente. Windows SmartScreen o el Control de aplicaciones pueden avisar o bloquearlo; si no te fías, ejecútalo desde el código fuente (abajo).

## Qué hace

- **Entrada raw:** lee los counts reales del sensor por Raw Input de Windows, sin aceleración ni velocidad de puntero.
- **Test adaptativo:** un modelo bayesiano (proceso gaussiano) elige qué sensibilidad probar a continuación y para cuando la pérdida esperada de quedarse con su recomendación es pequeña. Descuenta el aprendizaje y la fatiga a lo largo del test.
- **Flicks y tracking:** los flicks se puntúan por throughput de Fitts × precisión (así la dificultad de cada blanco no mete ruido); el tracking, por % de tiempo sobre el blanco.
- **Resultado con incertidumbre:** valor recomendado, zona óptima, intervalo de confianza al 90 % y tres perfiles (ágil / equilibrada / precisa).
- **Comparación con tu sensibilidad actual:** estima la mejora esperada y la probabilidad de que la nueva sea mejor.
- **Sensibilidad vertical:** ajuste opcional de la proporción vertical/horizontal.
- **Memoria entre sesiones:** tus resultados anteriores se usan como información previa con poco peso (`resultados.json`, local).
- **Aviso del siguiente blanco**, **sonidos** y **modo ritmo**: los blancos pueden salir al compás de la música que suena en el PC (captura WASAPI loopback; detecta el tempo y la fase) o de unos BPM manuales.
- **Conversión a juegos:** Valorant, CS2, Apex, Overwatch 2, Fortnite, Call of Duty, Rainbow Six Siege, Minecraft Java, Roblox, PUBG y Rust.

## Ejecutar desde el código

Requiere Python 3.10+ en Windows.

```
python sensfinder.py
```

## Crear el ejecutable

```
build.bat
```

Genera `dist\SensFinder.exe` con PyInstaller.

## Limitaciones

- Los valores de **Roblox, PUBG y Rust** son aproximados (marcados con ≈ en la app): verifícalos dando un giro de 360° en el juego y midiendo los cm.
- El rendimiento humano es ruidoso: con pocas pruebas la zona óptima puede salir ancha. Repetir el test otro día mejora la estimación.
- Un aim-trainer no es una partida real: úsalo para acotar una zona y confirma en juego durante unos días.
- La detección de ritmo funciona mejor con música con bombo marcado; con ritmos irregulares puede fallar.

## Archivos

| Archivo | Contenido |
|---|---|
| `sensfinder.py` | Aplicación, pantallas y lógica del test |
| `engine.py` | Motor estadístico y conversiones de juegos |
| `ui.py` | Mini kit de interfaz sobre tkinter |
| `gdi.py` | Render del juego por GDI (alto FPS) |
| `rawmouse.py` | Raw Input del mouse vía ctypes |
| `music.py` | Captura de audio WASAPI y detección de ritmo |
| `sounds.py` | Sonidos del juego |
