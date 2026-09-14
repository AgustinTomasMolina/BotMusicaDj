"""Tests del embedding de timbre (motor/embeddings.py).

Todo el audio sale de `sintetico.click_track`: en esta máquina no hay archivos reales y la
spec §5 prohíbe inventar BPM o tonalidad en un test.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from motor.embeddings import DIM, N_MFCC, embed, normalize_matrix, normalize_one  # noqa: E402
from motor.sintetico import click_track  # noqa: E402
from motor.tonalidad import NOTAS, tono  # noqa: E402


def _coseno(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_dim_sale_de_las_features_reales():
    """DIM no es un número elegido a mano: son las filas que devuelve librosa × 2 (media y
    desvío). El stub viejo decía 96 'ajustar cuando se fije la combinación final'."""
    import librosa

    y, sr = click_track(128.0, dur=10, nota="C", modo="min", seed=1)
    filas = (librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC).shape[0]
             + librosa.feature.chroma_cqt(y=y, sr=sr).shape[0]
             + librosa.feature.tonnetz(chroma=librosa.feature.chroma_cqt(y=y, sr=sr)).shape[0]
             + librosa.feature.spectral_contrast(y=y, sr=sr, n_bands=6).shape[0])
    assert filas == 45, f"la combinación de features cambió: {filas} filas por frame"
    assert 2 * filas == DIM, f"DIM={DIM} no coincide con 2×{filas}"
    assert DIM == 90

    assert embed(y, sr).shape == (DIM,)


def test_embed_layout_y_valores():
    """Cada bloque del vector es la media/desvío temporal de la feature que corresponde,
    y el bloque armónico está ROTADO a la tónica. Los valores esperados salen de librosa
    directamente, no de embeddings.py."""
    import librosa

    y, sr = click_track(128.0, dur=15, nota="D", modo="min", seed=3)
    assert tono(y, sr)["nota"] == "D", "el sintético se generó en D; si no lo detecta, la rotación esperada de este test no aplica"

    v = embed(y, sr)

    mf = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC)
    cg = librosa.feature.chroma_cqt(y=y, sr=sr)
    rot = np.roll(cg, -NOTAS.index("D"), axis=0)          # tónica D → fila 0
    tn = librosa.feature.tonnetz(chroma=rot)
    sc = librosa.feature.spectral_contrast(y=y, sr=sr, n_bands=6)

    def media(f):   # en float64, como el embedding: librosa devuelve float32
        return np.asarray(f, dtype=np.float64).mean(axis=1)

    def desvio(f):
        return np.asarray(f, dtype=np.float64).std(axis=1)

    bloques = [("mfcc.mean", v[0:20], media(mf)), ("mfcc.std", v[20:40], desvio(mf)),
               ("chroma_rel.mean", v[40:52], media(rot)),
               ("chroma_rel.std", v[52:64], desvio(rot)),
               ("tonnetz.mean", v[64:70], media(tn)),
               ("tonnetz.std", v[70:76], desvio(tn)),
               ("contraste.mean", v[76:83], media(sc)),
               ("contraste.std", v[83:90], desvio(sc))]
    for nombre, got, esperado in bloques:
        assert np.allclose(got, esperado, rtol=0, atol=1e-12), \
            f"el bloque {nombre} no es lo que devuelve librosa: {got[:3]} vs {esperado[:3]}"

    # Y el chroma NO es el absoluto: en D el chroma sin rotar es otra cosa.
    assert not np.allclose(v[40:52], cg.mean(axis=1), rtol=0, atol=1e-6), \
        "el bloque de chroma quedó sin rotar (sería dependiente de la key)"


def test_embed_es_determinista():
    """Spec §5: misma entrada → misma salida. Idéntica, no 'parecida'."""
    y, sr = click_track(130.0, dur=12, nota="G", modo="maj", seed=11)
    a, b = embed(y, sr), embed(y, sr)
    assert np.array_equal(a, b), f"dos embeds del mismo audio difieren en {np.abs(a - b).max()}"


def test_desvio_distingue_track_parejo_de_uno_que_cambia_de_seccion():
    """El desvío temporal es la mitad del vector y esta es su razón de ser: un loop que no
    se mueve y un track que cambia de sección tienen medias parecidas y son cosas distintas
    para armar un set."""
    y_parejo, sr = click_track(128.0, dur=24, nota="C", modo="min", seed=1)
    a, _ = click_track(128.0, dur=12, nota="C", modo="min", seed=1)
    b, _ = click_track(128.0, dur=12, nota="F#", modo="min", seed=2)
    y_cambia = np.concatenate([a, b])

    std_parejo = np.linalg.norm(embed(y_parejo, sr)[52:64])   # bloque chroma_rel.std
    std_cambia = np.linalg.norm(embed(y_cambia, sr)[52:64])
    assert std_cambia > 2 * std_parejo, \
        f"el desvío no distingue el cambio de sección: parejo={std_parejo:.4f} cambia={std_cambia:.4f}"


def test_silencio_no_produce_nan():
    """Sin contenido tonal no hay tónica que detectar: se deja sin rotar en vez de inventar
    una key, y el vector tiene que seguir siendo finito."""
    v = embed(np.zeros(22050 * 3, dtype="float32"), 22050)
    assert np.isfinite(v).all(), f"{int((~np.isfinite(v)).sum())} valores no finitos con silencio"
    assert np.array_equal(v[40:64], np.zeros(24)), "el chroma del silencio no es cero"


# --- normalización -------------------------------------------------------------------

# Matriz chica con la aritmética a mano, para fijar los valores sin recalcularlos con el
# código bajo prueba:
#   columnas: media 3 y 3; desvío poblacional sqrt(8/3) = 1.6329931618554518 en las dos
#   z-score → [[-1.2247, +1.2247], [0, -1.2247], [+1.2247, 0]]
#   L2 por fila → [[-1/√2, +1/√2], [0, -1], [+1, 0]]
_M = np.array([[1.0, 5.0], [3.0, 1.0], [5.0, 3.0]])
_ESPERADA = np.array([[-0.7071067811865475, 0.7071067811865475], [0.0, -1.0], [1.0, 0.0]])


def test_normalize_matrix_valores_exactos():
    norm, media, desvio = normalize_matrix(_M)
    assert np.allclose(media, [3.0, 3.0], rtol=0, atol=1e-12), f"media={media}"
    assert np.allclose(desvio, [1.6329931618554518] * 2, rtol=0, atol=1e-12), f"desvio={desvio}"
    assert np.allclose(norm, _ESPERADA, rtol=0, atol=1e-12), f"normalizada=\n{norm}"


def test_normalize_matrix_zscore_antes_de_l2():
    """Las dos propiedades que el orden (z-score y DESPUÉS L2) tiene que dejar: filas de
    norma 1 — para que el producto punto sea coseno — y columnas centradas en la escala que
    devuelve la función."""
    y1, sr = click_track(126.0, dur=10, nota="C", modo="min", seed=1)
    y2, _ = click_track(134.0, dur=10, nota="A", modo="maj", seed=2)
    y3, _ = click_track(140.0, dur=10, nota="F#", modo="min", seed=3)
    m = np.vstack([embed(y1, sr), embed(y2, sr), embed(y3, sr)])

    norm, media, desvio = normalize_matrix(m)
    normas = np.linalg.norm(norm, axis=1)
    assert np.allclose(normas, 1.0, rtol=0, atol=1e-12), f"normas por fila={normas}"

    z = (m - media) / desvio
    assert np.allclose(z.mean(axis=0), 0.0, rtol=0, atol=1e-9), \
        f"columnas sin centrar, |media| máx={np.abs(z.mean(axis=0)).max()}"
    assert np.allclose(z.std(axis=0), 1.0, rtol=0, atol=1e-9), \
        f"columnas sin escalar, desvío mín/máx={z.std(axis=0).min()}/{z.std(axis=0).max()}"

    # El producto punto entre filas normalizadas ES el coseno de los z-scores originales.
    assert abs(float(norm[0] @ norm[1]) - _coseno(z[0], z[1])) < 1e-12


def test_normalize_one_da_lo_mismo_que_la_fila_de_la_biblioteca():
    """Si difieren, la caché de la biblioteca miente: un track recién analizado se compara
    contra vecinos que viven en otra escala."""
    m = np.array([[1.0, 5.0, 2.0], [3.0, 1.0, 9.0], [5.0, 3.0, -4.0], [2.0, 8.0, 0.5]])
    norm, media, desvio = normalize_matrix(m)
    for i in range(m.shape[0]):
        uno = normalize_one(m[i], media, desvio)
        assert np.allclose(uno, norm[i], rtol=0, atol=1e-12), \
            f"fila {i}: normalize_one={uno} vs matriz={norm[i]}"


def test_bordes_desvio_cero_y_norma_cero_sin_nan():
    """Biblioteca de un solo track (desvío 0 en todas las columnas) y un track idéntico a
    la media (norma 0 después del z-score). Ninguno puede devolver NaN."""
    uno_solo = np.array([[4.0, -2.0, 7.0]])
    norm, media, desvio = normalize_matrix(uno_solo)
    assert np.isfinite(norm).all(), f"NaN con un solo track: {norm}"
    assert np.array_equal(norm, np.zeros((1, 3))), f"un solo track debería quedar en 0, dio {norm}"
    assert np.array_equal(desvio, np.ones(3)), f"el desvío devuelto no está saneado: {desvio}"

    # Columna constante entre varios tracks: esa dimensión no aporta, no explota.
    m = np.array([[1.0, 5.0], [3.0, 5.0], [5.0, 5.0]])
    norm, media, desvio = normalize_matrix(m)
    assert np.isfinite(norm).all(), f"NaN con una columna constante: {norm}"
    assert np.array_equal(norm[:, 1], np.zeros(3)), "la columna constante no quedó en 0"

    # Vector exactamente igual a la media → z-score todo 0 → norma 0.
    v = normalize_one(np.array([3.0, 5.0]), media, desvio)
    assert np.isfinite(v).all(), f"NaN al normalizar el vector medio: {v}"
    assert np.array_equal(v, np.zeros(2)), f"el vector medio debería quedar en 0, dio {v}"


if __name__ == "__main__":
    test_dim_sale_de_las_features_reales()
    test_embed_layout_y_valores()
    test_embed_es_determinista()
    test_desvio_distingue_track_parejo_de_uno_que_cambia_de_seccion()
    test_silencio_no_produce_nan()
    test_normalize_matrix_valores_exactos()
    test_normalize_matrix_zscore_antes_de_l2()
    test_normalize_one_da_lo_mismo_que_la_fila_de_la_biblioteca()
    test_bordes_desvio_cero_y_norma_cero_sin_nan()
    print("OK — tests de embeddings pasaron")
