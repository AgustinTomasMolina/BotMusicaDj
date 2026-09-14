# A/B de tonalidad: `tono()` vs `tono_consenso()` — 2026-09-14

Corrida de ground truth en la máquina de casa (usuario AgusT), sobre la biblioteca real
de Rekordbox. Los CSV crudos quedaron en `gt_out/` (gitignoreado: tienen rutas personales).
Este documento fija los números para no perderlos.

## Setup (para que sea reproducible)
- venv Python **3.12.10**, stack pineado: librosa 0.11.0, numpy 2.0.2, soundfile 0.14.0.
- Suite completa: **384 passed**.
- XML: `Rekordbox.xml` (220 tracks). Roots de audio: `OneDrive/Escritorio/Musica apelo` + `Music`.
- Resolución: **205/220 OK · 0 ambiguos · 15 no-encontrados** (casi todos `.flac`/`.aiff` sueltos).
- Muestra analizada: **199 tracks** (186 con tonalidad de referencia).
- Comando: `python -m ground_truth.sesion --xml <xml> --roots "<...>/Musica apelo" "<...>/Music" --salida gt_out --si`

## Tonalidad — antes (tono) vs después (tono_consenso)

| Métrica | SIN consenso | CON consenso | Δ |
|---|---|---|---|
| Exacta | **45.7%** | 43.0% | −2.7 |
| Compatible | **57.5%** | 52.7% | −4.8 |

**El consenso NO mejora la precisión; la baja un poco.** `tono()` analiza la ventana central
de 90 s; `tono_consenso` vota la moda de 3 tramos de 45 s, y la moda de ventanas cortas a
veces pega peor que una sola ventana larga.

## El consenso como CONFIANZA (su verdadero valor)

| Acuerdo | n | exacta | compatible |
|---|---|---|---|
| 3/3 | 76 | 55.3% | 59.2% |
| 2/3 | 70 | 35.7% | 52.9% |
| 1/3 | 31 | 25.8% | 32.3% |

- **Pearson confianza↔acierto: +0.156** (consenso) vs **+0.022** (confianza vieja de Krumhansl).
- Monótono: a menos acuerdo, menos acierto. La confianza por consenso **sí predice**; la vieja no.

**Implicación (decisión del usuario, NO se cambió el default):** usar `tono()` para la key y
el acuerdo del consenso solo para mostrar `?` en los `<3/3` (spec §6), no para reemplazar la detección.

## BPM (idéntico en ambos; el consenso solo toca tonalidad)
- p95 **CRUDO 70.01** · p95 **TOLERANTE 0.27** (≤1.0 ✓). El crudo alto = half-time (los ~70 tracks bajo 140 BPM).
- Fuera de umbral: **3** — `Kiss My Accent` (×2, ratio 4/5) e `IsGwan – Dressed` (93→140, tresillo).

## Tiempo por track (carga incluida)
- Medio **1.77 s** · p95 **2.46 s** · **0/199 sobre el umbral de 10 s** ✓.

## Fuera de umbral (con nombre)
- BPM: los 3 de arriba.
- Tonalidad incompatible (ni exacta ni vecina): **79 tracks** — lista completa en el CSV de evaluación.

## Nota de entorno
- `pytest` no está en `requirements.txt`; vive en `requirements-dev.txt` (pin 9.1.1).
- Trampa de OneDrive: un archivo tardó 12 s la 1ª lectura (descarga única → 2.1 s al releer); el
  promedio real es 0.72 s/track. No es sistémico; la sesión es reanudable y lo absorbe.
