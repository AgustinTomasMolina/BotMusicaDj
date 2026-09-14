"""Tests de tonalidad: compat_camelot (lógica) + detección sobre audio sintético."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

from motor.sintetico import click_track  # noqa: E402
from motor.tonalidad import (  # noqa: E402
    NOTAS,
    chroma_relativo,
    compat_camelot,
    cromagrama,
    ranking,
    ranking_chroma,
    tono,
    ventana_central,
)


def test_compat_camelot():
    assert compat_camelot("8A", "8A") == 1.0        # misma
    assert compat_camelot("8A", "9A") == 0.8        # vecina, mismo lado
    assert compat_camelot("8A", "8B") == 0.8        # relativo mayor/menor
    assert compat_camelot("8A", "10A") == 0.5       # ±2 mismo lado
    assert compat_camelot("8A", "3B") == 0.2        # lejana → piso (se tapa con EQ)
    assert compat_camelot("8A", "?") == 0.5         # desconocida → neutro


def test_compat_camelot_consistente_por_distancia():
    # Antes: 8A→10B (cruce, dist 2) valía 0.5 y 8A→9B (diagonal) 0.2, aunque 10B
    # está MÁS lejos. Ahora ±2 solo cuenta en el mismo lado (Fable I1).
    assert compat_camelot("8A", "10B") == 0.2
    assert compat_camelot("8A", "9B") == 0.2
    assert compat_camelot("8A", "10B") <= compat_camelot("8A", "10A")


def test_compat_camelot_ignora_basura():
    assert compat_camelot("8A", "8C") == 0.5        # lado inválido → tratado como '?'
    assert compat_camelot("8A", "13A") == 0.5       # número fuera de 1..12


def test_deteccion_nota_sintetica():
    """Sobre un tono sintético en una nota conocida, detecta esa pitch class."""
    for nota in ["C", "A", "E"]:
        y, sr = click_track(140.0, dur=20, nota=nota)
        det = tono(y, sr)
        assert det["nota"] == nota, f"generé {nota}, detecté {det['nota']}"


def test_deteccion_modo_sintetica():
    """Con la tercera en el sintético, el modo (maj/min) queda CONOCIDO y se detecta
    (tarea #10.1). Antes el sintético solo tenía fundamental+quinta y el modo era ciego."""
    for nota in ["C", "A", "G", "F#"]:
        for modo in ["maj", "min"]:
            y, sr = click_track(140.0, dur=20, nota=nota, modo=modo)
            det = tono(y, sr)
            assert det["nota"] == nota and det["modo"] == modo, \
                f"generé {nota} {modo}, detecté {det['nota']} {det['modo']}"


def test_ranking_refactor_no_cambio_el_resultado():
    """`ranking` ahora se apoya en `cromagrama` + `ranking_chroma` en vez de calcular el
    chroma adentro. Los valores esperados son los que daba la versión anterior sobre este
    mismo audio sintético, capturados ANTES del refactor."""
    y, sr = click_track(128.0, dur=20, nota="A", modo="min", seed=7)
    recorte = ventana_central(y, sr)
    orden = ranking(recorte, sr)

    assert len(orden) == 24, f"esperaba las 24 correlaciones, salieron {len(orden)}"
    esperado = [(0.8343361952618968, "A", "min"), (0.5287442509523532, "A", "maj"),
                (0.3123843124517890, "F", "maj"), (0.3100853800499990, "C", "maj"),
                (0.2144960222846467, "E", "maj")]
    for (corr, nota, modo), (c_e, n_e, m_e) in zip(orden[:5], esperado, strict=True):
        assert (nota, modo) == (n_e, m_e), f"esperaba {n_e} {m_e}, salió {nota} {modo}"
        assert abs(corr - c_e) < 1e-12, f"{nota} {modo}: esperaba {c_e}, salió {corr}"

    # Y es exactamente la misma cuenta que hace quien ya tiene el chroma: si estos dos
    # caminos se separan, el embedding mediría una tonalidad distinta que el motor.
    assert orden == ranking_chroma(cromagrama(recorte, sr))


def test_chroma_relativo_rota_a_la_tonica():
    """Rotación pura: la fila de la tónica pasa al índice 0 y el resto la sigue."""
    base = np.arange(12, dtype=np.float64)
    assert np.array_equal(chroma_relativo(base, "C"), base)          # C ya está en 0
    assert np.array_equal(chroma_relativo(base, "D"), np.roll(base, -2))
    assert chroma_relativo(base, "F#")[0] == base[NOTAS.index("F#")]

    # También sobre un cromagrama (12, n_frames): rota el eje de las pitch classes.
    m = np.arange(24, dtype=np.float64).reshape(12, 2)
    assert np.array_equal(chroma_relativo(m, "D")[0], m[2])


def test_chroma_relativo_acerca_dos_tracks_de_la_misma_forma_en_keys_distintas():
    """Para esto existe la función. Cinco tríadas menores sintéticas, la misma forma en
    cinco keys distintas: en absoluto cada una tiene el pico en otra pitch class, rotadas
    tienen todas el mismo perfil. Si no, el embedding compararía key en vez de carácter."""
    notas = ["C", "G", "A", "F#", "D"]
    absolutos, relativos = {}, {}
    for nota in notas:
        y, sr = click_track(128.0, dur=15, nota=nota, modo="min", seed=5)
        absolutos[nota] = cromagrama(y, sr)
        relativos[nota] = chroma_relativo(absolutos[nota], nota)

    # En TODAS las keys, el perfil rotado pone arriba la tónica (índice 0) y su tercera
    # menor (índice 3) — que es la tríada que generó el sintético.
    for nota, rel in relativos.items():
        top3 = set(np.argsort(rel)[-3:].tolist())
        assert {0, 3} <= top3, \
            f"{nota}: tónica y tercera menor no quedaron arriba tras rotar, top3={sorted(top3)} de {np.round(rel, 3)}"

    def coseno(a, b):
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))

    pares = [(a, b) for a in notas for b in notas if a < b]
    peor_rel = min(coseno(relativos[a], relativos[b]) for a, b in pares)
    mejor_abs = max(coseno(absolutos[a], absolutos[b]) for a, b in pares)
    assert peor_rel > mejor_abs, (
        f"rotar no acercó nada: el par relativo MENOS parecido ({peor_rel:.3f}) tendría que "
        f"superar al par absoluto MÁS parecido ({mejor_abs:.3f})")


def test_chroma_relativo_rechaza_una_nota_que_no_existe():
    try:
        chroma_relativo(np.zeros(12), "H")
    except ValueError as e:
        assert "H" in str(e), f"el error no dice qué nota falló: {e}"
    else:
        raise AssertionError("'H' no es una nota y chroma_relativo la aceptó igual")


def test_silencio_no_inventa_key():
    """Sin señal armónica, NO devolver un Camelot concreto (un dato que miente
    es peor que uno ausente, spec §6 / Fable I4)."""
    det = tono(np.zeros(44100, dtype="float32"), 22050)
    assert det["camelot"] == "?"
    assert det["nota"] is None
    assert det["confianza"] == 0.0


if __name__ == "__main__":
    test_compat_camelot()
    test_compat_camelot_consistente_por_distancia()
    test_compat_camelot_ignora_basura()
    test_deteccion_nota_sintetica()
    test_deteccion_modo_sintetica()
    test_ranking_refactor_no_cambio_el_resultado()
    test_chroma_relativo_rota_a_la_tonica()
    test_chroma_relativo_acerca_dos_tracks_de_la_misma_forma_en_keys_distintas()
    test_chroma_relativo_rechaza_una_nota_que_no_existe()
    test_silencio_no_inventa_key()
    print("OK — tests de tonalidad pasaron")
