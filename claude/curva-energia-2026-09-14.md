# Calibración del umbral de desvío de la curva de energía — 2026-09-14

Tarea 14 del contrato §4: el umbral `energia_desvio_curva` está en `None` (a calibrar), y
**todavía nada alimenta esa métrica**. Falta decidir DOS cosas mirando datos reales:
1. cómo se **agrega** (mediana / p90 / por-set), y
2. el **número** del umbral.

Este documento fija la distribución medida para poder elegir. Reproducible con:
`python -m benchmark.curva_energia_calibracion --db <store> [--curva peak]`

## Setup
- Biblioteca real escaneada: **197 tracks** (`Musica apelo`) en un store SQLite (`motor scan`).
- Un set generado por cada track semilla con `build_set` (largo 20, `randomness=0` → determinista),
  y `energy_curve_deviation` de cada uno. 197 sets medidos.

## Distribución del desvío por curva
| curva | min | p25 | mediana | p75 | p90 | p95 | max | media | σ |
|---|---|---|---|---|---|---|---|---|---|
| **peak** | 0.082 | 0.145 | **0.177** | 0.213 | 0.249 | 0.270 | 0.328 | 0.181 | 0.050 |
| warmup | 0.098 | 0.150 | 0.174 | 0.201 | 0.229 | 0.240 | 0.294 | 0.178 | 0.038 |
| flat | 0.092 | 0.152 | 0.172 | 0.210 | 0.238 | 0.245 | 0.298 | 0.180 | 0.039 |

Histograma (peak):
```
   0.082–0.102 |   7  #########
   0.102–0.123 |  11  ###############
   0.123–0.143 |  26  ###################################
   0.143–0.164 |  37  ##################################################
   0.164–0.184 |  32  ###########################################
   0.184–0.205 |  25  ##################################
   0.205–0.225 |  25  ##################################
   0.225–0.246 |  12  ################
   0.246–0.266 |   9  ############
   0.266–0.287 |   7  #########
   0.287–0.308 |   1  #
   0.308–0.328 |   5  #######
```
Peores sets (peak): Andres Campo – Ligne Jaune (0.328), Skrillex – move ting (0.315),
Facu Baez – HOT LIKE FIRE (0.314), radical_redemption – scream (0.309).

## Recomendación (la decisión final es del usuario)
El umbral §4 es una **compuerta anti-regresión** ("un cambio que rompe cualquiera no entra"),
no una nota de la biblioteca actual. Debe **pasar el estado sano de hoy con aire** y disparar
solo si un cambio empeora el seguimiento de la curva de forma amplia.

- **Agregación recomendada: la MEDIANA** de los desvíos por-set sobre el benchmark. Es robusta
  a un par de semillas patológicas (que existen y no deberían reprobar todo el build) y sensible
  a que la distribución entera se corra hacia arriba.
- **Umbral recomendado: 0.22** sobre esa mediana. Hoy la mediana es 0.177 (media 0.181, σ 0.05),
  así que 0.22 ≈ mediana + ~0.9σ (≈ p78): deja ~25% de aire sobre el estado actual y reprueba
  si el set típico deja de seguir la curva.

Alternativas, según qué tan estricto lo quieras:
- **Guarda floja / pura anti-regresión:** 0.25 sobre la mediana (más aire).
- **Barra de calidad estricta:** 0.20 (≈ media actual; casi sin aire, cualquier empeoramiento reprueba).
- **Si preferís por-set en vez de agregado:** poné el umbral cerca de 0.30 (apenas sobre p95=0.27,
  bajo el max 0.328): así solo reprueban los 1-2 peores sets, no la variación normal.

Números iguales en las tres curvas (mediana ~0.17-0.18); `peak` tiene la cola más larga, así
que un umbral calibrado sobre `peak` sirve para las tres.

## Pendiente de tarea 14 (además del número)
Escribir el benchmark que GENERA los sets y alimenta `energia_desvio_curva` con el agregado
elegido, y fijar el umbral en `benchmark/umbrales.py` con su test (antes/después, §5).
