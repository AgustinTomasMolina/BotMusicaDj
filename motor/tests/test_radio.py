"""Tests de la radio: `similar()` y `build_set()`.

Todo acá es lógica pura sobre `Track` armados a mano — no se mide audio, así que no hay
ningún BPM ni tonalidad "detectados": los valores son datos de entrada del caso, no
resultados de un análisis (spec §5 prohíbe inventar valores MEDIDOS, y no hay ninguno).

Los embeddings se construyen a mano y NORMALIZADOS a norma 1, que es el contrato de
`Track` (`embeddings.normalize_matrix`): sin eso el producto punto no sería el coseno y
las similitudes esperadas de los tests no significarían nada.
"""
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from motor.energia import ascending_spearman, energy_target  # noqa: E402
from motor.modelos import Track  # noqa: E402
from motor.radio import (  # noqa: E402
    STOP_ARTIST_GAP,
    STOP_BIBLIOTECA_VACIA,
    STOP_SIN_MEZCLABLES,
    RadioConfig,
    bpm_delta_pct,
    build_set,
    key_relation,
    musical_fit,
    similar,
)
from motor.scoring import TOLERANCIA_BPM, bpm_score, mezclabilidad  # noqa: E402
from motor.tonalidad import compat_camelot  # noqa: E402

CAMELOT = [f"{n}{lado}" for lado in ("A", "B") for n in range(1, 13)]


def unit(vec) -> np.ndarray:
    """Vector con norma 1 — el contrato del embedding de `Track`."""
    v = np.asarray(vec, dtype=np.float64)
    return v / np.linalg.norm(v)


def track(nombre: str, bpm: float, key: str = "8A", energy: float = 0.5,
          emb=(1.0, 0.0, 0.0, 0.0), artist: str | None = None) -> Track:
    return Track(
        path=Path("/lib") / nombre,
        duration=300.0,
        bpm=bpm,
        key=key,
        energy=energy,
        embedding=unit(emb),
        license="CC-BY",
        source_url="https://example.org/" + nombre,
        artist=artist,
        title=nombre,
    )


def rutas(rset) -> list[str]:
    return [s.track.path.name for s in rset]


def pitch_real(a: float, b: float) -> float:
    """Cuánto hay que estirar el tempo para mezclar `a` con `b`, en la MEJOR de las tres
    lecturas (mismo, doble y medio tiempo): `1 - lento / rápido` de cada par.

    Escrito ACÁ y de otra forma a propósito. La versión anterior de este helper copiaba la
    fórmula del motor (`min(|a-b|, |a-2b|, |a-b/2|) / max(a, b)`) y por eso no vio que esa
    fórmula medía mal el caso de octava: 100 → 220 daba 4.5% cuando el pitch real es 9.1%.
    Un test que recalcula con la misma cuenta que prueba no prueba nada.
    """
    mejor = math.inf
    for lectura in (b, b * 2, b / 2):
        lento, rapido = sorted((a, lectura))
        mejor = min(mejor, 1.0 - lento / rapido)
    return mejor


def fuera_de_tolerancia(a: float, b: float) -> bool:
    """±8% de §4 contra el pitch real (ver `pitch_real`), no contra `scoring`."""
    return pitch_real(a, b) >= 0.08


# ---------------------------------------------------------------------------
# similar()
# ---------------------------------------------------------------------------

def test_similar_ordena_por_similitud_coseno():
    # Embeddings elegidos para que el orden esperado se pueda calcular a mano:
    # el coseno contra (1,0) es 1.0, 0.8, 0.6 y 0.0 respectivamente.
    semilla = track("semilla.wav", 150, emb=(1.0, 0.0))
    biblio = [
        track("ortogonal.wav", 150, emb=(0.0, 1.0)),   # cos 0.0
        track("clon.wav", 150, emb=(1.0, 0.0)),        # cos 1.0
        track("cerca.wav", 150, emb=(0.8, 0.6)),       # cos 0.8
        track("medio.wav", 150, emb=(0.6, 0.8)),       # cos 0.6
    ]
    out = similar(semilla, biblio, n=4)
    assert [t.path.name for t, _ in out] == ["clon.wav", "cerca.wav", "medio.wav", "ortogonal.wav"]
    assert [round(s, 6) for _, s in out] == [1.0, 0.8, 0.6, 0.0], "el score no es el coseno"


def test_similar_desempata_por_ruta_y_no_por_el_orden_de_entrada():
    semilla = track("semilla.wav", 150, emb=(1.0, 0.0))
    a = track("aaa.wav", 150, emb=(0.8, 0.6))
    b = track("bbb.wav", 150, emb=(0.8, 0.6))   # MISMA similitud que aaa
    c = track("ccc.wav", 150, emb=(0.8, 0.6))
    esperado = ["aaa.wav", "bbb.wav", "ccc.wav"]
    for orden in ([a, b, c], [c, b, a], [b, c, a]):
        out = similar(semilla, orden, n=3)
        assert [t.path.name for t, _ in out] == esperado, (
            "empate de similitud resuelto por el orden de la lista, no por la ruta"
        )


def test_similar_excluye_al_propio_track_por_ruta():
    semilla = track("semilla.wav", 150, emb=(1.0, 0.0))
    copia = track("semilla.wav", 150, emb=(1.0, 0.0))   # mismo archivo, otro objeto
    otro = track("otro.wav", 150, emb=(0.6, 0.8))
    out = similar(semilla, [copia, otro], n=5)
    assert [t.path.name for t, _ in out] == ["otro.wav"]


def test_similar_no_repite_una_ruta_duplicada():
    """Dos cargas iguales del mismo archivo son UN similar, no dos."""
    semilla = track("semilla.wav", 150, emb=(1.0, 0.0))
    a = track("a.wav", 150, emb=(0.8, 0.6))
    a_dup = track("a.wav", 150, emb=(0.8, 0.6))       # mismo archivo, otro objeto, igual
    b = track("b.wav", 150, emb=(0.6, 0.8))
    out = similar(semilla, [a, a_dup, b], n=5)
    assert [t.path.name for t, _ in out] == ["a.wav", "b.wav"], (
        f"la ruta repetida salió dos veces: {[t.path.name for t, _ in out]}"
    )


def test_ruta_duplicada_con_contenido_distinto_es_error():
    """Dos objetos con la misma ruta y DISTINTO análisis: elegir uno dependería del orden de
    la lista (§5). Tanto `build_set` como `similar` lo rechazan en vez de elegir callados."""
    semilla = track("semilla.wav", 150, emb=(1.0, 0.0))
    viejo = track("a.wav", 150, emb=(0.8, 0.6))
    nuevo = track("a.wav", 152, emb=(0.6, 0.8))        # misma ruta, otro análisis
    for orden in ([viejo, nuevo], [nuevo, viejo]):
        with pytest.raises(ValueError, match="misma ruta"):
            build_set(semilla, orden, RadioConfig(length=3))
        with pytest.raises(ValueError, match="misma ruta"):
            similar(semilla, orden, n=3)


def test_similar_bordes():
    t = track("solo.wav", 150, emb=(1.0, 0.0))
    otro = track("otro.wav", 150, emb=(0.6, 0.8))
    assert similar(t, [], n=5) == []                       # biblioteca vacía
    assert similar(t, [t], n=5) == []                      # solo él mismo
    assert similar(t, [otro], n=0) == []                   # n = 0
    assert similar(t, [otro], n=-3) == []                  # n negativo
    out = similar(t, [otro], n=99)                         # n mayor que la biblioteca
    assert [x.path.name for x, _ in out] == ["otro.wav"], "n > biblioteca tiene que devolver lo que hay"


# ---------------------------------------------------------------------------
# El POR QUÉ (§6)
# ---------------------------------------------------------------------------

def test_bpm_delta_pct_coincide_con_la_compuerta():
    """El porcentaje que se MUESTRA y la compuerta que DECIDE tienen que ser el mismo
    número: |pct| < 8 si y solo si el track pasó. Si se separan, la pantalla explica una
    transición que no es la que ocurrió (§6: un dato que miente)."""
    for a in (120.0, 128.0, 150.0, 174.0):
        for b in (60.0, 75.0, 120.0, 127.0, 128.0, 129.5, 138.0, 150.0, 160.0, 175.0, 300.0):
            pct, _ = bpm_delta_pct(a, b)
            pasa = bpm_score(a, b) > 0.0
            assert (abs(pct) < TOLERANCIA_BPM * 100) is pasa, (
                f"{a}→{b}: pct={pct:.3f} dice una cosa y la compuerta otra (pasa={pasa})"
            )


def test_compuerta_y_motivo_miden_el_pitch_real_en_octava():
    """La compuerta y el porcentaje mostrado contra una definición INDEPENDIENTE del motor
    (`pitch_real`), en una grilla que incluye pares de medio/doble tiempo. El test de arriba
    compara el motor contra sí mismo y no puede ver una fórmula mal; este sí: con el
    denominador sin transformar, 100→220 entraba mostrando +4.5% sobre un salto de 9.1%."""
    tempos = (60.0, 72.0, 75.0, 80.0, 87.0, 90.0, 100.0, 110.0, 128.0, 140.0, 150.0, 152.0,
              160.0, 174.0, 187.0, 200.0, 220.0, 256.0)
    for a in tempos:
        for b in tempos:
            real = pitch_real(a, b)
            assert (bpm_score(a, b) > 0.0) is (real < TOLERANCIA_BPM), (
                f"{a}→{b}: pitch real {real:.2%}, la compuerta dice pasa={bpm_score(a, b) > 0}"
            )
            pct, _ = bpm_delta_pct(a, b)
            assert abs(pct) == pytest.approx(real * 100, abs=1e-9), (
                f"{a}→{b}: el motivo dice {pct:+.2f}% y el salto real es {real:.2%}"
            )


def test_octava_fuera_de_tolerancia_corta_el_set_en_los_dos_sentidos():
    """El mismo par no puede entrar o no según cuál sea la semilla (§4)."""
    for semilla_bpm, otro_bpm in ((100.0, 220.0), (220.0, 100.0), (80.0, 174.0), (174.0, 80.0)):
        semilla = track("semilla.wav", semilla_bpm, emb=(1.0, 0.0))
        otro = track("otro.wav", otro_bpm, emb=(1.0, 0.0))
        rset = build_set(semilla, [otro], RadioConfig(length=5))
        assert rutas(rset) == ["semilla.wav"], (
            f"{semilla_bpm}→{otro_bpm} entró con pitch real {pitch_real(semilla_bpm, otro_bpm):.2%}"
        )
        assert rset.stop == STOP_SIN_MEZCLABLES


def test_bpm_delta_pct_tiene_signo_y_lectura_de_octava():
    assert bpm_delta_pct(150.0, 153.0)[0] > 0            # el que entra es más rápido
    assert bpm_delta_pct(150.0, 147.0)[0] < 0            # más lento
    assert abs(bpm_delta_pct(150.0, 153.0)[0] - 3 / 153 * 100) < 1e-9
    assert bpm_delta_pct(150.0, 150.0) == (0.0, "mismo tiempo")
    assert bpm_delta_pct(150.0, 75.0)[1] == "doble tiempo", "75 contra 150 es medio/doble tiempo"
    assert bpm_delta_pct(75.0, 150.0)[1] == "medio tiempo"
    # BPM inválido: dato ausente, no un 0.0 que se lea como 'mismo tempo' (§6).
    assert math.isnan(bpm_delta_pct(0.0, 150.0)[0])
    assert math.isnan(bpm_delta_pct(150.0, float("nan"))[0])


def test_relacion_y_compat_no_se_separan():
    """Cada etiqueta que se muestra tiene que corresponder al valor que puntúa
    `compat_camelot`. Sin este amarre, `key_relation` puede decir 'vecino' de un par que
    el motor puntuó como lejano."""
    # Pares concretos PRIMERO: "vecino" y "relativo" valen los dos 0.8, así que el mapeo
    # numérico de abajo no puede distinguirlos — cruzar las dos etiquetas sería invisible
    # para ese chequeo y visible acá.
    assert key_relation("8A", "8A") == "mismo"
    assert key_relation("8A", "9A") == "vecino"        # ±1 en la rueda, mismo lado
    assert key_relation("8A", "7A") == "vecino"
    assert key_relation("12A", "1A") == "vecino"       # la rueda es circular
    assert key_relation("8A", "8B") == "relativo"      # Am ↔ C: mismo número, otro lado
    assert key_relation("8A", "10A") == "±2"
    assert key_relation("8A", "3B") == "lejano"
    assert key_relation("8A", "9B") == "lejano"        # diagonal: cambia número Y lado

    esperado = {"mismo": 1.0, "vecino": 0.8, "relativo": 0.8, "±2": 0.5, "lejano": 0.2}
    for a in CAMELOT:
        for b in CAMELOT:
            rel = key_relation(a, b)
            assert compat_camelot(a, b) == esperado[rel], f"{a}→{b} etiquetado '{rel}'"
    assert key_relation("8A", "?") == "desconocida"
    assert key_relation("8A", "13Z") == "desconocida"


def test_el_motivo_corresponde_al_track_elegido():
    """§6: la radio muestra POR QUÉ eligió cada track. Si el motivo no describe la
    transición real, es peor que no mostrar nada."""
    rng = np.random.default_rng(7)
    keys = ["8A", "9A", "8B", "7A", "9B"]
    biblio = [
        track(f"t{i:02d}.wav", 150 + (i % 7) * 0.7, keys[i % len(keys)], energy=i / 30,
              emb=unit(rng.standard_normal(6)))
        for i in range(30)
    ]
    cfg = RadioConfig(length=10, seed=1)
    rset = build_set(biblio[0], biblio[1:], cfg)
    assert len(rset) == 10, rset.stop_detail

    assert rset[0].transition.is_seed
    assert rset[0].transition.bpm_delta_pct is None, "la semilla no viene de ninguna transición"
    assert rset[0].transition.energy_goal == pytest.approx(
        energy_target(0, cfg.length, cfg.curve), abs=1e-12), "la meta de energía de la semilla"

    semilla = rset[0].track
    for pos, (anterior, actual) in enumerate(zip(rset.steps, rset.steps[1:], strict=False), 1):
        tr = actual.transition
        prev, cur = anterior.track, actual.track
        assert tr.from_bpm == prev.bpm and tr.to_bpm == cur.bpm
        assert tr.from_key == prev.key and tr.to_key == cur.key
        pct, lectura = bpm_delta_pct(prev.bpm, cur.bpm)
        assert tr.bpm_delta_pct == pct and tr.bpm_octave == lectura
        assert tr.key_relation == key_relation(prev.key, cur.key)
        assert tr.key_compat == compat_camelot(prev.key, cur.key)
        assert tr.energy == cur.energy
        # El renglón de §6 tiene que nombrar las DOS keys reales de la transición.
        assert tr.reason().split("|")[1].strip().startswith(f"{prev.key} → {cur.key}")

        # Los NÚMEROS del porqué, recalculados desde afuera sobre los tracks elegidos. Sin
        # esto, `mixability=encaje` o `energy_goal=0.0` pasaban todos los tests.
        meta = energy_target(pos, cfg.length, cfg.curve)
        assert tr.energy_goal == pytest.approx(meta, abs=1e-12), (
            f"posición {pos}: energy_goal={tr.energy_goal} y la curva pedía {meta}"
        )
        mezcla = mezclabilidad(prev.bpm, cur.bpm, prev.key, cur.key)
        assert tr.mixability == pytest.approx(mezcla, abs=1e-12), (
            f"posición {pos}: mixability={tr.mixability} y la mezclabilidad real es {mezcla}"
        )
        # Redundancia MMR: el mayor coseno contra lo elegido DESPUÉS de la semilla y antes
        # de esta posición (0 si todavía no hay nada).
        previos = [s.track for s in rset.steps[1:pos]]
        redundancia = max((float(cur.embedding @ t.embedding) for t in previos), default=0.0)
        encaje = musical_fit(float(cur.embedding @ semilla.embedding),
                             float(cur.embedding @ prev.embedding), redundancia,
                             cur.energy, meta, cfg)
        assert tr.musical_fit == pytest.approx(encaje, abs=1e-9), (
            f"posición {pos}: musical_fit={tr.musical_fit} y el encaje real es {encaje}"
        )
        assert tr.total == pytest.approx(encaje * mezcla, abs=1e-9), (
            f"posición {pos}: total={tr.total} y encaje × mezclabilidad da {encaje * mezcla}"
        )


# ---------------------------------------------------------------------------
# El contrato de §4: 0% de transiciones fuera de ±8%
# ---------------------------------------------------------------------------

def _biblioteca_trampa() -> list[Track]:
    """Biblioteca donde lo MÁS PARECIDO al timbre de la semilla está fuera de tempo.

    Es el caso que separa un score multiplicativo de uno aditivo: si la mezclabilidad
    compensara en vez de ser compuerta, estos tracks entrarían por parecido.
    """
    biblio = []
    for i, bpm in enumerate((110.0, 118.0, 135.0, 168.0, 172.0, 200.0, 95.0, 210.0)):
        biblio.append(track(f"fuera{i}.wav", bpm, "8A", energy=0.5 + i / 40,
                            emb=(1.0, 0.0, 0.0)))          # coseno 1.0 con la semilla
    for i, bpm in enumerate((150.6, 152.0, 155.0, 158.0, 144.0)):
        biblio.append(track(f"dentro{i}.wav", bpm, "8A", energy=0.3 + i / 10,
                            emb=(0.2, 0.9, 0.1)))          # mucho menos parecidos
    return biblio


def test_cero_transiciones_fuera_de_tolerancia():
    semilla = track("semilla.wav", 150.0, "8A", energy=0.3, emb=(1.0, 0.0, 0.0))
    biblio = _biblioteca_trampa()
    # La trampa tiene que ser trampa de verdad: los parecidos están fuera de tolerancia.
    assert all(fuera_de_tolerancia(150.0, t.bpm) for t in biblio if t.path.name.startswith("fuera"))

    rset = build_set(semilla, biblio, RadioConfig(length=12, seed=3))
    assert len(rset) > 1, "el set ni siquiera arrancó; el caso no prueba nada"

    for anterior, actual in zip(rset.steps, rset.steps[1:], strict=False):
        a, b = anterior.track.bpm, actual.track.bpm
        assert not fuera_de_tolerancia(a, b), (
            f"transición {a}→{b} fuera de ±{TOLERANCIA_BPM:.0%} (spec §4 exige 0%)"
        )
        # El motivo que se muestra también tiene que respetar el contrato: si el renglón
        # dice "+9.1%", o miente o la compuerta se rompió.
        assert abs(actual.transition.bpm_delta_pct) < TOLERANCIA_BPM * 100

    # El umbral es POR TRANSICIÓN: el tempo del set puede derivar de a poco (150 → 158 →
    # 168 son tres saltos legales), así que un track lejano de la SEMILLA no es una
    # violación. Lo que sería violación es que entre de una, saltando desde la semilla, y
    # eso es lo que se verifica: los "fuera" tienen coseno 1.0 contra la semilla, o sea
    # que un score aditivo los pondría primeros compensando el tempo con el parecido.
    assert rutas(rset)[1].startswith("dentro"), (
        f"el primer track del set salió del grupo fuera de tempo: {rutas(rset)[1]}"
    )


def test_se_corta_en_vez_de_aflojar_la_tolerancia():
    """Spec §4: 0% fuera de ±8%. Sin candidatos mezclables el set termina en la semilla —
    NO se afloja la tolerancia como proponía el boceto viejo."""
    semilla = track("semilla.wav", 150.0, "8A", energy=0.3, emb=(1.0, 0.0, 0.0))
    biblio = [t for t in _biblioteca_trampa() if t.path.name.startswith("fuera")]
    rset = build_set(semilla, biblio, RadioConfig(length=20, seed=0))

    assert rutas(rset) == ["semilla.wav"], (
        f"el set siguió con transiciones fuera de tolerancia: {rutas(rset)}"
    )
    assert rset.stop == STOP_SIN_MEZCLABLES
    assert not rset.is_complete
    assert "§4" in rset.stop_detail, "el corte no explica por qué se cortó"


# ---------------------------------------------------------------------------
# Determinismo (§5)
# ---------------------------------------------------------------------------

def _biblioteca_pareja(n: int = 40, seed: int = 11) -> list[Track]:
    """Muchos candidatos mezclables entre sí y de scores parecidos: sin eso, `randomness`
    no tiene entre qué elegir y un test de aleatoriedad pasaría por falta de opciones."""
    rng = np.random.default_rng(seed)
    return [
        track(f"p{i:02d}.wav", 150 + (i % 9) * 0.4, "8A", energy=(i % 10) / 10,
              emb=unit(rng.standard_normal(5)))
        for i in range(n)
    ]


def test_determinismo_con_randomness_cero():
    biblio = _biblioteca_pareja()
    cfg = RadioConfig(length=12, seed=42, randomness=0.0)
    uno = rutas(build_set(biblio[0], biblio[1:], cfg))
    dos = rutas(build_set(biblio[0], biblio[1:], cfg))
    assert uno == dos, "misma semilla y randomness=0 dieron sets distintos (§5)"
    assert len(uno) == 12, "el caso no llegó a armar un set completo"

    # Y tampoco puede depender del orden en que venga la biblioteca.
    revuelta = list(reversed(biblio[1:]))
    assert rutas(build_set(biblio[0], revuelta, cfg)) == uno, (
        "el set cambió al revolver la biblioteca: hay un desempate por orden de entrada"
    )


def test_desempate_estable_cuando_los_candidatos_son_identicos():
    """El caso que de verdad expone un desempate por orden de llegada: cinco candidatos
    con el MISMO bpm, key, energía y embedding, o sea exactamente el mismo score. Con
    embeddings al azar nunca hay dos scores iguales y cualquier criterio de desempate
    parece determinista; acá el empate es exacto y el orden tiene que salir de la ruta."""
    semilla = track("semilla.wav", 150, "8A", energy=0.5, emb=(1.0, 0.0))
    clones = [track(f"clon{i}.wav", 150, "8A", energy=0.5, emb=(1.0, 0.0)) for i in range(5)]
    esperado = ["semilla.wav"] + [f"clon{i}.wav" for i in range(5)]

    for orden in (clones, list(reversed(clones)), [clones[3], clones[0], *clones[1:3], clones[4]]):
        rset = build_set(semilla, orden, RadioConfig(length=6, seed=0))
        assert rutas(rset) == esperado, (
            f"empate exacto resuelto por el orden de entrada: {rutas(rset)}"
        )


def test_determinismo_con_randomness_alta():
    biblio = _biblioteca_pareja()
    base = RadioConfig(length=12, seed=42, randomness=1.0)
    uno = rutas(build_set(biblio[0], biblio[1:], base))
    dos = rutas(build_set(biblio[0], biblio[1:], base))
    assert uno == dos, "misma semilla con randomness>0 dio sets distintos: el RNG no es propio (§5)"

    distintos = {
        tuple(rutas(build_set(biblio[0], biblio[1:],
                              RadioConfig(length=12, seed=s, randomness=1.0))))
        for s in range(6)
    }
    assert len(distintos) > 1, "cambiar la semilla no cambió nada: `randomness` no aleatoriza"

    # randomness=0 y randomness=1 sobre la misma semilla tampoco pueden ser lo mismo,
    # o la perilla no está conectada.
    determinista = rutas(build_set(biblio[0], biblio[1:], RadioConfig(length=12, seed=42)))
    assert any(tuple(determinista) != d for d in distintos)


# ---------------------------------------------------------------------------
# artist_gap y MMR
# ---------------------------------------------------------------------------

def test_artist_gap_no_repite_artista_en_la_ventana():
    rng = np.random.default_rng(5)
    # Cinco artistas para una ventana de tres: con menos artistas que `gap + 1` el filtro
    # es insatisfacible por construcción y el test estaría probando el corte, no el gap.
    artistas = ["Perrotta", "Kobayashi", "Dax J", "Rosa Pistola", "Ellen Allien"]
    biblio = [
        track(f"a{i:02d}.wav", 150 + (i % 5) * 0.5, "8A", energy=(i % 10) / 10,
              emb=unit(rng.standard_normal(5)), artist=artistas[i % len(artistas)])
        for i in range(24)
    ]
    semilla = track("semilla.wav", 150, "8A", energy=0.2, emb=unit(rng.standard_normal(5)),
                    artist="Semillero")
    gap = 3
    rset = build_set(semilla, biblio, RadioConfig(length=10, seed=2, artist_gap=gap))
    assert len(rset) >= 6, f"el set quedó en {len(rset)}: {rset.stop_detail}"

    nombres = [s.track.artist for s in rset]
    for i, quien in enumerate(nombres):
        ventana = nombres[max(0, i - gap):i]
        assert quien not in ventana, f"'{quien}' repetido dentro de {gap} posiciones: {nombres}"

    # Con gap=0 el filtro está apagado y el mismo artista puede repetirse pegado.
    sin_gap = build_set(semilla, biblio, RadioConfig(length=10, seed=2, artist_gap=0))
    assert len(sin_gap) == 10


def test_artist_gap_corta_el_set_en_vez_de_repetir():
    """Decisión documentada en el módulo: si lo único mezclable repite artista, el set
    termina. El corte lo dice, para que quien llama pueda bajar `artist_gap` y reintentar."""
    semilla = track("semilla.wav", 150, "8A", energy=0.5, emb=(1.0, 0.0), artist="Otra")
    biblio = [track(f"u{i}.wav", 150 + i * 0.2, "8A", energy=0.5, emb=(1.0, 0.0), artist="Uno")
              for i in range(5)]
    rset = build_set(semilla, biblio, RadioConfig(length=10, seed=0, artist_gap=4))
    assert rutas(rset) == ["semilla.wav", "u0.wav"], (
        f"repitió artista dentro de la ventana: {rutas(rset)}"
    )
    assert rset.stop == STOP_ARTIST_GAP
    assert "artist_gap" in rset.stop_detail
    # Los mismos tracks, sin el filtro, dan un set completo: lo que corta es el gap.
    assert len(build_set(semilla, biblio, RadioConfig(length=6, seed=0, artist_gap=0))) == 6


def test_el_corte_dice_artist_gap_cuando_el_gap_tapa_al_que_mezclaba():
    """Semilla 150 (artista X). Biblioteca: 151 (artista X), 200 y 100. El único que mezcla
    es el 151, y lo saca el gap. Antes el corte decía `sin_candidatos_mezclables | ninguno de
    los 2 candidatos entra en ±8%` — había 3, y la perilla que destraba el set es
    `artist_gap`, que el motivo ni nombraba."""
    semilla = track("semilla.wav", 150.0, emb=(1.0, 0.0), artist="X")
    biblio = [track("mismo_artista.wav", 151.0, emb=(1.0, 0.0), artist="X"),
              track("rapido.wav", 200.0, emb=(1.0, 0.0)),
              track("lento.wav", 100.0, emb=(1.0, 0.0))]
    rset = build_set(semilla, biblio, RadioConfig(length=5, artist_gap=4))

    assert rutas(rset) == ["semilla.wav"]
    assert rset.stop == STOP_ARTIST_GAP, f"stop={rset.stop!r} | {rset.stop_detail}"
    assert rset.stop_detail == (
        "de los 3 candidatos que quedan, los únicos que entran en ±8% de 150.0 BPM (1) "
        "repiten artista dentro de artist_gap=4; bajar artist_gap los habilita"
    ), rset.stop_detail
    # Y el motivo dice la verdad: bajando el gap, el que mezclaba entra.
    sin_gap = build_set(semilla, biblio, RadioConfig(length=5, artist_gap=0))
    assert rutas(sin_gap)[:2] == ["semilla.wav", "mismo_artista.wav"], rutas(sin_gap)


def test_el_corte_no_culpa_al_gap_si_lo_tapado_tampoco_mezcla():
    """Al revés: si lo que tapa el gap tampoco mezcla, bajar `artist_gap` no cambia nada, y
    el corte tiene que decir `sin_candidatos_mezclables`, aunque TODOS estén tapados."""
    semilla = track("semilla.wav", 150.0, emb=(1.0, 0.0), artist="X")
    biblio = [track("x_rapido.wav", 200.0, emb=(1.0, 0.0), artist="X"),
              track("x_lento.wav", 100.0, emb=(1.0, 0.0), artist="X")]
    rset = build_set(semilla, biblio, RadioConfig(length=5, artist_gap=4))
    assert rset.stop == STOP_SIN_MEZCLABLES, f"stop={rset.stop!r} | {rset.stop_detail}"
    assert rset.stop_detail.startswith("ninguno de los 2 candidatos que quedan"), rset.stop_detail
    assert "de esos, 2 además repiten artista dentro de artist_gap=4" in rset.stop_detail
    assert len(build_set(semilla, biblio, RadioConfig(length=5, artist_gap=0))) == 1


def test_mmr_saca_al_set_del_racimo():
    """Sin MMR el greedy se queda pegado al racimo de tracks casi idénticos: el más
    parecido al anterior es también el más parecido al que venía antes. Con MMR, elegir
    uno del racimo baja el valor de los otros."""
    dim = 10
    e = np.eye(dim)
    # Racimo: ocho tracks casi idénticos entre sí (coseno ≈ 1.0) y parecidos a la semilla
    # (0.7). Variados: parecidos a la semilla casi igual (0.65) pero lejanos entre sí
    # (0.42) y del racimo (0.46). Sin MMR gana siempre el racimo por continuidad.
    racimo = [
        track(f"racimo{i}.wav", 150 + i * 0.3, "8A", energy=0.5,
              emb=unit(0.7 * e[0] + 0.714 * e[1] + 0.004 * i * e[9]))
        for i in range(8)
    ]
    variado = [
        track(f"variado{i}.wav", 150 + i * 0.3, "8A", energy=0.5,
              emb=unit(0.65 * e[0] + 0.76 * e[2 + i]))
        for i in range(6)
    ]
    semilla = track("semilla.wav", 150, "8A", energy=0.5, emb=unit(e[0]))
    biblio = racimo + variado

    def del_racimo(lam: float) -> int:
        rset = build_set(semilla, biblio, RadioConfig(length=9, seed=0, mmr_lambda=lam))
        assert len(rset) == 9, rset.stop_detail
        return sum(1 for n in rutas(rset) if n.startswith("racimo"))

    sin_mmr, con_mmr = del_racimo(0.0), del_racimo(0.9)
    assert con_mmr < sin_mmr, (
        f"MMR no cambió nada: {con_mmr} tracks del racimo con lambda=0.9 contra {sin_mmr} sin MMR"
    )


# ---------------------------------------------------------------------------
# Curva de energía y bordes
# ---------------------------------------------------------------------------

def test_la_curva_de_energia_sube():
    """§4: Spearman del tramo ascendente ≥ 0.5 (con "warmup" el tramo ascendente es el set
    entero). Con una biblioteca entera mezclable, lo único que ordena el set es la curva."""
    rng = np.random.default_rng(21)
    biblio = [
        track(f"e{i:02d}.wav", 150 + (i % 7) * 0.5, "8A", energy=i / 49,
              emb=unit(rng.standard_normal(6)))
        for i in range(50)
    ]
    semilla = track("semilla.wav", 150, "8A", energy=0.05, emb=unit(rng.standard_normal(6)))
    rset = build_set(semilla, biblio, RadioConfig(length=15, curve="warmup", seed=4))
    assert len(rset) == 15, rset.stop_detail
    r = ascending_spearman(rset.energies, "warmup", 15)
    assert r >= 0.5, f"la energía no dibuja ninguna curva: Spearman ascendente {r:.3f} < 0.5 (§4)"


def test_bordes_de_la_biblioteca():
    semilla = track("semilla.wav", 150, "8A", energy=0.4, emb=(1.0, 0.0))

    vacia = build_set(semilla, [], RadioConfig(length=10))
    assert rutas(vacia) == ["semilla.wav"] and vacia.stop == STOP_BIBLIOTECA_VACIA

    # Biblioteca que solo tiene a la propia semilla: tampoco aporta nada, y la semilla no
    # puede repetirse adentro del set.
    solo_semilla = build_set(semilla, [semilla], RadioConfig(length=10))
    assert rutas(solo_semilla) == ["semilla.wav"] and solo_semilla.stop == STOP_BIBLIOTECA_VACIA

    uno = track("uno.wav", 151, "8A", energy=0.6, emb=(0.9, 0.436))
    dos = build_set(semilla, [uno, uno], RadioConfig(length=10))      # duplicado por ruta
    assert rutas(dos) == ["semilla.wav", "uno.wav"], "el mismo archivo entró dos veces"

    largo_uno = build_set(semilla, [uno], RadioConfig(length=1))
    assert rutas(largo_uno) == ["semilla.wav"] and largo_uno.is_complete


def test_ningun_track_se_repite_y_la_semilla_va_primera():
    biblio = _biblioteca_pareja(n=30, seed=3)
    semilla = biblio[0]
    rset = build_set(semilla, biblio, RadioConfig(length=14, seed=9))
    nombres = rutas(rset)
    assert nombres[0] == semilla.path.name, "el set no arranca por la semilla"
    assert len(set(nombres)) == len(nombres), f"hay tracks repetidos: {nombres}"
    assert rset.tracks[0] is semilla


def test_config_valida_lo_que_recibe():
    for kwargs in (
        {"length": 0}, {"curve": "montaña rusa"}, {"randomness": 1.5},
        {"randomness": -0.1}, {"top_k": 0}, {"artist_gap": -1},
    ):
        try:
            RadioConfig(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"RadioConfig aceptó {kwargs}")


if __name__ == "__main__":
    for nombre, fn in sorted(list(globals().items())):
        if nombre.startswith("test_") and callable(fn):
            fn()
            print("ok", nombre)

def test_w_energy_fuera_de_0_1_se_rechaza():
    """`w_energy` es la PROPORCION de mezcla entre timbre y energia, no un peso libre:

    musical_fit devuelve (1 - w_energy) * timbre + w_energy * energia. Con w_energy fuera
    de 0..1 el factor del timbre queda NEGATIVO y el motor pasa a premiar a los candidatos
    que menos se parecen, sin avisar. Medido antes de poner la guarda: con 0.9, 1.6 y 3.0
    salia exactamente el mismo set, porque pasado 1.0 el orden lo decide la energia sola.
    """
    for malo in (1.6, -0.5, 1.0001):
        with pytest.raises(ValueError, match="w_energy"):
            RadioConfig(length=20, seed=1, w_energy=malo)
    for bueno in (0.0, 0.35, 1.0):          # los bordes SI son validos
        assert RadioConfig(length=20, seed=1, w_energy=bueno).w_energy == bueno


def test_pesos_del_encaje_negativos_o_no_finitos_se_rechazan():
    """`w_seed`, `w_prev` y `mmr_lambda` son pesos libres (el tanh de `musical_fit` satura),
    pero negativos invierten lo que premian: `mmr_lambda < 0` premia la redundancia y
    `w_seed < 0` premia NO parecerse a la semilla, en silencio. NaN/inf tampoco."""
    for nombre in ("w_seed", "w_prev", "mmr_lambda"):
        for malo in (-0.1, -1e-9, float("nan"), float("inf")):
            with pytest.raises(ValueError, match=nombre):
                RadioConfig(**{nombre: malo})
        for bueno in (0.0, 0.3, 2.5):       # cero apaga el término; grande satura, no invierte
            assert getattr(RadioConfig(**{nombre: bueno}), nombre) == bueno


def test_mmr_negativo_premiaria_la_redundancia():
    """Por qué la guarda de arriba no es cosmética: medido sin la guarda, `musical_fit` con
    `mmr_lambda < 0` puntúa MÁS al candidato redundante que al que no repite nada."""
    cfg = RadioConfig()
    cfg.mmr_lambda = -0.3                            # después de __post_init__, a propósito
    redundante = musical_fit(0.3, 0.3, 0.9, 0.5, 0.5, cfg)
    fresco = musical_fit(0.3, 0.3, 0.0, 0.5, 0.5, cfg)
    assert redundante > fresco, "con mmr_lambda < 0 la redundancia tiene que salir premiada"
    with pytest.raises(ValueError, match="mmr_lambda"):
        RadioConfig(mmr_lambda=-0.3)
