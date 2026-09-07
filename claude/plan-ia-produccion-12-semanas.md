# Plan de 12 semanas — Producción de techno con IA

**Guía completa con formato:** https://claude.ai/code/artifact/80754b2b-079e-428c-9324-20c1fe0f7fb8

Contexto: Ableton Live 12, Pioneer DDJ-400 (2 canales), techno 150 BPM.
Objetivo: terminar tracks propios · 6–10 hs por semana · herramientas gratis primero.

---

## La regla

**Usá la IA para acelerar el feedback. Nunca para saltear la decisión.**

Separar un track de referencia en capas para estudiarlo: acelera el feedback.
Dejar que un plugin te ecualice la percusión: saltea la decisión.

---

## El stack

### Nivel 1 — Para aprender

| Herramienta | Costo | Para qué |
|---|---|---|
| **Demucs** vía Ultimate Vocal Remover | Gratis, local | Separa cualquier track en drums/bass/vocals/other. La que más enseña de toda la lista. |
| **Este proyecto de Claude** | Ya lo tenés | Diagnóstico y planes, no presets. |
| **Ear training** (SoundGym / TrainYourEars) | Free tier | 10 min por sesión. Sin oído entrenado, cualquier asistente decide por vos. |

### Nivel 2 — Para producir

| Herramienta | Costo | Para qué |
|---|---|---|
| **MIDI Generators de Live 12** | Stock | Rhythm, Euclidean, Seed, Stacks, Velocity Shaper. Mejores que la mayoría de plugins pagos para percusión de techno. |
| **Magenta Studio** (Google) | Gratis, open source | Continue, Groove, Drumify, Interpolate. Viejo, a veces cuesta en Windows, pero *Interpolate* no lo hace nadie más. |
| **Audio a MIDI de Live + Melodyne** | Live: stock | Sacar líneas de referencias para entenderlas. Funciona mucho mejor con Demucs primero. |
| **Scaler 3** | USD 59 | Armonía. En techno tiene poco uso. Saltealo por ahora. |

### Nivel 3 — Todavía no

- **Sonible smart:EQ / iZotope Neutron** — resuelven el síntoma sin que escuches el problema. Revisalos en el mes 6.
- **Ozone Master Assistant** — uso legítimo desde ya: masterizá vos, después dejá que él lo haga sobre el mismo export y **compará qué tocó**. Corrector, no entrega.
- **Suno / Udio** — no dan stems ni control. Único uso real: una atmósfera de maqueta. Ojo con publicar nada de eso.

---

## Las 12 semanas

Cada semana cierra con algo exportado. Si no hay export, no cuenta como hecha.

### Bloque 1 (sem 1–4) — Que un loop suene grande

| # | Foco | Entregable |
|---|---|---|
| 1 | El kick hasta el fondo (decay 280–320 ms, Saturator, Drum Buss) | 3 kicks terminados como presets propios |
| 2 | Rumble + groove (open hat contratiempo, closed hats, una perc) | 3 loops de 8 compases que aguanten 2 min |
| 3 | Ingeniería inversa con Demucs sobre 3 referencias | Doc con qué elementos tiene cada una y dónde entra |
| 4 | Bajo y stabs sin pelear con el kick | 1 loop con bajo audible y kick con pegada |

### Bloque 2 (sem 5–8) — Que el loop sea un track

| # | Foco | Entregable |
|---|---|---|
| 5 | Mapa de energía de una referencia, compás por compás | El mapa dibujado de un track de 6 min |
| 6 | Track 1 estirado a 6 min. Prohibido mejorar sonidos. | Arreglo con intro, break, drop, outro |
| 7 | Transiciones: risers, impactos, filtros y **silencios** | 8 transiciones resueltas |
| 8 | Escucharlo en celular, auto, auriculares, caminando | Export + lista de 5 problemas concretos |

### Bloque 3 (sem 9–12) — Que se pueda escuchar en cualquier lado

| # | Foco | Entregable |
|---|---|---|
| 9 | Mezcla con solo volumen, EQ y paneo. Todo en mono al final. | Track 1 mezclado, pico −6 dBFS |
| 10 | Track 2 completo en dos sesiones (velocidad > perfección) | Track 2 de cero a export en 6 hs |
| 11 | Master a mano + comparación con Ozone Assistant | 2 tracks masterizados + 3 cosas aprendidas |
| 12 | Set de 30 min en la DDJ mezclando tus tracks con referencias | Set grabado + qué corregir en el track 3 |

---

## La semana tipo

| Sesión | Duración | Qué hacés | Cómo termina |
|---|---|---|---|
| A — Producción | 3 hs | El objetivo de la semana. Sin abrir plugins nuevos. | Export |
| B — Producción | 3 hs | Seguís lo de A. No empezás nada nuevo. | Export + línea de diario |
| C — Estudio | 1–2 hs | Demucs sobre una referencia, ear training, o un script | Nota de qué descubriste |

**El diario:** una línea por export, una sola idea de mejora. En 12 semanas son 24 líneas
que son tu curva de aprendizaje. Cada sesión empieza leyendo la última.

---

## Rutina de ingeniería inversa (45 min, semanal)

1. Track que te vuelva loco → UVR con el modelo `htdemucs_ft`
2. Los 4 stems en Live, alineados a su BPM
3. Escuchar **solo el stem de drums** tres veces. Contar elementos distintos.
4. Aislar el kick con paso bajo en 200 Hz: ¿cuánto dura? ¿qué hay entre kick y kick?
5. Recrear **solo la percusión** con sonidos propios, 20 min, sin volver a escuchar el original
6. Comparar. La diferencia es exactamente lo que falta aprender.

**Prueba de las tres salidas** (10 min, fin de cada sesión): monitores → parlante de celular
(si el kick desaparece, falta saturación, no volumen) → mono (si algo se cae, hay fase).

---

## Proyectos de código

| # | Proyecto | Tiempo |
|---|---|---|
| 1 | Generador de patrones euclidianos → `.mid` con `mido`, 50 variaciones de una | 1 tarde |
| 2 | Indexador de librería de samples con `librosa` (BPM, tono, centroide, energía) → SQLite + CLI | 1 fin de semana |
| 3 | Pipeline de Demucs por lotes + proyecto de Live con stems alineados | 2 horas |
| 4 | **Comparador espectral**: FFT promediada de tu track vs referencia (`numpy` + `matplotlib`) | Medio día — el más útil |
| 5 | Asistente de crates: XML de Rekordbox → BPM/Camelot/energía → caminos de mezcla para la DDJ | Conecta con el music bot |

El proyecto 4 es la respuesta directa al pendiente de la guía de kick: te deja ver la zona
grave sin depender de con qué monitoreás.

---

## Cómo usar Claude en este proyecto

```
Estoy en [dónde estás]. Suena [qué escuchás]. Quiero que suene [qué buscás].
¿Qué miro primero?
```
```
Este es el arreglo de mi track: [qué entra en qué compás].
¿Dónde está plano y qué le sacarías?
```
```
Explicame en pasos, con dispositivos stock de Live 12, cómo hacer [efecto].
Nada de plugins pagos.
```
```
Escribime un script en Python que [tarea]. Corre en Windows, salida a [formato].
```

Cada cosa que resolvamos bien se guarda como doc del proyecto. En seis meses hay un manual
propio, con tu vocabulario y sobre tus problemas reales.

---

## Trampas

| Trampa | Por qué |
|---|---|
| Comprar en vez de terminar | El plugin nuevo se siente como progreso. No comprar nada hasta el track 3. |
| Dejar que la IA mezcle | Te resuelve el track de hoy y te deja igual de sordo para el de mañana. |
| El loop eterno | Si algo lleva 2 sesiones sin crecer en duración, se arregla estirándolo, no puliéndolo. |
| Empezar de cero cada vez | La sesión empieza leyendo la última línea del diario. |
| Confiar en el grave que no escuchás | Debajo de 100 Hz en auriculares chicos es una suposición. Analizador + proyecto 4. |

---

## Fuentes

- [Best AI tools for music producers in 2026 — Bridge.audio](https://www.bridge.audio/blog/best-ai-tools-for-music-producers-in-2026/)
- [Best AI Stem Separation Tools 2026 — MixingGPT](https://mixinggpt.com/blog/best-ai-stem-separation-tools-2026)
- [The 12 Best AI Tools for Ableton Live in 2026 — VIXSOUND](https://vixsound.com/blog/best-ai-tools-for-ableton-2026)
- [How I would learn music production if I had to start over in 2026 — SoundGym](https://www.soundgym.co/blog/item?id=learn-music-production-in-2026)
- [MIDI Tools — Ableton](https://www.ableton.com/en/packs/midi-tools/)
