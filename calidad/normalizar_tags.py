"""Tarea 5.3 (segunda mitad) — PARSEO de nombres para normalizar tags.

Por ahora SOLO el parseo: propone artista/título a partir del nombre de archivo y muestra
la tabla para revisar. No escribe ningún tag todavía.

    python -m calidad.normalizar_tags --audio <carpeta>

POR QUÉ NO ALCANZA CON "lo de la izquierda es el artista"
---------------------------------------------------------
Es la regla obvia y sobre esta carpeta falla en 8 de 29 archivos, todos con la forma
invertida "TÍTULO - Artista (Original mix)":

    Antü Inan - Fran Perrotta (Original mix)     → el artista es Fran Perrotta
    Static Bow - Fran Perrotta (Original mix)    → el artista es Fran Perrotta
    Think about me - fran perrotta edit          → el artista es fran perrotta

La regla ingenua pondría artista="Antü Inan", artista="Static Bow", artista="Think about
me": ocho artistas inventados, y encima perdería que los ocho son el MISMO artista.

Cómo se detecta sin adivinar: un TÍTULO no se repite entre archivos distintos, un ARTISTA
sí. Así que se cuenta cuántas veces aparece cada nombre de cada lado en toda la carpeta.
Si el lado derecho se repite más que el izquierdo, la forma está invertida. Es evidencia
de los propios datos, no una lista de artistas hardcodeada.

Cuando la evidencia no alcanza, el campo queda VACÍO. Nunca "Unknown" (spec §6).
"""
import argparse
import csv
import datetime
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from calidad.tags import EXTS, leer_tags

# Separador de artista/título. Solo con espacios alrededor: "kylian-dictador" no se parte.
SEPARADOR = re.compile(r"\s+[-–—]\s+")

# Basura que se saca del TEXTO QUE VA AL TAG. El archivo no se toca ni se renombra.
LIMPIEZA = [
    (re.compile(r"\s*myfreemp3\.vip\s*", re.I), "", "sitio de descarga"),
    (re.compile(r"\s*\(\s*cut\s*\)\s*", re.I), " ", "'(cut)'"),
    (re.compile(r"\s*\(\d+\)\s*$"), "", "sufijo de copia"),
    (re.compile(r"\s*\b(official|oficial)\s+(music\s+)?(video|audio)\b\s*", re.I), " ",
     "coletilla 'official video'"),
    (re.compile(r"\s*\bfree\s*download\b\s*", re.I), " ", "'free download'"),
    (re.compile(r"\s{2,}"), " ", None),      # espacios dobles que quedan al limpiar
]

# Número de catálogo al principio: "001 - Artista - Tema", "[ONYX002] Artista - Tema".
CATALOGO_ADELANTE = re.compile(r"^\s*[\[\(]?\s*[A-Z]{0,6}\s*\d{2,4}\s*[\]\)]?\s*[-–—]\s+", re.I)

DIRECTA, INVERTIDA, SIN_PARTIR = "artista-titulo", "titulo-artista", "sin-partir"


@dataclass
class Propuesta:
    archivo: str
    ruta: str
    nombre_base: str          # el stem, que es la fuente
    artista_actual: str       # lo que hay hoy en el tag (casi siempre vacío)
    titulo_actual: str
    artista_propuesto: str
    titulo_propuesto: str
    orientacion: str          # artista-titulo | titulo-artista | sin-partir
    veces_izq: int            # cuántas veces se repite ese nombre a la izquierda
    veces_der: int            # ídem a la derecha — si es mayor, la forma está invertida
    limpieza: str             # qué basura se sacó del texto
    motivo: str
    revisar: str              # si | no — casos donde la evidencia es floja


COLUMNAS = [f.name for f in fields(Propuesta)]


def limpiar(texto: str) -> tuple[str, str]:
    """Saca la basura de origen del texto. Devuelve (limpio, motivos)."""
    motivos = []
    out = texto
    for patron, reemplazo, motivo in LIMPIEZA:
        if patron.search(out):
            out = patron.sub(reemplazo, out)
            if motivo and motivo not in motivos:
                motivos.append(motivo)
    return out.strip(" -–—_"), "; ".join(motivos)


# Palabras que califican una VERSIÓN, no al artista. Si el lado del artista las trae,
# pertenecen al título: 'Fran Perrotta (Original mix)' es el artista 'Fran Perrotta' y el
# título lleva el '(Original mix)'.
_VERSION = re.compile(r"\b(original\s+mix|extended\s+mix|edit|preview|remix|bootleg|"
                      r"master(v\d+)?|vip|dub|radio\s+edit)\b", re.I)


def separar_version(lado: str) -> tuple[str, str]:
    """Separa un lado en (nombre, calificadores de versión encontrados)."""
    versiones = []
    resto = lado
    for m in re.finditer(r"\(([^)]*)\)", lado):               # "(Original mix)"
        if _VERSION.search(m.group(1)):
            versiones.append(m.group(1).strip())
            resto = resto.replace(m.group(0), " ")
    for m in _VERSION.finditer(resto):                        # "… edit" suelto
        versiones.append(m.group(0).strip())
        resto = resto.replace(m.group(0), " ")
    return re.sub(r"\s+", " ", resto).strip(" -–—_"), " ".join(versiones).strip()


def _clave(nombre: str) -> str:
    """Normaliza un lado para contarlo: sin paréntesis, sin versión, minúsculas.

    'Fran Perrotta (Original mix)' y 'fran perrotta edit' cuentan como el mismo nombre.
    """
    s = re.sub(r"\([^)]*\)", " ", nombre)
    s = _VERSION.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _tokens(clave: str) -> set[str]:
    """Palabras distintivas de un nombre (≥4 letras), para emparentar variantes."""
    return {t for t in clave.split() if len(t) >= 4}


def partir(nombre_base: str) -> tuple[str, str] | None:
    """Parte por el PRIMER separador. None si no hay.

    Se saca antes un número de catálogo al principio: en 'ONYX002 - Artista - Tema' el
    primer separador no divide artista de título.
    """
    limpio = CATALOGO_ADELANTE.sub("", nombre_base)
    partes = SEPARADOR.split(limpio, maxsplit=1)
    if len(partes) != 2:
        return None
    izq, der = partes[0].strip(), partes[1].strip()
    if len(izq) < 2 or len(der) < 2:
        return None
    return izq, der


def analizar(rutas: list[str]) -> list[Propuesta]:
    """Dos pasadas: primero se cuentan los nombres de cada lado, después se decide."""
    bases = {r: Path(r).stem for r in rutas}
    partidos = {r: partir(b) for r, b in bases.items()}

    # Pasada 1 — frecuencia de cada nombre en cada lado. Un título no se repite; un
    # artista sí. Esa asimetría es la única evidencia que hay para orientar el split.
    cuenta_izq = Counter(_clave(p[0]) for p in partidos.values() if p)
    cuenta_der = Counter(_clave(p[1]) for p in partidos.values() if p)

    # Tokens de los nombres que SE REPITEN a la derecha: sirven para emparentar variantes
    # ("Franco Perrotta" con "Fran Perrotta") sin hardcodear ningun artista.
    tokens_frecuentes = set()
    for clave, n in cuenta_der.items():
        if n >= 2:
            tokens_frecuentes |= _tokens(clave)

    propuestas = []
    for ruta in rutas:
        base = bases[ruta]
        actual = leer_tags(ruta)
        par = partidos[ruta]

        if par is None:
            propuestas.append(Propuesta(
                archivo=Path(ruta).name, ruta=ruta, nombre_base=base,
                artista_actual=actual["artista"], titulo_actual=actual["titulo"],
                artista_propuesto="", titulo_propuesto="",
                orientacion=SIN_PARTIR, veces_izq=0, veces_der=0, limpieza="",
                motivo="sin separador ' - ': no hay de dónde sacar el artista",
                revisar="no"))
            continue

        izq, der = par
        n_izq, n_der = cuenta_izq[_clave(izq)], cuenta_der[_clave(der)]
        revisar = "no"

        if n_der > n_izq and n_der >= 2:
            artista_bruto, titulo_bruto, orient = der, izq, INVERTIDA
            motivo = (f"'{_clave(der)}' aparece {n_der} veces a la DERECHA: eso es un "
                      f"artista, no un título — el nombre está invertido")
        else:
            artista_bruto, titulo_bruto, orient = izq, der, DIRECTA
            motivo = "primer separador: izquierda = artista"
            # Variante de un artista frecuente que la cuenta exacta no agarra: 'Franco
            # Perrotta' contra 'Fran Perrotta'. No se invierte solo — se marca.
            comunes = _tokens(_clave(der)) & tokens_frecuentes
            if comunes:
                revisar = "si"
                motivo = (f"posible INVERTIDO sin aplicar: '{_clave(der)}' comparte "
                          f"{sorted(comunes)} con un artista que se repite a la derecha")

        # El calificador de versión es del título, no del artista.
        artista_bruto, version = separar_version(artista_bruto)
        if version:
            titulo_bruto = f"{titulo_bruto} ({version})"

        # Varios separadores: no se sabe si el resto es título o sello.
        if len(SEPARADOR.findall(CATALOGO_ADELANTE.sub("", base))) > 1:
            revisar = "si"
            motivo += " · hay más de un separador: el resto puede ser título o sello"

        artista, limp_a = limpiar(artista_bruto)
        titulo, limp_t = limpiar(titulo_bruto)
        limpieza = "; ".join(x for x in (limp_a, limp_t) if x)

        propuestas.append(Propuesta(
            archivo=Path(ruta).name, ruta=ruta, nombre_base=base,
            artista_actual=actual["artista"], titulo_actual=actual["titulo"],
            artista_propuesto=artista, titulo_propuesto=titulo,
            orientacion=orient, veces_izq=n_izq, veces_der=n_der,
            limpieza=limpieza, motivo=motivo, revisar=revisar))
    return propuestas


def escribir_csv(props: list[Propuesta], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"parseo_nombres_{sello}.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        for p in props:
            w.writerow(asdict(p))
    return destino


def informe(props: list[Propuesta]) -> None:
    partibles = [p for p in props if p.orientacion != SIN_PARTIR]
    invertidos = [p for p in partibles if p.orientacion == INVERTIDA]
    dudosos = [p for p in partibles if p.revisar == "si"]
    sin_partir = [p for p in props if p.orientacion == SIN_PARTIR]

    print(f"\n{'=' * 100}\nPARSEO PROPUESTO — {len(props)} archivos "
          f"({len(partibles)} se parten · {len(sin_partir)} no)\n{'=' * 100}")

    print(f"\nSE PARTEN ({len(partibles)}) — {len(invertidos)} con la forma INVERTIDA\n")
    print(f"  {'nombre original':52} {'artista':22} {'título':26} {'orient':6}")
    print(f"  {'-' * 52} {'-' * 22} {'-' * 26} {'-' * 6}")
    for p in sorted(partibles, key=lambda p: (p.orientacion != INVERTIDA, p.archivo.lower())):
        marca = "INV" if p.orientacion == INVERTIDA else ("?" if p.revisar == "si" else "")
        print(f"  {p.nombre_base[:52]:52} {p.artista_propuesto[:22]:22} "
              f"{p.titulo_propuesto[:26]:26} {marca:6}")

    if invertidos:
        print(f"\n  Los {len(invertidos)} invertidos, con la evidencia que los delata:")
        for p in invertidos:
            print(f"    {p.nombre_base[:44]:44} izq×{p.veces_izq} der×{p.veces_der}  "
                  f"→ artista '{p.artista_propuesto}'")

    if dudosos:
        print(f"\n  Los {len(dudosos)} marcados '?' — el split se hizo, pero hay un motivo "
              f"concreto para mirarlos:")
        for p in dudosos:
            print(f"    {p.nombre_base[:50]:50} → '{p.artista_propuesto}' | "
                  f"'{p.titulo_propuesto[:24]}'")
            print(f"        {p.motivo}")

    limpiados = [p for p in props if p.limpieza]
    if limpiados:
        print(f"\n  Limpieza de basura aplicada al TEXTO del tag ({len(limpiados)}):")
        for p in limpiados:
            print(f"    {p.nombre_base[:46]:46} [{p.limpieza}]")

    print(f"\nNO SE PARTEN ({len(sin_partir)}) — artista y título quedan VACÍOS, no 'Unknown'\n")
    for p in sin_partir:
        print(f"  {p.nombre_base[:60]}")
    print(f"{'=' * 100}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="calidad.normalizar_tags",
        description="Parseo de nombres a artista/título. Todavía NO escribe tags.")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("calidad/out"))
    args = ap.parse_args(argv)

    rutas = sorted(str(p) for p in args.audio.rglob("*") if p.suffix.lower() in EXTS)
    if not rutas:
        print(f"No encontré audio en {args.audio}")
        return 1
    props = analizar(rutas)
    informe(props)
    print(f"Detalle → {escribir_csv(props, args.out)}")
    print("\nNo se escribió ningún tag ni se tocó ningún archivo: esto es solo el parseo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
