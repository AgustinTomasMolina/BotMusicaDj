"""Tarea 5.3 (primera mitad) — AUDITORÍA de tags ID3. Solo lee, no escribe nada.

Antes de normalizar hay que saber qué tan rotos están los tags. Este módulo mide y no
toca ningún archivo: ni el original ni una copia.

    python -m calidad.tags --audio <carpeta>

QUÉ TAG ES NUESTRO Y QUÉ TAG ES DE OTRO
---------------------------------------
Distinguirlos es la parte que más importa: un tag externo sirve como referencia
independiente y uno propio es circular.

El formato NO alcanza para decidirlo. `tagger.py:37` escribe la key en Camelot, pero
Mixed In Key TAMBIÉN escribe en Camelot — así que "es Camelot, luego lo escribimos
nosotros" es falso. Por eso la procedencia se decide por las FIRMAS que cada herramienta
deja en las claves crudas del archivo:

  - NOSOTROS: comentario que empieza con "MusiFlix". `server.py:704` arma
    `f"MusiFlix · calidad {grade}"` y se lo pasa a `tagger.taggear()`, con lang="spa".
  - MIXED IN KEY: `TXXX:EnergyLevel`, `GEOB:Key`/`GEOB:Energy` (JSON), y un comentario
    con la forma exacta "1A - Energy 7".
  - SERATO: `GEOB:Serato *`, `TXXX:SERATO_PLAYCOUNT`.
  - EXTERNO: key en notación clásica ('Am', 'F#m'), que nuestro tagger nunca escribe.

Esto CORRIGE la auditoría del 08/09, que descartó BabaBass3000 por circular: su TKEY '1A'
viene de Mixed In Key (tiene TXXX:EnergyLevel y el comentario "1A - Energy 7"), no de
MusiFlix. Es de otra herramienta, no nuestro.
"""
import argparse
import csv
import datetime
import re
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

EXTS = (".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg", ".opus")

# Marcador de tag propio (ver docstring). server.py:704.
FIRMA_PROPIA = "musiflix"

CAMELOT = re.compile(r"^\s*\d{1,2}\s*[AB]\s*$", re.I)
CLASICA = re.compile(r"^\s*[A-G][#b]?\s*(m|min|maj|major|minor)?\s*$", re.I)

# Basura de origen: nombres de sitios de descarga y coletillas de YouTube.
PATRONES_BASURA = [
    (re.compile(r"myfreemp3", re.I), "sitio de descarga (myfreemp3)"),
    (re.compile(r"\bmp3teca|mp3xd|descargar\b", re.I), "sitio de descarga"),
    (re.compile(r"\b(official|oficial)\s+(music\s+)?(video|audio)\b", re.I), "coletilla 'official video'"),
    (re.compile(r"\bfree\s*download\b", re.I), "'free download'"),
    (re.compile(r"\bno\s*copyright\b", re.I), "'no copyright'"),
    (re.compile(r"\b(lyrics?|letra)\b", re.I), "'lyrics'"),
    (re.compile(r"https?://|www\.|\.(com|net|vip|org)\b", re.I), "URL o dominio"),
    (re.compile(r"\[\s*(hd|hq|4k|320\s*kbps?)\s*\]", re.I), "etiqueta de calidad en el texto"),
    (re.compile(r"\bextended\s+mix\b.*\bextended\s+mix\b", re.I), "texto duplicado"),
]

# "Artista - Titulo" metido en el campo título.
SEPARADOR_ARTISTA = re.compile(r"^\s*(.{2,60}?)\s+[-–—]\s+(.{2,})$")

PROPIO, EXTERNO, NINGUNO, INDETERMINADO = "propio", "externo", "ninguno", "indeterminado"
MIXED_IN_KEY, SERATO = "mixed-in-key", "serato"

# Firmas de herramientas de terceros, detectadas sobre las claves CRUDAS del archivo.
# Importa distinguirlas: un Camelot escrito por Mixed In Key NO es nuestro, es de otra
# herramienta, y por lo tanto no es circular aunque use el mismo formato que tagger.py:37.
_FIRMA_SERATO = re.compile(r"^(GEOB:Serato|TXXX:SERATO_)", re.I)
_FIRMA_MIK_CLAVE = re.compile(r"^(TXXX:EnergyLevel|GEOB:(Key|Energy|BeatGrid)$)", re.I)
_FIRMA_MIK_COMENTARIO = re.compile(r"^\s*\d{1,2}[AB]\s*-\s*Energy\s*\d+\s*$", re.I)

# Basura de origen en el NOMBRE DE ARCHIVO. Con 93% de los archivos sin título, el nombre
# es la única fuente que va a tener la normalización, así que su suciedad importa tanto
# como la de los tags.
PATRONES_NOMBRE = PATRONES_BASURA + [
    (re.compile(r"\(\s*cut\s*\)", re.I), "'(cut)'"),
    (re.compile(r"\s\(\d+\)\s*$", re.I), "sufijo de copia '(1)'"),
    (re.compile(r"\b\d{2,3}k?\d{0,2}b?\b.*\b(master|mix)\b", re.I), "specs técnicas en el nombre"),
]


@dataclass
class Diagnostico:
    archivo: str
    ruta: str
    formato: str
    # Lo que hay hoy
    artista: str
    titulo: str
    genero: str
    bpm: str
    key: str
    comentario: str
    n_tags: int
    taggers: str               # firmas encontradas: propio, serato, mixed-in-key
    # Qué está roto
    sin_artista: str           # si | no
    sin_titulo: str
    sin_genero: str
    artista_en_titulo: str     # si | no — "Artista - Titulo" en el título o en el nombre
    fuente_sugerencia: str     # de dónde saldría la propuesta: tag | nombre de archivo
    artista_sugerido: str
    titulo_sugerido: str
    basura: str                # basura encontrada en los TAGS (vacío = limpio)
    campos_con_basura: str
    basura_nombre: str         # basura en el NOMBRE del archivo
    # Procedencia
    origen_key: str            # propio | mixed-in-key | serato | externo | ninguno | indeterminado
    origen_bpm: str
    origen_comentario: str


COLUMNAS = [f.name for f in fields(Diagnostico)]


def _texto(valor) -> str:
    """Normaliza a str lo que devuelve mutagen, que según el formato es lista, frame u objeto."""
    if valor is None:
        return ""
    if isinstance(valor, list):
        return str(valor[0]).strip() if valor else ""
    if isinstance(valor, bytes):
        return valor.decode("utf-8", "ignore").strip()
    return str(valor).strip()


# Cada campo lógico y las claves con las que aparece en ID3, Vorbis (flac/ogg) y MP4.
_CLAVES = {
    "artista": ("TPE1", "ARTIST", "©ART", "\xa9ART"),
    "titulo": ("TIT2", "TITLE", "©nam", "\xa9nam"),
    "genero": ("TCON", "GENRE", "©gen", "\xa9gen"),
    "bpm": ("TBPM", "BPM", "TMPO", "TEMPO"),
    "key": ("TKEY", "INITIALKEY", "KEY", "INITIAL KEY"),
    "comentario": ("COMM", "COMMENT", "©cmt", "\xa9cmt", "DESCRIPTION"),
}


def leer_tags(ruta: str) -> dict:
    """Lee los tags de cualquier formato y los mapea a campos comunes. No escribe."""
    from mutagen import File as MFile  # perezoso

    vacio = {k: "" for k in _CLAVES}
    vacio.update(formato="?", n_tags=0, claves=[])
    try:
        m = MFile(ruta)
    except Exception:  # noqa: BLE001
        return vacio
    if m is None:
        return {**vacio, "formato": Path(ruta).suffix.lstrip(".").lower()}

    datos = {**vacio, "formato": type(m).__name__.lower()}
    tags = getattr(m, "tags", None)
    if not tags:
        return datos

    claves = [str(k) for k in tags.keys()]
    datos["n_tags"] = len(claves)
    datos["claves"] = claves
    for campo, posibles in _CLAVES.items():
        for k in claves:
            # ID3 usa 'COMM::spa' y MP4 '----:com.apple.iTunes:initialkey'
            base = str(k).upper().split(":")[0]
            corto = str(k).upper().split(":")[-1]
            if base in posibles or corto in posibles or str(k) in posibles:
                try:
                    datos[campo] = _texto(tags[k])
                except Exception:  # noqa: BLE001
                    pass
                if datos[campo]:
                    break
    return datos


def detectar_taggers(claves: list[str], comentario: str) -> list[str]:
    """Qué herramientas dejaron su firma en el archivo, mirando las claves crudas."""
    encontrados = []
    if comentario.lower().startswith(FIRMA_PROPIA):
        encontrados.append(PROPIO)
    if any(_FIRMA_SERATO.match(str(k)) for k in claves):
        encontrados.append(SERATO)
    if (any(_FIRMA_MIK_CLAVE.match(str(k)) for k in claves)
            or _FIRMA_MIK_COMENTARIO.match(comentario or "")):
        encontrados.append(MIXED_IN_KEY)
    return encontrados


def origen_de_key(key: str, comentario: str, taggers: list[str] | None = None) -> str:
    """¿El TKEY lo escribimos nosotros, lo puso otra herramienta, o vino del origen?

    OJO con el Camelot: `tagger.py:37` lo escribe, pero Mixed In Key TAMBIÉN. El formato
    solo no alcanza para decir "es nuestro" — hay que mirar las firmas del archivo.
    """
    taggers = taggers or []
    if not key:
        return NINGUNO
    if PROPIO in taggers:
        return PROPIO
    if CAMELOT.match(key):
        # Camelot de otra herramienta: externo a nosotros, aunque comparta el formato.
        if MIXED_IN_KEY in taggers:
            return MIXED_IN_KEY
        if SERATO in taggers:
            return SERATO
        # Camelot sin firma de nadie: no se puede afirmar que sea nuestro ni que no.
        return INDETERMINADO
    if CLASICA.match(key):
        return EXTERNO         # 'Am', 'F#m' — notación que nuestro tagger nunca escribe
    return INDETERMINADO


def origen_simple(valor: str, comentario: str) -> str:
    """Procedencia de un campo que no tiene formato delator (BPM, comentario)."""
    if not valor:
        return NINGUNO
    if comentario.lower().startswith(FIRMA_PROPIA):
        return PROPIO
    return INDETERMINADO


def detectar_basura(campos: dict) -> tuple[str, str]:
    """Devuelve (motivos, campos afectados) para la basura de origen encontrada."""
    motivos, afectados = [], []
    for campo in ("artista", "titulo", "genero", "comentario"):
        texto = campos.get(campo, "")
        if not texto:
            continue
        for patron, motivo in PATRONES_BASURA:
            if patron.search(texto):
                if motivo not in motivos:
                    motivos.append(motivo)
                if campo not in afectados:
                    afectados.append(campo)
    return "; ".join(motivos), ", ".join(afectados)


def partir_artista_titulo(titulo: str, artista: str) -> tuple[str, str]:
    """Si el título trae 'Artista - Titulo', devuelve las dos partes. Si no, ('', '').

    Solo se propone cuando NO hay artista, o cuando el artista actual coincide con la
    parte izquierda: partir a ciegas rompería títulos que legítimamente llevan guion.
    """
    m = SEPARADOR_ARTISTA.match(titulo or "")
    if not m:
        return "", ""
    izq, der = m.group(1).strip(), m.group(2).strip()
    if not artista or artista.strip().lower() == izq.lower():
        return izq, der
    return "", ""


def basura_en_nombre(nombre: str) -> str:
    """Basura de origen en el nombre del archivo, que es de donde va a salir el título.

    Se mira el nombre SIN extensión: la extensión no es basura, y además varios patrones
    anclan al final ('(1)' de copia) y nunca dispararían con el '.mp3' colgando.
    """
    stem = Path(nombre).stem
    motivos = []
    for patron, motivo in PATRONES_NOMBRE:
        if patron.search(stem) and motivo not in motivos:
            motivos.append(motivo)
    return "; ".join(motivos)


def diagnosticar(ruta: str) -> Diagnostico:
    t = leer_tags(ruta)
    comentario = t["comentario"]
    taggers = detectar_taggers(t["claves"], comentario)
    basura, campos_basura = detectar_basura(t)
    nombre = Path(ruta).name

    # Si no hay título en el tag, el nombre del archivo es la única fuente: se evalúa ahí.
    fuente_titulo = t["titulo"] or Path(ruta).stem
    art_sug, tit_sug = partir_artista_titulo(fuente_titulo, t["artista"])

    origen_com = NINGUNO
    if comentario:
        if PROPIO in taggers:
            origen_com = PROPIO
        elif MIXED_IN_KEY in taggers:
            origen_com = MIXED_IN_KEY
        else:
            origen_com = INDETERMINADO

    return Diagnostico(
        archivo=nombre, ruta=ruta, formato=t["formato"],
        artista=t["artista"], titulo=t["titulo"], genero=t["genero"],
        bpm=t["bpm"], key=t["key"], comentario=comentario, n_tags=t["n_tags"],
        taggers=", ".join(taggers),
        sin_artista="si" if not t["artista"] else "no",
        sin_titulo="si" if not t["titulo"] else "no",
        sin_genero="si" if not t["genero"] else "no",
        artista_en_titulo="si" if art_sug else "no",
        fuente_sugerencia=("tag" if t["titulo"] else "nombre de archivo"),
        artista_sugerido=art_sug, titulo_sugerido=tit_sug,
        basura=basura, campos_con_basura=campos_basura,
        basura_nombre=basura_en_nombre(nombre),
        origen_key=origen_de_key(t["key"], comentario, taggers),
        origen_bpm=(PROPIO if PROPIO in taggers and t["bpm"]
                    else origen_simple(t["bpm"], comentario)),
        origen_comentario=origen_com,
    )


def rutas_de(carpeta: Path) -> list[str]:
    return sorted(str(p) for p in carpeta.rglob("*") if p.suffix.lower() in EXTS)


def escribir_csv(diags: list[Diagnostico], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"tags_auditoria_{sello}.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        for d in diags:
            w.writerow(asdict(d))
    return destino


def _pct(n, tot):
    return f"{n:3}/{tot}  ({100 * n / tot:4.1f}%)" if tot else "0"


def informe(diags: list[Diagnostico]) -> None:
    n = len(diags)
    print(f"\n{'=' * 74}\nAuditoría de tags — {n} archivos (solo lectura)\n{'=' * 74}")

    print("\nCAMPOS FALTANTES")
    for etiqueta, campo in (("sin artista", "sin_artista"), ("sin título", "sin_titulo"),
                            ("sin género", "sin_genero")):
        print(f"  {etiqueta:26} {_pct(sum(1 for d in diags if getattr(d, campo) == 'si'), n)}")
    sin_nada = sum(1 for d in diags if d.n_tags == 0)
    print(f"  {'sin NINGÚN tag':26} {_pct(sin_nada, n)}")
    los_tres = sum(1 for d in diags
                   if d.sin_artista == "si" and d.sin_titulo == "si" and d.sin_genero == "si")
    print(f"  {'sin artista NI título NI gén':26} {_pct(los_tres, n)}")

    print("\nARTISTA METIDO EN EL TÍTULO")
    con = [d for d in diags if d.artista_en_titulo == "si"]
    print(f"  {'“Artista - Título”':26} {_pct(len(con), n)}")
    for d in con[:8]:
        print(f"      {d.archivo[:40]:40} → artista='{d.artista_sugerido}' "
              f"título='{d.titulo_sugerido[:28]}'")

    print("\nBASURA DE ORIGEN — EN LOS TAGS")
    sucios = [d for d in diags if d.basura]
    print(f"  {'con basura en tags':26} {_pct(len(sucios), n)}")
    motivos: dict[str, int] = {}
    for d in sucios:
        for m in d.basura.split("; "):
            motivos[m] = motivos.get(m, 0) + 1
    for m, c in sorted(motivos.items(), key=lambda x: -x[1]):
        print(f"      {c:3}  {m}")

    print("\nBASURA DE ORIGEN — EN EL NOMBRE DE ARCHIVO")
    print("  (importa igual o más: sin título en el tag, el nombre es la única fuente)")
    sucios_n = [d for d in diags if d.basura_nombre]
    print(f"  {'con basura en el nombre':26} {_pct(len(sucios_n), n)}")
    motivos_n: dict[str, int] = {}
    for d in sucios_n:
        for m in d.basura_nombre.split("; "):
            motivos_n[m] = motivos_n.get(m, 0) + 1
    for m, c in sorted(motivos_n.items(), key=lambda x: -x[1]):
        print(f"      {c:3}  {m}")

    print("\nQUÉ HERRAMIENTA TOCÓ CADA ARCHIVO")
    conteo: dict[str, int] = {}
    for d in diags:
        for t in (d.taggers.split(", ") if d.taggers else ["(ninguna)"]):
            conteo[t] = conteo.get(t, 0) + 1
    for t, c in sorted(conteo.items(), key=lambda x: -x[1]):
        print(f"  {t:26} {_pct(c, n)}")

    print("\nTAGS YA ESCRITOS — ¿POR QUIÉN?")
    estados = (NINGUNO, PROPIO, MIXED_IN_KEY, SERATO, EXTERNO, INDETERMINADO)
    print(f"  {'campo':12}" + "".join(f"{e:>14}" for e in estados))
    for etiqueta, campo in (("TKEY", "origen_key"), ("BPM", "origen_bpm"),
                            ("comentario", "origen_comentario")):
        c = {k: sum(1 for d in diags if getattr(d, campo) == k) for k in estados}
        print(f"  {etiqueta:12}" + "".join(f"{c[e]:>14}" for e in estados))

    externas = [d for d in diags if d.origen_key == EXTERNO]
    if externas:
        print(f"\n  Las {len(externas)} keys EXTERNAS (sirven de referencia independiente):")
        for d in externas:
            print(f"      {d.archivo[:44]:44} key='{d.key}'  bpm='{d.bpm}'")
    propias = [d for d in diags if d.origen_key == PROPIO]
    if propias:
        print(f"\n  Las {len(propias)} keys PROPIAS (circulares, NO sirven de referencia):")
        for d in propias:
            print(f"      {d.archivo[:44]:44} key='{d.key}'  comentario='{d.comentario[:24]}'")
    print(f"{'=' * 74}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="calidad.tags",
        description="Auditoría de tags ID3. Solo lee: no escribe ni mueve nada.")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("calidad/out"))
    args = ap.parse_args(argv)

    rutas = rutas_de(args.audio)
    if not rutas:
        print(f"No encontré audio en {args.audio}")
        return 1
    diags = [diagnosticar(r) for r in rutas]
    informe(diags)
    destino = escribir_csv(diags, args.out)
    print(f"Detalle por archivo → {destino}")
    print("\nNo se escribió ningún tag: esto es solo la medición previa a normalizar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
