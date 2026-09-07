# Proyecto web para DJs — motor de radio con IA

Sitio de descarga de música gratuita para DJs, con recomendación tipo "estación de radio"
(referencia: SoundCloud). Stack elegido: **Python full (Django/FastAPI)**. Catálogo inicial:
**agregador de fuentes libres**.

---

## 1. Modelo de contenido y marco legal

La versión sostenible del proyecto no distribuye tracks comerciales sin permiso. Lo que sí
puede alojar y sirve al mismo público:

- **Free downloads que los propios artistas regalan** — enorme en techno/house, hoy se
  reparte con gates tipo Hypeddit/ToneDen. Este es el core del producto.
- **Creative Commons** — Free Music Archive, Jamendo, netlabels, Archive.org.
- **Dominio público** y bibliotecas royalty-free.
- **Promos** de sellos chicos que buscan difusión.

Ese encuadre convierte el "rechazo del artista" en alianza: el artista emergente quiere
estar porque recibe tráfico, follows y mails.

Requisitos técnicos que se derivan:

- Campo de **licencia obligatorio** por track (`CC-BY`, `CC-BY-SA`, `CC0`,
  `free-download-artista`) + URL de origen. Sin eso no se puede mostrar la atribución que
  las licencias CC exigen.
- Atribución automática visible en la ficha del track y en la playlist exportada.
- Flujo de **claim / takedown**.
- **Fingerprinting** (Chromaprint/AcoustID) en el pipeline de subida de artistas.

---

## 2. Arquitectura del motor de recomendación

Tres capas. La tesis del producto: *Spotify y SoundCloud recomiendan para escuchar; esto
recomienda para mezclar.*

### Capa 1 — Features por track (offline, una vez)

| Feature | Uso | Método |
|---|---|---|
| BPM | filtrado por tempo | `beat_track` + refinamiento por autocorrelación |
| Tonalidad → Camelot | mixing armónico | perfiles Krumhansl-Schmuckler sobre chroma |
| Loudness, onsets/seg, ratio percusivo | energía percibida | RMS + onsets + separación HPSS |
| Embedding | "cómo suena" | prototipo: MFCC/chroma/tonnetz/contraste. Producción: CLAP o Essentia Discogs-EffNet |

Dos hallazgos del prototipo:

- **El BPM de `librosa.beat_track` viene cuantizado** por la grilla de frames: con
  `hop=512`, ~3 BPM de error a 150 BPM. Inaceptable para DJ. Se corrige autocorrelando la
  envolvente de onsets y buscando en grilla fina el período que mejor explica el pulso.
  Baja el error a **±0.3 BPM**.
- **Chroma relativo**: rotar el chroma para que la tónica quede en el índice 0. El
  embedding captura el carácter armónico sin depender de la tonalidad absoluta — de eso
  ya se encarga la rueda Camelot.

### Capa 2 — Búsqueda vectorial

Prototipo: SQLite + producto matricial numpy. Producción: **Postgres + pgvector** con
índice HNSW (hasta ~1M tracks sobra y es gratis); Qdrant si escala más.

Normalización en dos pasos: z-score por dimensión sobre toda la biblioteca, después L2
por fila para que el producto punto sea similitud coseno.

### Capa 3 — Re-ranking de DJ

**Decisión de diseño central:**

```
score = encaje_musical  ×  mezclabilidad
```

**No es una suma.** Sumando criterios, un track que suena parecidísimo compensa estar
fuera de tempo y chocar de tonalidad, y entra al set igual. Multiplicando, la
mezclabilidad actúa como compuerta.

- `encaje_musical` = similitud con la semilla + similitud con el track anterior −
  redundancia MMR, comprimido con `tanh`, mezclado con el encaje a la curva de energía.
- `mezclabilidad` = `bpm_score^1.0 × key_score^0.6`. El exponente del BPM es más alto
  porque un choque de tonalidad se disimula con EQ; un tema fuera de tempo no se mezcla.

Además: curva de energía del set (`peak` sube hasta el 75% y afloja), energía como
percentil dentro de la biblioteca (no absoluta), MMR contra redundancia, gap mínimo entre
tracks del mismo artista, y soporte explícito de half/double time.

---

## 3. Estado actual

**Prototipo funcional entregado** (`djradio-prototipo.zip`): paquete Python con CLI
(`scan`, `list`, `info`, `similar`, `radio`), export a `.m3u8` para Rekordbox/Traktor,
caché en SQLite y suite de verificación con audio sintético de ground truth.

Resultados de la verificación (14 tracks sintéticos):

```
BPM (±1.0):                              14/14   (error máx. 0.34 BPM)
Tonalidad exacta:                        14/14
Transiciones fuera de tolerancia de BPM:   0/7
Choques armónicos:                         1/7   (biblioteca chica: al paso 7 no queda pool)
Curva de energía sube hacia el pico:        OK
```

Costo de análisis: ~5 s/track.

---

## 4. Próximos pasos

1. Correr el prototipo contra una biblioteca real y ajustar pesos escuchando los sets.
2. Cambiar el embedding hand-crafted por **CLAP** (habilita además búsqueda por texto:
   *"techno hipnótico con bajo rodante"*) o Discogs-EffNet.
3. Migrar el store a **Postgres + pgvector**.
4. API en Django/FastAPI, análisis en cola (**Celery + Redis** o `arq`) porque es lento;
   `/radio?seed=X` sirve desde embeddings precalculados y responde en milisegundos.
5. Ingesta de Free Music Archive / Jamendo / netlabels, con licencia y origen por track.
6. Señales implícitas (skip, descarga, add-to-crate) para la capa colaborativa **después**
   de tener usuarios — el content-based es lo que resuelve el cold start.
