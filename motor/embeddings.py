"""Capa 1b — el vector que representa "cómo suena" un track.

Hoy: embedding hand-crafted (MFCC + chroma relativo a la tónica + tonnetz + contraste
espectral). Anda sin GPU y sin descargar modelos: solo numpy y librosa.

Roadmap: reemplazarlo por CLAP (habilita búsqueda por texto, "techno hipnótico con bajo
rodante") o Discogs-EffNet. La interfaz está pensada para que ese cambio no toque nada
más: mientras `embed()` devuelva un `np.ndarray` 1-D de largo fijo, al resto del motor le
da igual de dónde salió (spec §5, "el embedding tiene que seguir siendo intercambiable").

Nota de diseño — la parte armónica es INDEPENDIENTE DE LA KEY: el chroma se rota a la
tónica antes de resumirlo, y el tonnetz se calcula sobre ese chroma ya rotado. Dos tracks
con el mismo carácter armónico en tonalidades distintas tienen que quedar cerca; para
juzgar si mezclan por key ya está `compat_camelot` (motor/tonalidad.py), que es una
compuerta aparte y no tiene por qué estar duplicada adentro del embedding.
"""
import numpy as np

from motor.tonalidad import chroma_relativo, cromagrama_temporal, ranking_chroma

# librosa se importa DENTRO de `embed` (lo único que lo usa), igual que en
# motor/tonalidad.py: así `normalize_matrix` / `normalize_one` — que son numpy puro y las
# usa cualquier consumidor de la caché de embeddings — se importan sin arrastrar numba.

N_MFCC = 20            # coeficientes de MFCC: timbre y textura
N_BANDAS_CONTRASTE = 6  # librosa devuelve n_bands + 1 filas (una banda extra, la superior)

# Filas por frame que produce cada feature, en el orden en que se concatenan.
_FILAS = (
    ("mfcc", N_MFCC),
    ("chroma_rel", 12),
    ("tonnetz", 6),
    ("contraste", N_BANDAS_CONTRASTE + 1),
)

# DIM sale de las features reales, no de un número elegido a mano: 20 + 12 + 6 + 7 = 45
# filas, × 2 porque de cada una se guarda media Y desvío temporal → 90.
# Si cambia alguna feature, cambia DIM solo; hay un test que lo fija contra lo que devuelve
# librosa, para que no se pueda mover sin que salte.
DIM = 2 * sum(n for _, n in _FILAS)


def embed(y: np.ndarray, sr: int, chroma: np.ndarray | None = None) -> np.ndarray:
    """Vector de timbre de largo `DIM`, SIN normalizar (normalizar es trabajo de
    `normalize_matrix`, que necesita toda la biblioteca para poder hacerlo).

    Concatena, en este orden y por feature primero la media y después el desvío:
      - MFCC (n_mfcc=20) — timbre y textura
      - chroma relativo a la tónica — carácter armónico, independiente de la key
      - tonnetz (sobre el chroma ya rotado) — tensión armónica
      - contraste espectral — cuánto "pega" el espectro por bandas

    El desvío importa tanto como la media: distingue un track que se mantiene igual toda su
    duración de uno que cambia de sección. Un loop de 6 minutos y un tema con breakdown
    pueden tener la misma media y son cosas distintas para armar un set.

    `chroma`: perfil cromático de 12 ya calculado (`tonalidad.cromagrama`). Solo se usa
    para elegir la tónica sobre la que se rota — sirve para que el embedding rote por la
    MISMA tónica que reportó el análisis de tonalidad del track, en vez de re-derivarla y
    arriesgar que difieran. Si es None se deriva del propio cromagrama.

    Recibe el audio YA RECORTADO — no ventanea, igual que `tonalidad.ranking`. Quien llama
    decide cuánto audio analiza (el §4 pide ≤10 s/track).
    """
    import librosa  # perezoso: ver la nota de los imports del módulo

    cg = cromagrama_temporal(y, sr)                       # (12, n_frames)
    perfil = cg.mean(axis=1) if chroma is None else np.asarray(chroma, dtype=np.float64)

    orden = ranking_chroma(perfil)
    # Chroma constante (silencio, o una señal sin contenido tonal): no hay tónica que
    # detectar. Se deja sin rotar en vez de inventar una key — un dato que miente es peor
    # que uno ausente (spec §6), y acá "sin rotar" es honesto: el vector queda igual de
    # constante que la señal que lo produjo.
    if orden:
        cg = chroma_relativo(cg, orden[0][1])

    partes = [
        librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC),
        cg,
        librosa.feature.tonnetz(chroma=cg),
        librosa.feature.spectral_contrast(y=y, sr=sr, n_bands=N_BANDAS_CONTRASTE),
    ]

    trozos = []
    for f in partes:
        f = np.asarray(f, dtype=np.float64)
        trozos.append(f.mean(axis=1))
        trozos.append(f.std(axis=1))
    return np.concatenate(trozos)


def normalize_matrix(matriz: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normaliza la matriz de embeddings de toda la biblioteca (una fila por track).

    Dos pasos, y el ORDEN no es intercambiable:
      1. z-score por dimensión (columna) sobre toda la biblioteca. Las features vienen en
         escalas distintas — el MFCC 0 se mueve en cientos y el chroma entre 0 y 1 —, así
         que sin esto el MFCC 0 solo decidiría la distancia y el resto sería decoración.
      2. L2 por fila, DESPUÉS. Así el producto punto entre dos filas es directamente la
         similitud coseno, que es lo que consulta el motor.
         Al revés no funciona: si se normaliza L2 primero, el z-score posterior vuelve a
         romper la norma 1 de cada fila y el producto punto deja de ser un coseno.

    Devuelve `(matriz_normalizada, media, desvio)`. Guardá media y desvío: hacen falta para
    normalizar un track nuevo (`normalize_one`) sin recalcular toda la biblioteca — y tienen
    que ser EXACTAMENTE estos, si no el track nuevo vive en otra escala que la caché.

    El `desvio` devuelto ya viene saneado: una dimensión constante (biblioteca de un solo
    track, o una feature que no varía) tiene desvío 0 y se reemplaza por 1.0, con lo que esa
    columna queda en 0 en vez de NaN. Una fila con norma 0 (un track idéntico a la media de
    la biblioteca) se deja en ceros por la misma razón.
    """
    m = np.asarray(matriz, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError(f"esperaba una matriz (n_tracks, DIM), recibí shape {m.shape}")

    media = m.mean(axis=0)
    desvio = m.std(axis=0)
    desvio = np.where(desvio > 0, desvio, 1.0)   # dimensión constante → columna en 0, no NaN

    z = (m - media) / desvio
    normas = np.linalg.norm(z, axis=1, keepdims=True)
    return z / np.where(normas > 0, normas, 1.0), media, desvio


def normalize_one(vector: np.ndarray, media: np.ndarray, desvio: np.ndarray) -> np.ndarray:
    """Aplica a un vector suelto la normalización ya ajustada a la biblioteca.

    Tiene que dar EXACTAMENTE la fila que `normalize_matrix` produjo para ese track; si no,
    la caché de la biblioteca miente y un track recién analizado se compara contra vecinos
    que viven en otra escala. Hay un test que lo verifica.

    Repite el saneo de `normalize_matrix` (desvío 0 → 1.0, norma 0 → se deja en ceros) para
    que también funcione con un desvío crudo guardado por fuera.
    """
    v = np.asarray(vector, dtype=np.float64)
    d = np.asarray(desvio, dtype=np.float64)
    z = (v - np.asarray(media, dtype=np.float64)) / np.where(d > 0, d, 1.0)
    norma = float(np.linalg.norm(z))
    return z if norma == 0.0 else z / norma
