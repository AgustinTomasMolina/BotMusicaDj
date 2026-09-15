"""Tests de la etapa B (evaluar): cruce, métricas y calibración.

Toda la etapa B es CSV → números, así que se prueba entera sin un solo archivo de audio.
Los BPM y tonalidades de los CSV de ejemplo son valores construidos a mano para ejercitar
cada caso (no se afirma el BPM de ningún track real, que es lo que prohíbe la spec §5).
"""
import csv

import pytest

from benchmark.evaluar import (
    UMBRAL_BPM,
    acierto_por_acuerdo,
    acuerdo_unanime,
    calibracion,
    cruzar,
    informe,
    leer_csv,
    metricas,
)
from benchmark.umbrales import evaluar as evaluar_umbrales

# --- CSVs de ejemplo -------------------------------------------------------------------

COLS_A = ["archivo", "ruta", "duracion_s", "bpm_est", "key_est", "confianza",
          "acuerdo", "tramos", "t_carga_s", "t_analisis_s", "t_total_s", "metodo"]

COLS_GT = ["track_id", "artist", "name", "bpm", "tonality", "camelot",
           "genre", "duration_s", "kind", "num_cues", "location"]


def _fila_a(archivo, bpm, key, conf=1.0, acuerdo="3/3", tramos="", t=3.0,
            metodo="tono_consenso"):
    return {"archivo": archivo, "ruta": f"D:/musica/{archivo}", "duracion_s": "240.0",
            "bpm_est": str(bpm), "key_est": key, "confianza": str(conf),
            "acuerdo": acuerdo, "tramos": tramos, "t_carga_s": "1.0",
            "t_analisis_s": str(round(t - 1.0, 2)), "t_total_s": str(t),
            "metodo": metodo}


def _fila_gt(track_id, archivo, bpm, camelot, artist="X", name="Y"):
    return {"track_id": str(track_id), "artist": artist, "name": name, "bpm": str(bpm),
            "tonality": "", "camelot": camelot, "genre": "", "duration_s": "240",
            "kind": "WAV File", "num_cues": "0",
            "location": f"C:/Users/dj/Escritorio/CRATE/{archivo}"}


def _escribir(tmp_path, nombre, cols, filas):
    p = tmp_path / nombre
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(filas)
    return p


# --- Cruce -----------------------------------------------------------------------------


def test_cruza_por_basename_ignorando_la_ruta():
    """El análisis tiene rutas de D:\\ y el GT rutas viejas del XML: cruza por nombre."""
    a = [_fila_a("track.wav", 128.0, "8A")]
    g = [_fila_gt(1, "track.wav", 128.0, "8A")]
    res = cruzar(a, g)
    assert len(res["cruces"]) == 1
    assert res["cruces"][0].archivo == "track.wav"


def test_cruce_no_distingue_mayusculas():
    """Que salga UNA fila no dice que sea la correcta: podría haber cruzado cualquier cosa.

    Se usan dos archivos con datos distintos para que un cruce equivocado sea detectable:
    si 'Track.WAV' se emparejara con la fila del GT que no le toca, los valores cantarían.
    """
    a = [_fila_a("Track.WAV", 128.0, "8A"), _fila_a("OTRO.MP3", 150.0, "5A")]
    g = [_fila_gt(1, "track.wav", 128.0, "8A", artist="Boltcore", name="Try To Make It"),
         _fila_gt(2, "otro.mp3", 150.0, "5A", artist="JOR", name="SWITCH")]
    cruces = cruzar(a, g)["cruces"]
    assert len(cruces) == 2

    por = {c.archivo: c for c in cruces}
    assert set(por) == {"Track.WAV", "OTRO.MP3"}
    # Cada uno emparejado con SU fila del ground truth, no con la otra.
    assert por["Track.WAV"].artista == "Boltcore" and por["Track.WAV"].bpm_ref == 128.0
    assert por["OTRO.MP3"].artista == "JOR" and por["OTRO.MP3"].bpm_ref == 150.0
    assert por["Track.WAV"].key_ref == "8A" and por["OTRO.MP3"].key_ref == "5A"
    assert por["Track.WAV"].err_crudo == 0.0 and por["OTRO.MP3"].err_crudo == 0.0


def test_homonimos_quedan_fuera_y_se_cuentan():
    """Original vs edit con el mismo nombre: no se elige uno, se excluye."""
    g = [_fila_gt(1, "track.wav", 128.0, "8A"), _fila_gt(2, "track.wav", 150.0, "5A")]
    res = cruzar([_fila_a("track.wav", 128.0, "8A")], g)
    assert res["cruces"] == []
    assert res["ambiguos"] == ["track.wav"]


def test_cuenta_los_que_faltan_de_cada_lado():
    a = [_fila_a("solo_audio.wav", 128.0, "8A"), _fila_a("comun.wav", 128.0, "8A")]
    g = [_fila_gt(1, "comun.wav", 128.0, "8A"), _fila_gt(2, "solo_gt.wav", 130.0, "9A")]
    res = cruzar(a, g)
    assert len(res["cruces"]) == 1
    assert res["solo_analisis"] == ["solo_audio.wav"]
    assert res["solo_gt"] == ["solo_gt.wav"]


# --- BPM: los dos errores ---------------------------------------------------------------


def test_half_time_pasa_el_umbral_pero_deja_el_crudo_grande():
    """El contrato §4 tolera la octava; el crudo es el que delata que difieren."""
    res = cruzar([_fila_a("t.wav", 152.0, "8A")], [_fila_gt(1, "t.wav", 76.0, "8A")])
    c = res["cruces"][0]
    assert c.err_octava == 0.0 and c.veredicto_bpm == "OK"
    assert c.err_crudo == 76.0
    assert c.clase_bpm == "octava"


def test_error_chico_real_rompe_el_umbral():
    res = cruzar([_fila_a("t.wav", 153.2, "8A")], [_fila_gt(1, "t.wav", 152.0, "8A")])
    c = res["cruces"][0]
    assert c.veredicto_bpm == "ROTO"
    assert c.err_crudo == c.err_octava == 1.2
    assert c.clase_bpm == "error-genuino"


def test_los_dos_p95_se_reportan_por_separado():
    """Con puros half-time el tolerante da 0 y el crudo enorme: son métricas distintas."""
    a = [_fila_a(f"t{i}.wav", 152.0, "8A") for i in range(5)]
    g = [_fila_gt(i, f"t{i}.wav", 76.0, "8A") for i in range(5)]
    e = metricas(cruzar(a, g)["cruces"])["extra"]
    assert e["bpm_p95_tolerante"] == 0.0
    assert e["bpm_p95_crudo"] == 76.0


def test_umbral_bpm_sale_de_la_tabla_de_la_spec():
    assert UMBRAL_BPM == 1.0


# --- Tonalidad --------------------------------------------------------------------------


def test_exacta_y_compatible():
    a = [_fila_a("a.wav", 128.0, "8A"), _fila_a("b.wav", 128.0, "9A"),
         _fila_a("c.wav", 128.0, "3B")]
    g = [_fila_gt(1, "a.wav", 128.0, "8A"),   # exacta
         _fila_gt(2, "b.wav", 128.0, "8A"),   # vecina → compatible pero no exacta
         _fila_gt(3, "c.wav", 128.0, "8A")]   # lejana → ni exacta ni compatible
    cruces = cruzar(a, g)["cruces"]
    por = {c.archivo: c for c in cruces}
    assert por["a.wav"].key_exacta == "si" and por["a.wav"].key_compatible == "si"
    assert por["b.wav"].key_exacta == "no" and por["b.wav"].key_compatible == "si"
    assert por["c.wav"].key_exacta == "no" and por["c.wav"].key_compatible == "no"

    m = metricas(cruces)["umbrales"]
    assert m["tonalidad_exacta"] == pytest.approx(100 / 3)
    assert m["tonalidad_compatible"] == pytest.approx(200 / 3)


def test_sin_camelot_en_el_gt_no_cuenta_como_fallo():
    """Rekordbox deja tracks sin tonalidad; no deben contar como error."""
    cruces = cruzar([_fila_a("t.wav", 128.0, "8A")], [_fila_gt(1, "t.wav", 128.0, "")])["cruces"]
    assert cruces[0].key_exacta == "sin-referencia"
    assert "tonalidad_exacta" not in metricas(cruces)["umbrales"]


def _cruces_mezclados():
    """Seis tracks con referencia + uno sin referencia. Todos contra GT 8A.

    Unánimes (3/3): u1 exacta, u2 exacta, u3 vecina (9A: compatible, no exacta), u4 lejana
    (3B). No unánimes: n1 "2/3" lejana, n2 "1/3" lejana. Sin referencia: s1 "3/3".

    A mano:
      contrato (solo unánimes, 4): exacta 2/4 = 50%   compatible 3/4 = 75%
      global (los 6 con ref)     : exacta 2/6 = 33.3% compatible 3/6 = 50%
      cobertura                  : 4/6 = 66.7%  (s1 no cuenta: no tiene referencia)
    """
    a = [_fila_a("u1.wav", 128.0, "8A", acuerdo="3/3"), _fila_a("u2.wav", 128.0, "8A", acuerdo="3/3"),
         _fila_a("u3.wav", 128.0, "9A", acuerdo="3/3"), _fila_a("u4.wav", 128.0, "3B", acuerdo="3/3"),
         _fila_a("n1.wav", 128.0, "3B", acuerdo="2/3"), _fila_a("n2.wav", 128.0, "3B", acuerdo="1/3"),
         _fila_a("s1.wav", 128.0, "8A", acuerdo="3/3")]
    g = [_fila_gt(i, f"{n}.wav", 128.0, "8A") for i, n in
         enumerate(["u1", "u2", "u3", "u4", "n1", "n2"])]
    g.append(_fila_gt(99, "s1.wav", 128.0, ""))
    return cruzar(a, g)


def test_contrato_de_tonalidad_sale_solo_de_los_unanimes():
    met = metricas(_cruces_mezclados()["cruces"])
    m, e = met["umbrales"], met["extra"]
    assert m["tonalidad_exacta"] == pytest.approx(50.0), "el contrato no salió de los unánimes"
    assert m["tonalidad_compatible"] == pytest.approx(75.0), "el contrato no salió de los unánimes"
    assert e["tonalidad_exacta_global"] == pytest.approx(200 / 6)
    assert e["tonalidad_compatible_global"] == pytest.approx(50.0)
    assert e["n_key"] == 6 and e["n_key_unanimes"] == 4
    assert e["cobertura_unanimes"] == pytest.approx(400 / 6)


def test_sin_consenso_el_contrato_de_tonalidad_queda_sin_medir():
    """Análisis con tono() simple: acuerdo vacío en todos. El contrato NO se rellena con la
    cifra global — queda ausente y umbrales lo marca 'sin medir'. La global sí aparece."""
    a = [_fila_a("a.wav", 128.0, "8A", acuerdo=""), _fila_a("b.wav", 128.0, "3B", acuerdo="")]
    g = [_fila_gt(1, "a.wav", 128.0, "8A"), _fila_gt(2, "b.wav", 128.0, "8A")]
    met = metricas(cruzar(a, g)["cruces"])
    assert "tonalidad_exacta" not in met["umbrales"]
    assert "tonalidad_compatible" not in met["umbrales"]
    assert met["extra"]["tonalidad_exacta_global"] == pytest.approx(50.0)
    assert met["extra"]["hay_consenso"] is False
    assert met["extra"]["cobertura_unanimes"] is None
    filas = {f.umbral.clave: f for f in evaluar_umbrales(met["umbrales"])}
    assert filas["tonalidad_exacta"].ok is None and filas["tonalidad_exacta"].valor is None


def test_con_consenso_y_ningun_unanime_la_cobertura_es_cero_y_el_contrato_sin_medir():
    a = [_fila_a("a.wav", 128.0, "8A", acuerdo="2/3"), _fila_a("b.wav", 128.0, "8A", acuerdo="")]
    g = [_fila_gt(1, "a.wav", 128.0, "8A"), _fila_gt(2, "b.wav", 128.0, "8A")]
    met = metricas(cruzar(a, g)["cruces"])
    assert "tonalidad_exacta" not in met["umbrales"]
    assert met["extra"]["hay_consenso"] is True
    assert met["extra"]["cobertura_unanimes"] == 0.0
    assert met["extra"]["tonalidad_exacta_global"] == 100.0


def test_unanimidad_con_otra_cantidad_de_tramos():
    """n_tramos es configurable: '5/5' es unánime, '4/5' no. A mano: el unánime (exacto) da
    100%; si '4/5' (lejano) contara, daría 50%."""
    assert acuerdo_unanime("5/5") is True
    assert acuerdo_unanime("4/5") is False
    assert acuerdo_unanime("3/3") is True
    assert acuerdo_unanime("0/0") is False
    assert acuerdo_unanime("") is None
    with pytest.raises(ValueError, match="formato inesperado"):
        acuerdo_unanime("3 de 3")

    a = [_fila_a("a.wav", 128.0, "8A", acuerdo="5/5"), _fila_a("b.wav", 128.0, "3B", acuerdo="4/5")]
    g = [_fila_gt(1, "a.wav", 128.0, "8A"), _fila_gt(2, "b.wav", 128.0, "8A")]
    met = metricas(cruzar(a, g)["cruces"])
    assert met["umbrales"]["tonalidad_exacta"] == 100.0
    assert met["extra"]["cobertura_unanimes"] == 50.0


def test_informe_imprime_la_cobertura_al_lado_del_contrato(capsys):
    informe(_cruces_mezclados())
    out = capsys.readouterr().out
    assert "CONTRATO — solo tracks con acuerdo unánime de tono_consenso: 50.0% exacta · 75.0% compatible" in out, out
    assert "cobertura del subconjunto: 4/6 (66.7%)" in out, out
    assert "informativo — global, todos los tracks: 33.3% exacta · 50.0% compatible" in out, out
    fila = next(linea for linea in out.splitlines() if linea.strip().startswith("Tonalidad exacta (unánimes)"))
    assert "50.00%" in fila and "[cobertura 4/6]" in fila, fila


def test_informe_sin_acuerdo_dice_la_causa_real_y_no_pide_consenso(capsys):
    """Desde aabd83d la etapa A mide el acuerdo siempre: sin acuerdo es CSV viejo o tracks
    cortos. Pedir --consenso sería mandar a cambiar la key, contra la decisión."""
    a = [_fila_a("a.wav", 128.0, "8A", acuerdo="", metodo="tono")]
    informe(cruzar(a, [_fila_gt(1, "a.wav", 128.0, "8A")]))
    out = capsys.readouterr().out
    lineas = [linea.strip() for linea in out.splitlines()]
    assert ("CONTRATO — sin medir: ningún track con referencia trae acuerdo entre tramos. O el "
            "CSV es anterior a aabd83d (reanalizar con la etapa A actual, que mide el acuerdo "
            "siempre), o todos duran menos de ~135 s y no dan para 3 tramos disjuntos") in lineas, out
    assert "--consenso" not in out, "el informe sigue sugiriendo --consenso, que cambia la key"
    fila = next(linea for linea in lineas if linea.startswith("Tonalidad exacta (unánimes)"))
    assert fila.endswith("· sin medir (sin acuerdo entre tramos: CSV viejo o tracks cortos)"), fila
    assert "informativo — global, todos los tracks: 100.0% exacta" in out, out


# --- B: acierto de la key analizada por acuerdo ------------------------------------------


def _cruces_por_acuerdo():
    """Key de tono() contra GT 8A, un acierto distinto por grupo para que mezclar grupos se note.

    A mano:
      3/3 : a 8A (exacta), b 9A (vecina)        → n=2  exacta 50.0%  compatible 100.0%
      2/3 : c 8A (exacta), d 3B, e 3B (lejanas) → n=3  exacta 33.3%  compatible  33.3%
      1/3 : f 9A (vecina)                       → n=1  exacta  0.0%  compatible 100.0%
      ""  : g 8A (exacta)                       → n=1  exacta 100%   compatible 100.0%
      h 3/3 sin referencia en el GT             → fuera
    """
    filas = [("a", "8A", "3/3"), ("b", "9A", "3/3"), ("c", "8A", "2/3"), ("d", "3B", "2/3"),
             ("e", "3B", "2/3"), ("f", "9A", "1/3"), ("g", "8A", ""), ("h", "8A", "3/3")]
    a = [_fila_a(f"{n}.wav", 128.0, k, acuerdo=ac, metodo="tono") for n, k, ac in filas]
    g = [_fila_gt(i, f"{n}.wav", 128.0, "8A" if n != "h" else "")
         for i, (n, _, _) in enumerate(filas)]
    return cruzar(a, g)


def test_acierto_de_la_key_analizada_por_acuerdo():
    tabla = metricas(_cruces_por_acuerdo()["cruces"])["extra"]["acierto_por_acuerdo"]
    obtenido = [(g["acuerdo"], g["n"], round(g["exacta"], 1), round(g["compatible"], 1))
                for g in tabla]
    assert obtenido == [("3/3", 2, 50.0, 100.0), ("2/3", 3, 33.3, 33.3),
                        ("1/3", 1, 0.0, 100.0), ("sin acuerdo", 1, 100.0, 100.0)], obtenido


def test_acierto_por_acuerdo_ordena_por_acuerdo_y_no_por_como_llegan_los_tracks():
    """Más tramos primero, más acuerdo primero, "sin acuerdo" al final. Los nombres están
    elegidos para que el orden alfabético (el de `cruzar`) sea el INVERSO al esperado: si el
    orden de la tabla saliera de cómo llegan los tracks, este test lo ve."""
    filas = [("a", ""), ("b", "1/3"), ("c", "2/3"), ("d", "3/3"), ("e", "4/5"), ("f", "5/5")]
    a = [_fila_a(f"{n}.wav", 128.0, "8A", acuerdo=ac, metodo="tono") for n, ac in filas]
    g = [_fila_gt(i, f"{n}.wav", 128.0, "8A") for i, (n, _) in enumerate(filas)]
    tabla = acierto_por_acuerdo(cruzar(a, g)["cruces"])
    assert [t["acuerdo"] for t in tabla] == ["5/5", "4/5", "3/3", "2/3", "1/3", "sin acuerdo"]


def test_calibracion_con_metodos_mezclados_no_le_atribuye_la_confianza_a_ninguno(capsys):
    """Un CSV con filas de tono() y de tono_consenso() mezcla dos escalas de confianza que
    no significan lo mismo. Atribuírsela a uno de los dos sería imprimir una causa falsa."""
    a, g = [], []
    for i, (conf, metodo) in enumerate(((1.0, "tono"), (0.33, "tono_consenso"),
                                        (0.67, "tono"), (1.0, "tono_consenso"))):
        a.append(_fila_a(f"t{i}.wav", 128.0, "8A", conf=conf, metodo=metodo))
        g.append(_fila_gt(i, f"t{i}.wav", 128.0, "8A"))
    res = cruzar(a, g)
    cal = calibracion(res["cruces"])
    assert cal["metodo"] == "", f"con métodos mezclados el método tiene que quedar vacío: {cal['metodo']!r}"

    informe(res)
    salida = capsys.readouterr().out
    assert "método mezclado o desconocido: no es una sola escala" in salida, salida
    assert "Krumhansl de tono()" not in salida, "le atribuyó la confianza mezclada a tono()"
    assert "coinciden" not in salida and "tramos distintos" not in salida, salida


def test_acierto_por_acuerdo_no_esconde_un_acuerdo_roto():
    a = [_fila_a("a.wav", 128.0, "8A", acuerdo="2/3/4", metodo="tono")]
    with pytest.raises(ValueError, match="formato inesperado"):
        acierto_por_acuerdo(cruzar(a, [_fila_gt(1, "a.wav", 128.0, "8A")])["cruces"])


def test_informe_imprime_el_acierto_por_acuerdo(capsys):
    informe(_cruces_por_acuerdo())
    lineas = [linea.rstrip() for linea in capsys.readouterr().out.splitlines()]
    i = lineas.index("  acierto de la key analizada (key_est) por acuerdo entre tramos (método: tono):")
    assert lineas[i + 1:i + 5] == [
        "    3/3          n=   2  exacta  50.0% · compatible 100.0%",
        "    2/3          n=   3  exacta  33.3% · compatible  33.3%",
        "    1/3          n=   1  exacta   0.0% · compatible 100.0%",
        "    sin acuerdo  n=   1  exacta 100.0% · compatible 100.0%",
    ], lineas[i:i + 5]


# --- Tiempo -----------------------------------------------------------------------------


def test_cuenta_los_que_pasan_el_umbral_de_tiempo():
    a = [_fila_a("a.wav", 128.0, "8A", t=3.0), _fila_a("b.wav", 128.0, "8A", t=12.0)]
    g = [_fila_gt(1, "a.wav", 128.0, "8A"), _fila_gt(2, "b.wav", 128.0, "8A")]
    e = metricas(cruzar(a, g)["cruces"])["extra"]
    assert e["sobre_umbral_tiempo"] == 1
    assert e["n_tiempo"] == 2


# --- Calibración ------------------------------------------------------------------------


def test_calibracion_detecta_confianza_que_predice():
    """Confianza 1.0 siempre acierta y 0.33 siempre falla → Pearson fuerte y positivo."""
    a, g = [], []
    for i in range(6):
        ok = i < 3
        a.append(_fila_a(f"t{i}.wav", 128.0, "8A" if ok else "3B",
                         conf=1.0 if ok else 0.333, acuerdo="3/3" if ok else "1/3"))
        g.append(_fila_gt(i, f"t{i}.wav", 128.0, "8A"))
    cal = calibracion(cruzar(a, g)["cruces"])
    assert cal["n"] == 6
    assert cal["pearson"] > 0.9
    tramos = {t[0]: t[2] for t in cal["tramos"]}
    assert tramos["1.00 — los 3 coinciden"] == 100.0
    assert tramos["0.33 — los 3 tramos distintos"] == 0.0


def test_calibracion_detecta_confianza_que_no_dice_nada():
    """Aciertos y errores repartidos por igual en cada nivel → Pearson ~0."""
    a, g = [], []
    for i in range(8):
        conf = 1.0 if i % 2 == 0 else 0.333
        acierta = i % 4 < 2
        a.append(_fila_a(f"t{i}.wav", 128.0, "8A" if acierta else "3B", conf=conf))
        g.append(_fila_gt(i, f"t{i}.wav", 128.0, "8A"))
    cal = calibracion(cruzar(a, g)["cruces"])
    assert abs(cal["pearson"]) < 0.3


def test_calibracion_con_tono_no_etiqueta_la_krumhansl_como_tramos(capsys):
    """Con tono() la confianza es la correlación Krumhansl: etiquetarla "los 3 coinciden"
    describiría otra cosa. Las etiquetas quedan numéricas y el informe dice qué es."""
    a, g = [], []
    for i, conf in enumerate((1.0, 0.2, 0.5, 0.8, -0.1)):
        a.append(_fila_a(f"t{i}.wav", 128.0, "8A", conf=conf, metodo="tono"))
        g.append(_fila_gt(i, f"t{i}.wav", 128.0, "8A"))
    res = cruzar(a, g)
    cal = calibracion(res["cruces"])
    assert cal["metodo"] == "tono"
    assert [(t[0], t[1]) for t in cal["tramos"]] == [
        ("< 0.34", 2), ("0.34-0.66", 1), ("0.67-0.98", 1), (">= 0.99", 1)], cal["tramos"]

    informe(res)
    lineas = [linea.strip() for linea in capsys.readouterr().out.splitlines()]
    assert ("confianza = correlación Krumhansl de tono() — NO es acuerdo entre tramos; "
            "el acierto por acuerdo está arriba, en Tonalidad") in lineas, lineas
    assert not any("coinciden" in linea or "tramos distintos" in linea for linea in lineas), lineas


def test_calibracion_vacia_si_no_hay_referencia():
    cal = calibracion(cruzar([_fila_a("t.wav", 128.0, "8A")],
                             [_fila_gt(1, "t.wav", 128.0, "")])["cruces"])
    assert cal["n"] == 0 and cal["pearson"] is None


# --- Ida y vuelta por disco --------------------------------------------------------------


def test_lee_los_csv_de_disco_y_evalua(tmp_path):
    """El camino real: dos archivos CSV en disco, sin audio de por medio."""
    pa = _escribir(tmp_path, "analisis.csv", COLS_A,
                   [_fila_a("uno.wav", 128.0, "8A"), _fila_a("dos.wav", 76.0, "9A")])
    pg = _escribir(tmp_path, "gt.csv", COLS_GT,
                   [_fila_gt(1, "uno.wav", 128.0, "8A"), _fila_gt(2, "dos.wav", 152.0, "9A")])
    res = cruzar(leer_csv(pa), leer_csv(pg))
    assert len(res["cruces"]) == 2
    m = metricas(res["cruces"])
    assert m["umbrales"]["tonalidad_exacta"] == 100.0
    assert m["extra"]["bpm_p95_tolerante"] == 0.0     # el 76 vs 152 es octava
    assert m["extra"]["bpm_p95_crudo"] > 70.0
