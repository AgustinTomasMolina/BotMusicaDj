"""CLI del motor: llenar la biblioteca y armar sets desde la terminal.

    python -m motor scan <carpeta> --licencia "..." --origen "..."
    python -m motor list
    python -m motor info <track>
    python -m motor similar <track> [-n 10]
    python -m motor radio <track> [--largo 20] [--curva peak] [--semilla N]
                                  [--randomness 0] [--m3u8 set.m3u8]
    python -m motor puente <origen> <destino> [--min-intermedios 3] [--max-intermedios 4]
                                              [--m3u8 puente.m3u8]

(Instalado con `pip install -e .`, `djradio <comando>` es lo mismo.)

LA BASE. `--db <archivo>` en cualquier comando, antes o después del nombre del comando.
Si no se pasa, sale de la variable `DJRADIO_DB` y si tampoco está, de
`~/.djradio/biblioteca.sqlite`. Fuera del directorio de trabajo a propósito: la biblioteca
es una sola aunque se corra desde carpetas distintas, y no termina adentro de un repo.

`<track>` acepta una ruta o un fragmento del nombre del archivo (o del "artista — título").
Si el fragmento coincide con más de un track NO se elige uno: se listan los candidatos y
sale con error. Es la misma regla que `ground_truth.resolver` con los homónimos — elegir a
la suerte hace que el motor trabaje sobre otro track y el resultado se ve igual de bien.

Códigos de salida: 0 hecho · 1 algo no se pudo hacer (archivos que no se analizaron, un
puente que la biblioteca no tiene) ·
2 error de uso (falta licencia/origen, base vacía o de un esquema que este código no conoce,
track inexistente o ambiguo, mutagen sin instalar).

NO modifica los audios: solo los lee (regla del proyecto).
"""
from __future__ import annotations

import argparse
import contextlib
import math
import os
import sys
import time
from pathlib import Path

from calidad.tags import EXTS
from motor.energia import (
    CURVES,
    ascending_positions,
    ascending_spearman,
    energy_curve_deviation,
)
from motor.modelos import DURACION_MINIMA_TRACK_S, Track, es_track, require_text
from motor.tonalidad import camelot_a_clasica

OK = 0
FALLO = 1
USO = 2

VAR_DB = "DJRADIO_DB"
DB_DEFAULT = Path.home() / ".djradio" / "biblioteca.sqlite"


class ErrorDeUso(Exception):
    """Algo que el usuario tiene que corregir. Se imprime el mensaje, sin traceback."""


# --- utilidades ------------------------------------------------------------------------


def db_por_defecto() -> Path:
    return Path(os.environ[VAR_DB]) if os.environ.get(VAR_DB) else DB_DEFAULT


def _clave(ruta: Path | str) -> str:
    """Ruta canónica con la que el scan guarda cada track: absoluta y resuelta, así un
    `info ./x.wav` y un scan de la carpeta padre hablan del mismo registro."""
    return str(Path(ruta).resolve())


def _misma_o_adentro(ruta: Path, carpeta: Path) -> bool:
    """¿`ruta` está dentro de `carpeta`? Comparando con `normcase`: en Windows 'D:\\Musica'
    y 'd:\\musica' son la misma carpeta."""
    r, c = os.path.normcase(str(ruta)), os.path.normcase(str(carpeta))
    return r == c or r.startswith(c.rstrip("\\/") + os.sep)


def _clasica(camelot: str) -> str:
    return camelot_a_clasica(camelot) or "?"


# La key va marcada con `?` salvo que los tramos del track hayan votado TODOS lo mismo
# (spec §6: un dato que miente es peor que uno ausente). La confianza es el ACUERDO entre
# tramos de `tono_consenso`, no el campo `confianza` de `tono()`: está medido que ese no
# predice nada (Pearson +0.02) y que el acuerdo sí (3/3 → 55% exacta, 2/3 → 36%, 1/3 → 26%;
# A/B 2026-09-14). Es la misma regla y la misma función que el reporte del pipeline.
MARCA_DUDOSA = "?"


def key_dudosa(acuerdo: str | None) -> bool:
    """¿Hay que mostrar la key con `?`? Sí salvo acuerdo unánime entre tramos.

    Tres casos que caen del lado del `?`: no unánime ("2/3"), sin evidencia ("0/0" o NULL
    porque la fila se analizó antes de que el scan midiera el acuerdo) y acuerdo ilegible.
    Un acuerdo roto no puede tirar la tabla entera —el resto de los tracks no tiene la
    culpa— y tampoco puede pasar por confiable: es dudoso, como el reporte del pipeline.

    El formato lo lee `benchmark.evaluar.acuerdo_unanime`, que es donde vive esa regla;
    acá no se vuelve a parsear "g/t".
    """
    from benchmark.evaluar import acuerdo_unanime

    try:
        return acuerdo_unanime(acuerdo) is not True
    except (ValueError, TypeError, AttributeError):
        # TypeError/AttributeError: la columna es TEXT, pero un BLOB escrito por fuera vuelve
        # como bytes y `acuerdo_unanime` hace .strip() sobre eso. Sin esto, UNA fila corrupta
        # tiraba `list`, `radio`, `similar` e `info` con traceback para toda la biblioteca.
        return True


def percentil_energia(energia: float) -> int:
    """La energía de un track como se MUESTRA: percentil 0-100 dentro de la biblioteca.

    `Track.energy` ya es ese percentil, pero en 0..1 (lo calcula `store._track` con
    `energia.percentil`). Esto es solo cómo se escribe, y vive acá —al lado de `key_dudosa`
    y `marca_key`, no adentro de `cmd_list`— por el mismo motivo que ellas: la pantalla de
    radio de MusiFlix (`server.py`) muestra el MISMO número que esta terminal, y dos
    redondeos distintos para un solo dato son dos datos (§6). Antes esta cuenta estaba
    escrita tres veces en este archivo: `_fila`, `cmd_list` y `cmd_info`.

    `round` y no `int`: es el mismo redondeo al par que hacía el `format(x, '.0f')` de esas
    tres, así que las tablas imprimen exactamente lo que imprimían.
    """
    return round(energia * 100)


def marca_key(acuerdo: str | None) -> str:
    """Lo que va pegado a la key en las tablas: `"?"` si es dudosa, un espacio si no.

    Un espacio y no cadena vacía: las columnas de `list` tienen que quedar alineadas
    igual con y sin marca, o la tabla se lee como si faltara un campo.
    """
    return MARCA_DUDOSA if key_dudosa(acuerdo) else " "


# Pie de las tablas que muestran keys. Sin esto el `?` es un símbolo mudo: el DJ ve que
# algo pasa pero no qué, y la regla de §6 es justamente no dejarlo adivinar.
LEYENDA_KEY = ("? junto a la key = la detección no es confiable (los tramos del track no "
               "votaron todos lo mismo, o el acuerdo no se midió) · `info <track>` dice cuál "
               "de los dos")


def aviso_fragmentos(cuantos: int, encabezado: str) -> str:
    """El renglón que explica por qué faltan archivos en pantalla.

    Lo usan `radio` y `similar`, que esconden los mismos archivos por el mismo criterio
    (`modelos.es_track`): dos textos distintos para la misma resta harían pensar que son dos
    filtros distintos. Cambia solo el encabezado, porque el verbo no es el mismo ("la radio
    ignoró" / "no se muestran"). Dice que siguen en la biblioteca: el DJ tiene que saber
    dónde están, no solo que no están acá.
    """
    return (f"{encabezado} {cuantos} archivo{'' if cuantos == 1 else 's'} de menos de "
            f"{DURACION_MINIMA_TRACK_S:.0f} s: loops, samples y notas de voz no son tracks "
            f"(siguen en la biblioteca: `list` e `info` los muestran).")


def motivo_semilla_no_track(t: Track) -> str:
    """Por qué la radio no arma un set desde este archivo: no es un track.

    El texto vive acá, y no adentro de `cmd_radio`, porque la MISMA decisión la toma la API
    (`server.py`, `/api/radio/set`): la pantalla de radio rechaza la semilla con el mismo
    criterio (`modelos.es_track`) y tiene que dar el mismo motivo. Dos textos para una sola
    regla se leen como dos reglas distintas — es el mismo argumento que `aviso_fragmentos`.

    Lo que NO va acá es el consejo de cómo elegir otra semilla: `djradio list` es una
    instrucción de terminal y en una pantalla web no significa nada. Cada frontend agrega
    el suyo; `cmd_radio` agrega el de la CLI.
    """
    return (f"{t.label} dura {t.duration:.1f} s y la radio necesita al menos "
            f"{DURACION_MINIMA_TRACK_S:.0f} s: es un loop, un sample o una nota de voz, no "
            f"un track. Su BPM y su key no son datos confiables, y el set entero se arma "
            f"contra ellos.")


def esta_bloqueada(e: Exception) -> bool:
    """¿Este `sqlite3.OperationalError` es "otro proceso tiene la base tomada"?

    Sale de `_abrir_store` para que la API pueda clasificar igual sin copiar la condición:
    dos lugares decidiendo con dos listas de palabras distintas es cómo un "database is
    locked" termina reportado como base corrupta en una pantalla y como "hay otra
    instancia" en la terminal.
    """
    texto = str(e)
    return "locked" in texto or "busy" in texto


def _abrir_store(db: Path):
    """`Store(db)`, con una base de esquema desconocido o bloqueada convertida en error de uso.

    El store rechaza la base al abrirla, antes de tocar ninguna fila; acá solo se le saca el
    traceback para que el usuario lea el motivo.

    Bloqueada (hallazgo H2, tarea 1.2): si otro proceso tiene la base tomada — la está
    migrando, o escribiendo un scan — más de `store.ESPERA_BLOQUEO_S`, SQLite se rinde con
    `OperationalError: database is locked`. No es un bug del usuario ni de la base: es "hay
    otra instancia", y se dice así. Cualquier otro `OperationalError` sigue su camino con
    traceback: esconderlo detrás de un mensaje de uso sería mentir sobre qué pasó."""
    import sqlite3

    from motor import store as modulo_store
    from motor.store import EsquemaIncompatible, Store

    try:
        return Store(db)
    except EsquemaIncompatible as e:
        raise ErrorDeUso(str(e)) from e
    except sqlite3.OperationalError as e:
        if not esta_bloqueada(e):
            raise
        raise ErrorDeUso(
            f"La base {db} está ocupada: otra instancia de djradio la está usando o migrando "
            f"(se esperó {modulo_store.ESPERA_BLOQUEO_S:g} s).\n"
            f"  Esperá a que termine y reintentá.") from e


def _abrir_existente(db: Path):
    """Abre la base para LEER. Si no existe no la crea: un `list` con la ruta mal escrita
    dejaría una base vacía nueva y el próximo comando diría "vacía" en vez de "no existe"."""
    if not db.exists():
        raise ErrorDeUso(
            f"No existe la base {db}.\n"
            f"  Primero analizá una carpeta: python -m motor --db \"{db}\" scan <carpeta> "
            f"--licencia ... --origen ...")
    store = _abrir_store(db)
    if store.count() == 0:
        store.close()
        raise ErrorDeUso(
            f"La base {db} está vacía.\n"
            f"  Analizá una carpeta: python -m motor --db \"{db}\" scan <carpeta> "
            f"--licencia ... --origen ...")
    return store


def clave_lexica(ruta: Path | str) -> str:
    """Clave para comparar dos rutas SIN tocar el disco: absoluta, normalizada y `normcase`.

    `_clave` (con `Path.resolve()`) no sirve para esto: `resolve()` sigue symlinks, o sea
    que le pega al filesystem con la cadena que le den. Acá todo es manipulación de texto —
    `join` con el directorio de trabajo (que deja las absolutas como están), `normpath` y
    `normcase` —, así que una ruta arbitraria, inexistente o una UNC no produce ni un
    `stat`. Ver `resolver_track(consultar_disco=False)` para por qué importa.
    """
    return os.path.normcase(os.path.normpath(os.path.join(os.getcwd(), str(ruta))))


def resolver_track(consulta: str, biblioteca: list[Track], *,
                   consultar_disco: bool = True) -> Track:
    """Ruta exacta, o fragmento del nombre que coincida con UN solo track.

    Ambiguo → `ErrorDeUso` con TODOS los candidatos. No hay "el primero" ni "el más
    parecido": ver el docstring del módulo.

    `consultar_disco=False` para quien recibe la consulta de AFUERA — la API
    (`server.py`, `/api/radio/set`). Con el default, esta función le hace `is_file()` a la
    cadena que le pasen: en la terminal eso es inocuo (la escribe el dueño de la máquina),
    pero por HTTP convierte el endpoint en un oráculo de qué archivos existen en el host
    ("existe pero no está en la base" vs "ningún track coincide") y, con una UNC
    (`\\\\host\\share\\x`), en un `stat` que sale por SMB. El endpoint no tiene CORS y
    escucha en localhost: cualquier página abierta en el navegador puede disparar ese GET.

    Sin disco, una ruta se resuelve igual mientras esté EN la biblioteca (se compara con
    `clave_lexica` contra las rutas ya guardadas); lo que no se puede es preguntar por una
    que no está, que es justamente lo que había que sacar.
    """
    # `is_file` y no `exists`: con una carpeta `crate/` en el directorio de trabajo,
    # `info crate` se tomaba como ruta y contestaba "existe pero no está en la base" en vez
    # de buscar el fragmento. Un track es siempre un archivo.
    if consultar_disco and Path(consulta).is_file():
        clave = os.path.normcase(_clave(consulta))
        for t in biblioteca:
            if os.path.normcase(str(t.path)) == clave:
                return t
        raise ErrorDeUso(
            f"{consulta} existe pero no está en la base.\n"
            f"  Analizalo con: python -m motor scan <carpeta que lo contiene> "
            f"--licencia ... --origen ...")
    if not consultar_disco:
        clave = clave_lexica(consulta)
        for t in biblioteca:
            if clave_lexica(t.path) == clave:
                return t
        # Sin coincidencia NO se dice nada sobre el disco: sigue por fragmento, y si tampoco
        # hay, el error es el mismo para una ruta que existe y para una que no.

    frag = consulta.casefold()
    candidatos = [t for t in biblioteca
                  if frag in t.path.name.casefold() or frag in t.label.casefold()]
    if not candidatos:
        raise ErrorDeUso(f"Ningún track de la base coincide con {consulta!r}.")
    if len(candidatos) > 1:
        lineas = "\n".join(f"    {t.path}" for t in candidatos)
        raise ErrorDeUso(
            f"{consulta!r} coincide con {len(candidatos)} tracks y no elijo uno:\n{lineas}\n"
            f"  Pasá la ruta completa o un fragmento que identifique a uno solo.")
    return candidatos[0]


def _fila(t: Track) -> str:
    """El renglón de un track: BPM con un decimal, las DOS notaciones de key y el `?` de
    confianza cuando la key es dudosa (spec §6)."""
    return (f"{t.bpm:6.1f} BPM  {t.key:>3} {_clasica(t.key):<3}{marca_key(t.key_acuerdo)} "
            f"energía {percentil_energia(t.energy):3d}  {t.label}")


# --- scan ------------------------------------------------------------------------------

_AYUDA_LICENCIA = """\
Falta {faltan}. `scan` los exige y no los inventa (spec §5: licencia y origen son
obligatorios en cualquier track desde el primer día). Se aplican a TODO lo que se
analice en esta corrida, así que escaneá por separado carpetas de procedencia distinta.

  --licencia  con qué derecho tenés el audio: "compra personal", "CC-BY-4.0", "promo del sello"
  --origen    de dónde salió: una URL, la tienda o "biblioteca personal"

Ejemplo para una biblioteca personal comprada en Beatport:

  python -m motor scan "D:\\Musica\\Techno" --licencia "compra personal" --origen "https://www.beatport.com"
"""


def _requerir_mutagen() -> None:
    """`scan` lee artista y título con `calidad.tags.leer_tags`, que importa mutagen recién
    al usarlo. Sin este chequeo, con mutagen ausente el scan hacía el warm-up, analizaba el
    primer track y se caía con `ModuleNotFoundError` antes del primer upsert — con las filas
    de archivos borrados ya quitadas de la base. Se verifica ANTES de abrir la base."""
    try:
        import mutagen  # noqa: F401
    except ImportError as e:
        raise ErrorDeUso(
            "`scan` necesita mutagen para leer artista y título de los tags y no está "
            "instalado.\n"
            "  Instalalo con: pip install mutagen==1.48.1  (o pip install -e . de nuevo)\n"
            "No se tocó la base.") from e


def cmd_scan(args: argparse.Namespace) -> int:
    faltan = [f"--{n}" for n, v in (("licencia", args.licencia), ("origen", args.origen))
              if v is None or not str(v).strip()]
    if faltan:
        # ANTES de abrir la base: `Store(...)` ya crea el archivo y el esquema.
        raise ErrorDeUso(_AYUDA_LICENCIA.format(faltan=" y ".join(faltan)))
    require_text(args.licencia, "licencia")
    require_text(args.origen, "origen")
    _requerir_mutagen()

    carpeta = Path(args.carpeta)
    if not carpeta.is_dir():
        raise ErrorDeUso(f"No existe la carpeta {carpeta} (¿está enchufado el disco?). "
                         f"No se tocó la base.")
    carpeta = carpeta.resolve()

    from calidad.tags import leer_tags
    from motor.analisis import analizar_archivo, calentar

    rutas = sorted({_clave(p) for p in carpeta.rglob("*")
                    if p.is_file() and p.suffix.lower() in EXTS})

    nuevos: list[str] = []
    actualizados: list[str] = []
    sin_cambios: list[str] = []
    fallidos: list[tuple[str, str]] = []
    borrados: list[tuple[str, str]] = []   # (ruta, por qué se quitó de la base)

    with _abrir_store(args.db) as store:
        en_base = {os.path.normcase(str(p)): p for p in store.paths()}
        en_disco = {os.path.normcase(r) for r in rutas}

        # Lo que la base tiene DENTRO de esta carpeta y ya no está en disco. Solo dentro de
        # la carpeta escaneada: escanear otra carpeta no puede borrar la de un pendrive que
        # hoy no está enchufado.
        for clave, ruta in sorted(en_base.items()):
            if _misma_o_adentro(ruta, carpeta) and clave not in en_disco:
                store.delete(ruta)
                borrados.append((str(ruta), "ya no está en disco"))

        pendientes = [r for r in rutas if store.needs_analysis(r)]
        a_analizar = set(pendientes)
        sin_cambios = [r for r in rutas if r not in a_analizar]
        print(f"{len(rutas)} archivos de audio en {carpeta} · "
              f"{len(pendientes)} para analizar · {len(sin_cambios)} sin cambios")

        if pendientes:
            # El JIT de numba compila en la primera llamada: sin esto el primer track tarda
            # ~20× y su tiempo es un artefacto (benchmark/analizar.py, 96 s medidos en frío).
            print(f"  warm-up (compilación JIT): {calentar():.1f} s — no se cuenta", flush=True)

        for i, ruta in enumerate(pendientes, 1):
            nombre = Path(ruta).name
            estaba = os.path.normcase(ruta) in en_base
            try:
                mtime = Path(ruta).stat().st_mtime   # ANTES de analizar: si cambia durante
                t0 = time.perf_counter()             # el análisis, el próximo scan lo rehace
                res = analizar_archivo(ruta, consenso=args.consenso)
                dt = time.perf_counter() - t0
            except Exception as e:  # noqa: BLE001 — un archivo raro no frena 10k tracks
                res, motivo = None, f"{type(e).__name__}: {e}"
            else:
                motivo = "no se pudo decodificar o dura menos de 1 s"

            if res is None:
                fallidos.append((nombre, motivo))
                if estaba:
                    # El archivo cambió y la versión nueva no se puede analizar: el análisis
                    # viejo describe OTRO audio. Dejarlo sería un dato que miente (§6).
                    # Cuenta como borrado: el resumen no puede decir "borrados 0" con una
                    # fila menos en la base.
                    store.delete(ruta)
                    borrados.append((ruta, "cambió y la versión nueva no se pudo analizar: "
                                           "se quitó el análisis anterior"))
                print(f"  [{i}/{len(pendientes)}] {nombre[:48]:48} FALLÓ ({motivo})", flush=True)
                continue

            features, duracion = res
            tags = leer_tags(ruta)
            store.upsert(ruta, features, duration=duracion, license=args.licencia,
                         source_url=args.origen, artist=tags.get("artista") or None,
                         title=tags.get("titulo") or None, mtime=mtime)
            (actualizados if estaba else nuevos).append(nombre)
            print(f"  [{i}/{len(pendientes)}] {nombre[:48]:48} {features.bpm:6.1f} BPM  "
                  f"{features.key:>3} {_clasica(features.key):<3}"
                  f"{marca_key(features.key_acuerdo)} {dt:5.2f} s", flush=True)

        total = store.count()

    print(f"\nnuevos {len(nuevos)} · actualizados {len(actualizados)} · "
          f"sin cambios {len(sin_cambios)} · borrados {len(borrados)} · "
          f"fallidos {len(fallidos)}")
    for ruta, motivo in borrados:
        print(f"  borrado ({motivo}): {ruta}")
    for nombre, motivo in fallidos:
        print(f"  fallido: {nombre} — {motivo}")
    print(f"Base: {args.db} ({total} tracks)")
    return FALLO if fallidos else OK


# --- list / info / similar -------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    with _abrir_existente(args.db) as store:
        biblioteca = store.load_library()   # ordenada por ruta: orden estable
    print(f"{'BPM':>6}      {'key':>3} {'clás':<4} {'energía':>8}  "
          f"artista — título (sin tags: el nombre del archivo)")
    for t in biblioteca:
        print(f"{t.bpm:6.1f} BPM  {t.key:>3} {_clasica(t.key):<4}{marca_key(t.key_acuerdo)} "
              f"{percentil_energia(t.energy):7d}  {t.label}")
    print(f"\n{len(biblioteca)} tracks · energía = percentil dentro de esta biblioteca (0-100)")
    print(LEYENDA_KEY)
    return OK


def _o_no_medido(valor: float | None, formato: str) -> str:
    return "no medido" if valor is None else format(valor, formato)


def _detalle_acuerdo(acuerdo: str | None, tramos: str | None) -> str:
    """El renglón de `info` que explica el `?` (o su ausencia) de la key.

    Distingue los dos "no hay confianza" que la tabla de `list` no puede distinguir, porque
    se arreglan distinto: NULL se arregla volviendo a analizar el archivo, "0/0" no se
    arregla con nada (el track es más corto que los tres tramos disjuntos que hacen falta).
    """
    from benchmark.evaluar import acuerdo_unanime

    # str(): un BLOB escrito por fuera llega como bytes y rompería `.strip()` de más abajo,
    # tirando `info` entero. Se lo trata como ilegible, igual que un texto con otro formato.
    texto = str(acuerdo or "").strip()
    if not texto:
        # NULL, o el vacío que escribiría otra herramienta: en los dos casos nadie midió.
        return ("no medido — este track se analizó antes de que el scan guardara el acuerdo. "
                "La key va con ? hasta que se vuelva a analizar el archivo")
    try:
        unanime = acuerdo_unanime(texto)
    except (ValueError, TypeError, AttributeError):
        return f"ilegible ({acuerdo!r}) — la key va con ?"
    votos = f" ({tramos})" if tramos else ""
    if unanime and not tramos:
        # Fila incoherente: dice que N tramos coincidieron y no guarda ninguno.
        # `Store.upsert` no la deja entrar (`require_acuerdo_key`), así que solo puede venir
        # de una base tocada por fuera. No se narra como confianza: se dice que está rota,
        # porque "todos los tramos votaron la misma key" sin un solo voto es inventar.
        return (f"{texto} — INCOHERENTE: dice {texto.split('/')[-1]} tramos de acuerdo y no "
                f"guarda ninguno; la fila no la escribió djradio")
    if unanime:
        return f"{texto} — todos los tramos votaron la misma key{votos}"
    if not tramos:
        return (f"{texto} — el track no da para comparar tramos disjuntos (hacen falta "
                f"~135 s): la key va con ?")
    return f"{texto} — los tramos no coinciden{votos}: la key va con ?"


def cmd_info(args: argparse.Namespace) -> int:
    with _abrir_existente(args.db) as store:
        t = resolver_track(args.track, store.load_library())
        f = store.get_features(t.path)
    minutos, segundos = divmod(t.duration, 60)
    print(f"{t.label}\n")
    for etiqueta, valor in (
        ("archivo", str(t.path)),
        ("artista", t.artist if t.artist else "(sin tag)"),
        ("título", t.title if t.title else "(sin tag)"),
        # Si no es un track, se dice DONDE se lee la duración y no en una nota al pie: es el
        # único lugar donde el DJ puede enterarse de por qué ese archivo no aparece ni en
        # `radio` ni en `similar` (§6: el dato dudoso se marca donde se lee).
        ("duración", f"{int(minutos)}:{segundos:04.1f} ({t.duration:.1f} s)"
                     + ("" if es_track(t.duration) else
                        f" — menos de {DURACION_MINIMA_TRACK_S:.0f} s: no es un track (loop, "
                        f"sample o nota de voz). `radio` y `similar` no lo proponen")),
        ("BPM", f"{t.bpm:.1f}"),
        # El `?` va pegado a la key y el porqué en el renglón de abajo: el dato dudoso se
        # marca donde se lee, no solo en una nota al pie (§6).
        ("key", f"{t.key} ({_clasica(t.key)}) {marca_key(t.key_acuerdo)}".rstrip()),
        ("acuerdo key", _detalle_acuerdo(f.key_acuerdo, f.key_tramos)),
        ("energía", f"percentil {percentil_energia(t.energy)} de la biblioteca "
                    f"(RMS crudo {f.energy_raw:.4f})"),
        ("rms", _o_no_medido(f.rms, ".4f")),
        ("onsets/s", _o_no_medido(f.onset_rate, ".2f")),
        ("ratio percusivo", _o_no_medido(f.percussive_ratio, ".2f")),
        ("licencia", t.license),
        ("origen", t.source_url),
    ):
        print(f"  {etiqueta:16} {valor}")
    return OK


def cmd_similar(args: argparse.Namespace) -> int:
    from motor.radio import similar

    if args.n < 1:
        raise ErrorDeUso(f"-n tiene que ser al menos 1, recibí {args.n}")
    with _abrir_existente(args.db) as store:
        biblioteca = store.load_library()
    t = resolver_track(args.track, biblioteca)
    print(f"Más parecidos por sonido a: {t.label}")
    print("(similitud coseno del timbre; NO mira BPM ni key — para eso está `radio`)\n")
    parecidos = similar(t, biblioteca, n=args.n)
    # Cuántos escondió `similar`. Se cuenta con `es_track`, la MISMA regla que el filtro, no
    # con una copia del número: si mañana el corte se mueve, el aviso se mueve con él.
    saltados = sum(1 for otro in biblioteca
                   if str(otro.path) != str(t.path) and not es_track(otro.duration))
    if not parecidos:
        print("  la biblioteca no tiene otro track con qué compararlo")
    else:
        for i, (otro, sim) in enumerate(parecidos, 1):
            print(f"  {i:>2}. {sim:+.3f}  {_fila(otro)}")
    if saltados:
        print(f"\n{aviso_fragmentos(saltados, 'No se muestran')}")
    if parecidos:
        print(f"\n{LEYENDA_KEY}")
    return OK


# --- radio -----------------------------------------------------------------------------


# Por debajo de esta cantidad de PUNTOS, el Spearman se imprime como ORIENTATIVO. Medido
# por permutaciones (todas hasta n=8, 200k al azar desde n=9), con n = puntos que entran
# al Spearman: la probabilidad de que un orden AL AZAR dé Spearman ≥ 0.5 es 50% con n=3,
# 15% con n=6, 7% con n=10, 6% con n=11 y recién baja de 5% en n=12 (4.9%). O sea: con
# menos de 12 puntos, "pasó el ≥ 0.5 de §4" no distingue una curva armada de un orden
# cualquiera.
#
# El Spearman de §4 ahora es el del TRAMO ASCENDENTE (`ascending_spearman`), así que los
# puntos son los de ese tramo, no el largo del set: con "peak" entran solo las posiciones
# hasta el 75% (`ascending_positions`). Un set "peak" de 12 tracks aporta 9 puntos y es
# orientativo; con "peak" hacen falta 16 tracks para llegar a 12 puntos.
MIN_PUNTOS_SPEARMAN = 12


def linea_curva(energias: list[float], curva: str = "peak", largo: int | None = None) -> str:
    """El renglón de la curva de energía del set, honesto sobre cuánto significa (§6).

    Imprime las dos métricas de §4: el desvío medio de la curva (principal) y el Spearman del
    tramo ascendente (secundaria, ≥ 0.5). El umbral del desvío de §4 es sobre la MEDIANA de
    muchos sets del benchmark, no sobre un set suelto: por eso acá se muestra el valor y el
    umbral, pero no se aprueba ni se reprueba este set contra él. `largo` es el largo
    PEDIDO del set: si el set quedó corto, la curva contra la que se mide es la que usó
    `build_set`, no una estirada a lo que sonó.
    """
    desvio = energy_curve_deviation(energias, curva, largo)
    rho = ascending_spearman(energias, curva, largo)
    n_puntos = len(ascending_positions(len(energias), curva, largo))

    from benchmark.umbrales import UMBRALES  # una sola fuente del número del contrato

    umbral = next(u for u in UMBRALES if u.clave == "energia_desvio_curva")
    referencia = (f"§4: la mediana de muchos sets ≤ {umbral.limite:g}; un set suelto no se aprueba"
                  if not umbral.a_calibrar else f"umbral a calibrar, {umbral.calibrar}")
    texto = f"curva de energía ({curva}) · desvío medio "
    texto += ("no calculable sin tracks" if math.isnan(desvio)
              else f"{desvio:.3f} ({referencia})")
    texto += " · Spearman tramo ascendente (§4 pide ≥ 0.5): "
    if curva == "flat":
        return texto + "no definido: la curva flat no tiene tramo que suba"
    if math.isnan(rho):
        return (texto + f"no calculable ({n_puntos} puntos en el tramo ascendente, o energía "
                "constante)")
    if n_puntos < MIN_PUNTOS_SPEARMAN:
        return (texto + f"{rho:+.2f} — ORIENTATIVO: con {n_puntos} puntos en el tramo "
                f"ascendente un orden al azar también puede pasar 0.5; se compara contra §4 "
                f"desde {MIN_PUNTOS_SPEARMAN} puntos")
    return texto + f"{rho:+.2f}"


def titular_corte(stop: str | None) -> str:
    """El renglón que encabeza un set corto, uno por código de `RadioSet.stop`.

    Los cuatro cortes se leen distinto porque se arreglan distinto, y decir "no hay más
    tracks compatibles" cuando SÍ los hay y los tapó el `artist_gap` sería exactamente el
    dato que miente de §6. El detalle (`stop_detail`) va abajo con los números; esto es el
    titular, para que no haya que leer un párrafo para saber qué pasó.

    Un código desconocido —una versión nueva de `radio.py` contra una CLI vieja— no se
    inventa: se dice que el set se cortó y el detalle explica el resto.
    """
    from motor.radio import (
        STOP_ARTIST_GAP,
        STOP_BIBLIOTECA_AGOTADA,
        STOP_BIBLIOTECA_VACIA,
        STOP_SIN_MEZCLABLES,
    )

    return {
        STOP_SIN_MEZCLABLES: "NO HAY MÁS TRACKS COMPATIBLES: ninguno de los que quedan "
                             "entra en la tolerancia de BPM",
        STOP_ARTIST_GAP: "NO HAY MÁS TRACKS COMPATIBLES SIN REPETIR ARTISTA: los que "
                         "mezclan están tapados por artist_gap",
        STOP_BIBLIOTECA_AGOTADA: "NO QUEDAN MÁS TRACKS: todos los compatibles ya sonaron",
        STOP_BIBLIOTECA_VACIA: "NO HAY CON QUÉ SEGUIR: la biblioteca no aporta otro track "
                               "además de la semilla",
    }.get(stop, "EL SET SE CORTÓ")


def cmd_radio(args: argparse.Namespace) -> int:
    from motor.export import write_m3u8
    from motor.radio import RadioConfig, build_set

    try:
        config = RadioConfig(length=args.largo, curve=args.curva, seed=args.semilla,
                             randomness=args.randomness, artist_gap=args.artist_gap)
    except ValueError as e:
        raise ErrorDeUso(str(e)) from e

    with _abrir_existente(args.db) as store:
        biblioteca = store.load_library()
    semilla = resolver_track(args.track, biblioteca)
    # La semilla no puede ser un loop ni un sample. No es simetría con el filtro de
    # candidatos: es que TODO el set se arma contra el BPM y la key de la semilla (la
    # compuerta de ±8%, `w_seed`, cada similitud), y en 4 segundos de audio esos dos valores
    # no son una medición. Un set entero apoyado en un dato inventado es peor que un error
    # (§6). La decisión es de acá y no de `build_set`: la librería sigue armando el set si
    # alguien se lo pide a propósito (ver `test_la_semilla_corta_arma_set_igual`).
    if not es_track(semilla.duration):
        raise ErrorDeUso(
            f"{motivo_semilla_no_track(semilla)}\n  Elegí una semilla con `djradio list` "
            f"(o `python -m motor list`), que muestra toda la biblioteca.")

    rset = build_set(semilla, biblioteca, config)
    print(f"Set desde: {semilla.label}  (curva {config.curve}, semilla {config.seed}, "
          f"randomness {config.randomness:g})\n")
    for i, (paso, motivo) in enumerate(zip(rset.steps, rset.reasons(), strict=True), 1):
        print(f"  {i:>2}. {_fila(paso.track)}")
        print(f"      └ {motivo}")

    print(f"\n{len(rset)} de {config.length} tracks pedidos · "
          f"{linea_curva(rset.energies, config.curve, config.length)}")
    # Se dice SIEMPRE, no solo cuando el set queda corto: es la resta entre lo que muestra
    # `list` y entre lo que la radio eligió. Sin esto, con la biblioteca de descargas recién
    # escaneada el DJ ve 64 tracks y un set armado sobre 53, sin ninguna explicación.
    if rset.fragments:
        print(aviso_fragmentos(rset.fragments, "La radio ignoró"))
    if not rset.is_complete:
        print(f"{titular_corte(rset.stop)}.")
        print(f"  El set quedó en {len(rset)} de {config.length} · por qué ({rset.stop}): "
              f"{rset.stop_detail}")
    print(LEYENDA_KEY)

    if args.m3u8:
        destino = write_m3u8(rset.tracks, args.m3u8)
        print(f"M3U8: {destino}")
    return OK


# --- puente ----------------------------------------------------------------------------


def titular_puente(stop: str | None) -> str:
    """El renglón que encabeza un puente que no se pudo armar, uno por código de
    `Bridge.stop` — mismo criterio que `titular_corte`: los motivos se arreglan distinto,
    así que se leen distinto."""
    from motor.puente import STOP_LARGO, STOP_SIN_CAMINO

    return {
        STOP_SIN_CAMINO: "NO HAY PUENTE: ninguna cadena de tracks une los dos BPM dentro de "
                         "la tolerancia",
        STOP_LARGO: "NO HAY PUENTE DE ESE LARGO: hay camino dentro de la tolerancia, pero no "
                    "con esa cantidad de intermedios",
    }.get(stop, "NO HAY PUENTE")


def cmd_puente(args: argparse.Namespace) -> int:
    """`puente <A> <B>`: los temas que llevan de A a B sin un salto fuera de ±8%.

    Salidas: 0 hay puente · 1 no hay (no es un error de uso: la biblioteca no lo tiene, y el
    motivo dice por qué) · 2 error de uso (track inexistente o ambiguo, un extremo que no es
    un track, A y B iguales, rango de intermedios inválido).
    """
    from motor.export import write_m3u8
    from motor.puente import STOP_EXTREMO_NO_TRACK, STOP_MISMO_TRACK, build_bridge

    with _abrir_existente(args.db) as store:
        biblioteca = store.load_library()
    origen = resolver_track(args.origen, biblioteca)
    destino = resolver_track(args.destino, biblioteca)
    try:
        puente = build_bridge(origen, destino, biblioteca, args.min_intermedios,
                              args.max_intermedios)
    except ValueError as e:
        raise ErrorDeUso(str(e)) from e
    if puente.stop == STOP_EXTREMO_NO_TRACK:
        raise ErrorDeUso(f"{puente.stop_detail}.\n  Elegí otro extremo con `djradio list` "
                         f"(o `python -m motor list`), que muestra toda la biblioteca.")
    if puente.stop == STOP_MISMO_TRACK:
        raise ErrorDeUso(f"El {puente.stop_detail}.")

    rango = (f"{args.min_intermedios}" if args.min_intermedios == args.max_intermedios
             else f"entre {args.min_intermedios} y {args.max_intermedios}")
    print(f"Puente desde: {origen.label}")
    print(f"       hasta: {destino.label}")
    print(f"({rango} intermedios; cada salto dentro de ±8% de BPM)\n")
    if not puente.found:
        print(f"{titular_puente(puente.stop)}.")
        print(f"  Por qué ({puente.stop}): {puente.stop_detail}")
        return FALLO

    for i, (paso, motivo) in enumerate(zip(puente.steps, puente.reasons(), strict=True), 1):
        if i == 1:
            # El primero no viene de ninguna parte: no es la "semilla" de una radio, es el
            # origen que eligió el DJ. Mismo formato que el renglón de la semilla.
            motivo = f"origen | {paso.track.key} | {paso.track.bpm:.1f} BPM"
        print(f"  {i:>2}. {_fila(paso.track)}")
        print(f"      └ {motivo}")

    flojo = min(p.transition.mixability for p in puente.steps[1:])
    print(f"\n{len(puente.intermediates)} intermedios · costo {puente.cost:.3f} "
          f"(Σ -log mezclabilidad; 0 = todos los saltos perfectos) · el salto más flojo "
          f"mezcla {flojo:.2f}")
    if puente.fragments:
        print(aviso_fragmentos(puente.fragments, "El puente ignoró"))
    print(LEYENDA_KEY)
    if args.m3u8:
        print(f"M3U8: {write_m3u8(puente.tracks, args.m3u8)}")
    return OK


# --- main ------------------------------------------------------------------------------


def construir_parser() -> argparse.ArgumentParser:
    # `--db` vale antes y después del comando. SUPPRESS en los dos lados para que el default
    # de un subparser no pise el valor que se pasó antes del comando.
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--db", type=Path, default=argparse.SUPPRESS,
                       help=f"Base SQLite de la biblioteca (default: ${VAR_DB} o {DB_DEFAULT}).")
    ap = argparse.ArgumentParser(
        prog="python -m motor", parents=[comun],
        description="Motor de DJ Radio: analizar una carpeta y armar sets mezclables.")
    sub = ap.add_subparsers(dest="comando", required=True, metavar="comando")

    p = sub.add_parser("scan", parents=[comun], help="Analiza una carpeta y actualiza la biblioteca.")
    p.add_argument("carpeta", type=Path)
    p.add_argument("--licencia", default=None,
                   help="Obligatorio. Con qué derecho tenés el audio (ej. \"compra personal\").")
    p.add_argument("--origen", default=None,
                   help="Obligatorio. De dónde salió (URL, tienda o \"biblioteca personal\").")
    p.add_argument("--consenso", action="store_true",
                   help="tono_consenso() para la tonalidad. Es el MISMO flag que "
                        "benchmark.analizar y pipeline.revisar: prenderlo solo acá deja la "
                        "biblioteca con keys distintas de las que mide el benchmark.")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("list", parents=[comun], help="La biblioteca en una tabla.")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("info", parents=[comun], help="Todo lo que se sabe de un track.")
    p.add_argument("track", help="Ruta o fragmento del nombre.")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("similar", parents=[comun], help="Los que más se parecen por sonido.")
    p.add_argument("track", help="Ruta o fragmento del nombre.")
    p.add_argument("-n", type=int, default=10, help="Cuántos (default 10).")
    p.set_defaults(func=cmd_similar)

    p = sub.add_parser("radio", parents=[comun], help="Arma un set desde un track, con el porqué de cada paso.")
    p.add_argument("track", help="Track semilla: ruta o fragmento del nombre.")
    p.add_argument("--largo", type=int, default=20, help="Tracks del set (default 20).")
    p.add_argument("--curva", choices=CURVES, default="peak", help="Curva de energía.")
    p.add_argument("--semilla", type=int, default=0,
                   help="Semilla del azar (solo cuenta con --randomness > 0).")
    p.add_argument("--randomness", type=float, default=0.0,
                   help="0 = determinista puro; hasta 1 = muestrea entre los mejores.")
    p.add_argument("--artist-gap", type=int, default=4,
                   help="Mínimo de tracks entre dos del mismo artista (default 4).")
    p.add_argument("--m3u8", type=Path, default=None, help="Exporta el set a este .m3u8.")
    p.set_defaults(func=cmd_radio)

    p = sub.add_parser("puente", parents=[comun],
                       help="Los temas que llevan de un track a otro sin salirse de ±8% de BPM.")
    p.add_argument("origen", help="Track de salida: ruta o fragmento del nombre.")
    p.add_argument("destino", help="Track de llegada: ruta o fragmento del nombre.")
    p.add_argument("--min-intermedios", type=int, default=3,
                   help="Mínimo de temas entre origen y destino, sin contarlos (default 3; "
                        "0 permite el salto directo).")
    p.add_argument("--max-intermedios", type=int, default=4,
                   help="Máximo de temas entre origen y destino (default 4).")
    p.add_argument("--m3u8", type=Path, default=None, help="Exporta el puente a este .m3u8.")
    p.set_defaults(func=cmd_puente)
    return ap


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(Exception):   # la consola de Windows es cp1252
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = construir_parser().parse_args(argv)
    args.db = getattr(args, "db", None) or db_por_defecto()
    try:
        return args.func(args)
    except ErrorDeUso as e:
        print(f"\n{e}\n", file=sys.stderr)
        return USO


if __name__ == "__main__":
    sys.exit(main())
