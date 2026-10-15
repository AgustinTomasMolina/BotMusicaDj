"""La radio desde una playlist (f55): qué temas de la playlist entran al set y por qué no los otros.

Portado de la rama vieja `f33-radio-desde-playlist` (radio_playlist.py, 57702b8) a la Radio de
hoy (f50) y a las playlists de hoy (f53: importadas, con el estado del análisis del motor). Lo
que cambió respecto de f33 está marcado con "f55".

Decisiones del dueño, que son el contrato de este módulo:

1. La radio de la web arma SOLO desde una playlist de «Mis playlists» (`mi_playlists` de db.py).
   El modo «toda la biblioteca del motor» ya no existe en la web (la CLI sigue igual).
2. Solo entra el MISMO GÉNERO que la semilla. El género es el que tiene guardado el item de la
   playlist, comparado sin mayúsculas, sin espacios de sobra y en NFKC (`clave_genero`): "Hard
   Techno" y "  hard   techno" son el mismo; "Hard Techno" y "Techno" NO (nada de géneros
   "parecidos": eso sería inventar). Sin género no entra, y se dice.
3. BPM, key y energía son los del MOTOR, nunca los del item (esos vienen de Rekordbox o de la
   búsqueda y no son lo que mide la radio). Este módulo no los mira: solo filtra el pool que
   después recibe `motor.radio.build_set`, que decide todo lo demás (±8 % de BPM, curva, largo).
4. Sin archivo en la PC no hay nada que analizar ni que tocar: no entra, y se dice.
5. Lo que el motor no analizó (o está analizando ahora, en segundo plano) no entra todavía: se
   cuenta aparte para que la pantalla avise «N todavía analizando».
6. Lo que no es un track (menos de 90 s, `motor.modelos.es_track`) no entra: f55 lo saca acá en
   vez de dejar que el motor lo ignore, así cada tema afuera tiene su motivo en la lista.
7. Duplicados — el mismo archivo, o el mismo artista + título — DENTRO del género del set: entra
   el PRIMERO en el orden de la playlist (de los que pueden entrar); los otros dicen de cuál son
   duplicado. f55: el duplicado se decide entre los que podrían entrar, así una copia que todavía
   no se analizó no le quita el lugar a otra que ya está lista (es el mismo tema).

Las cuentas son una PARTICIÓN de la playlist: `entran + sum(excluidos) == total`, para que la
pantalla pueda decir «entran 38 de 44 (4 de otro género, 1 sin género, 1 sin archivo)» sin que
los números dejen de cerrar.

Lógica pura: no importa `server`, `db` ni `motor` (la imagen Docker puede no traer motor/). El
server arma los `Tema` con lo que ya sabe (db + `_analisis_items` + la base del motor).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# --- motivos por los que un tema queda afuera, en el orden en que se deciden ---------------------
OTRO_GENERO = "otro_genero"      # tiene género y no es el de la semilla
SIN_ARCHIVO = "sin_archivo"      # no hay archivo en la PC (por bajar, no encontrado, se movió)
SIN_GENERO = "sin_genero"        # vacío (o "Sin género", que db.sanear_genero ya guarda vacío)
ANALIZANDO = "analizando"        # en la cola o analizándose ahora (segundo plano)
SIN_ANALIZAR = "sin_analizar"    # con archivo, el motor todavía no lo midió (o cambió)
FALLO = "fallo_analisis"         # se intentó analizar y no se pudo
NO_ES_TRACK = "no_es_track"      # dura menos que un track (loop, sample)
DUPLICADO = "duplicado"          # el mismo tema ya entró antes en la playlist
MOTIVOS = (OTRO_GENERO, SIN_GENERO, SIN_ARCHIVO, ANALIZANDO, SIN_ANALIZAR, FALLO, NO_ES_TRACK,
           DUPLICADO)

# Estados del análisis como los dice `server._analisis_items` (f53).
ANALIZADO = "analizado"
EN_CURSO = ("analizando", "en-cola")

# Lo mínimo para que un set sea un set (la semilla y uno más): con menos, «Armar un set» queda
# deshabilitado en Mis playlists y lo dice.
MINIMO_SET = 2


# Caracteres que no se ven y no son espacio: guion suave, ancho cero, marcas de dirección, BOM.
# Un copiar-pegar los trae sin que nadie se entere ("Tech​no" se ve "Techno" y no lo es).
_INVISIBLES = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")
# Controles C0, DEL y C1: no son texto.
_CONTROLES = re.compile("[\x00-\x1f\x7f-\x9f]")


def limpiar_texto(texto) -> str:
    """Un texto corto (género, nombre) como se guarda y se muestra: sin invisibles, los
    controles como espacio y los espacios colapsados. Lo usan `db.sanear_genero`, el nombre de
    la playlist que guarda la radio y la comparación de géneros de acá: un solo saneo."""
    if texto is None:
        return ""
    t = _CONTROLES.sub(" ", _INVISIBLES.sub("", str(texto)))
    return " ".join(t.split())


def normalizar(texto) -> str:
    """Texto comparable: sin invisibles ni controles, NFKC, casefold, sin espacios en las puntas
    y los de adentro colapsados. "  Hard   Bounce" y "hard bounce" son el mismo; "ＴＥＣＨＮＯ"
    (ancho completo, típico de un copiar-pegar), "Tech​no" y "techno" también."""
    if texto is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", limpiar_texto(texto)).casefold().split())


# Rutas absolutas dentro de un texto de error (Windows con unidad o UNC, o POSIX). Las carpetas
# del medio pueden tener espacios ("Bot MUSICA"); el último tramo se corta en un espacio, una
# comilla o un separador de mensaje.
_RUTA = re.compile(r"(?:[A-Za-z]:|\\\\[^\\/\s'\"]+)(?:[\\/]+[^\\/'\"<>|:;,()\r\n]+(?=[\\/]))*"
                   r"[\\/]+[^\\/\s'\"<>|:;,()]*"
                   r"|(?<![\w.~])/(?:[^/'\"<>|:;,()\r\n]+/)+[^/\s'\"<>|:;,()]*")


def ocultar_rutas(texto, conocidas=()) -> str | None:
    """El texto de un error SIN rutas de la PC (nunca viajan al navegador): primero las rutas
    que se conocen (`conocidas`: el archivo del tema, la base del motor; también como las
    escribe `repr`, con la barra doble), y después cualquier otra ruta absoluta. Cada ruta
    queda como su último tramo (el nombre del archivo o de la carpeta)."""
    if texto is None:
        return None
    t = str(texto)
    for r in sorted({str(c) for c in conocidas if c}, key=len, reverse=True):
        nombre = r.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        for forma in (r.replace("\\", "\\\\"), r, r.replace("\\", "/")):
            t = t.replace(forma, nombre)
    return _RUTA.sub(lambda m: m.group(0).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1], t)


def clave_genero(genero) -> str | None:
    """La clave con la que se comparan géneros, o `None` = sin género. "Sin género" (como rotula
    la home al que no tiene) cuenta como sin género aunque llegue escrito."""
    n = normalizar(genero)
    return None if not n or n in ("sin género", "sin genero") else n


@dataclass(frozen=True)
class Tema:
    """Un item de la playlist con lo que el server sabe de él. Nada de rutas: `clave_archivo`
    es solo una identidad para comparar (la arma el server y no viaja al navegador)."""
    item_id: int
    posicion: int                 # 1-based, en el orden de la playlist
    titulo: str
    artista: str
    genero: str | None            # como lo guardó el DJ (None = sin género)
    archivo_ok: bool              # el archivo está en disco
    archivo_motivo: str | None    # por qué no hay archivo (texto del server), si no hay
    analisis: str                 # 'analizado' | 'pendiente' | 'fallo' | 'analizando' | 'en-cola' | 'sin-archivo'
    analisis_motivo: str | None   # el motivo del fallo, si falló
    clave_archivo: str | None     # identidad del archivo (normcase de la ruta absoluta)
    radio_id: str | None          # id de la radio (solo si el motor lo tiene vigente)
    es_track: bool | None         # None = el motor no lo tiene cargado (no se sabe la duración)

    @property
    def etiqueta(self) -> str:
        t = " ".join((self.titulo or "").split()) or "(sin título)"
        a = " ".join((self.artista or "").split())
        return f"{a} — {t}" if a else t


@dataclass(frozen=True)
class Decision:
    """Qué pasa con un tema en un set de cierto género."""
    tema: Tema
    entra: bool
    motivo: str | None            # código de MOTIVOS (None = entra)
    texto: str | None             # el motivo en palabras, para la pantalla
    duplicado_de: int | None = None   # item_id del que entró


def _texto(motivo: str, t: Tema, genero_set: str | None, primero: Tema | None = None) -> str:
    if motivo == OTRO_GENERO:
        return (f"es de otro género ({t.genero}); el set es de "
                f"{genero_set or 'otro género'}")
    if motivo == SIN_ARCHIVO:
        return t.archivo_motivo or "no tiene archivo en esta PC: no hay nada que analizar"
    if motivo == SIN_GENERO:
        return ("no tiene género: el set junta solo temas del mismo género que la semilla. "
                "Ponéselo en Mis playlists")
    if motivo == ANALIZANDO:
        return "el motor lo está analizando: entra cuando termine"
    if motivo == FALLO:
        return f"el análisis falló: {t.analisis_motivo}" if t.analisis_motivo else "el análisis falló"
    if motivo == SIN_ANALIZAR:
        return "el motor todavía no lo analizó (Analizar, en Mis playlists)"
    if motivo == NO_ES_TRACK:
        return "dura menos de 90 s: no es un track (loop, sample)"
    if motivo == DUPLICADO and primero is not None:
        return (f"es el mismo tema que «{primero.etiqueta}» (#{primero.posicion} de la playlist): "
                f"entra solo el primero")
    return motivo


def _base(t: Tema) -> str | None:
    """Lo que deja afuera a un tema SIN mirar el género del set (None = podría entrar)."""
    if not t.archivo_ok:
        return SIN_ARCHIVO
    if clave_genero(t.genero) is None:
        return SIN_GENERO
    if t.analisis in EN_CURSO:
        return ANALIZANDO
    if t.analisis == "fallo":
        return FALLO
    if t.analisis != ANALIZADO or not t.radio_id or t.es_track is None:
        return SIN_ANALIZAR
    if t.es_track is False:
        return NO_ES_TRACK
    return None


def _identidades(t: Tema) -> list[tuple]:
    ids = []
    if t.clave_archivo:
        ids.append(("archivo", t.clave_archivo))
    titulo = normalizar(t.titulo)
    if titulo:     # sin título no hay tema que comparar: "" == "" no es el mismo tema
        ids.append(("tema", normalizar(t.artista), titulo))
    return ids


def clasificar(temas: Sequence[Tema], clave: str) -> list[Decision]:
    """Qué pasa con cada tema de la playlist (en su orden) en un set del género `clave`.

    El orden de las preguntas: primero el género (para ESTE set, lo que deja afuera a un tema de
    otro género es eso, aunque además le falte el archivo); después lo que se arregla en la
    playlist (archivo, género, análisis) y recién entre los que podrían entrar, los duplicados.
    """
    genero_set = next((t.genero for t in temas if clave_genero(t.genero) == clave), None)
    vistos: dict[tuple, Tema] = {}
    out = []
    for t in temas:
        cg = clave_genero(t.genero)
        motivo = OTRO_GENERO if cg is not None and cg != clave else _base(t)
        if motivo is not None:
            out.append(Decision(t, False, motivo, _texto(motivo, t, genero_set)))
            continue
        ids = _identidades(t)
        primero = next((vistos[k] for k in ids if k in vistos), None)
        if primero is not None:
            out.append(Decision(t, False, DUPLICADO, _texto(DUPLICADO, t, genero_set, primero),
                                primero.item_id))
            continue
        for k in ids:
            vistos[k] = t
        out.append(Decision(t, True, None, None))
    return out


def resumen(decisiones: Sequence[Decision]) -> dict:
    """`{entran, total, excluidos: {motivo: n}}` con TODOS los motivos (también en cero)."""
    excluidos = dict.fromkeys(MOTIVOS, 0)
    entran = 0
    for d in decisiones:
        if d.entra:
            entran += 1
        else:
            excluidos[d.motivo] += 1
    return {"entran": entran, "total": len(decisiones), "excluidos": excluidos}


def afuera(decisiones: Sequence[Decision]) -> list[dict]:
    """Los temas que quedan afuera, en el orden de la playlist, con su motivo (código y texto)."""
    return [{"item_id": d.tema.item_id, "posicion": d.tema.posicion, "titulo": d.tema.titulo,
             "artista": d.tema.artista, "genero": d.tema.genero, "motivo": d.motivo,
             "texto": d.texto, "duplicado_de": d.duplicado_de}
            for d in decisiones if not d.entra]


def generos(temas: Sequence[Tema]) -> list[dict]:
    """Por cada género que tiene algún tema que PODRÍA entrar (archivo, análisis y duración en
    regla): cuántos entran en un set de ese género y qué queda afuera. En el orden en que aparece
    el género en la playlist; el nombre es el del primer tema de ese género."""
    vistos: dict[str, str] = {}
    for t in temas:
        cg = clave_genero(t.genero)
        if cg is not None and cg not in vistos and _base(t) is None:
            vistos[cg] = " ".join(t.genero.split())
    out = []
    for clave, nombre in vistos.items():
        decisiones = clasificar(temas, clave)
        out.append({"genero": nombre, "clave": clave, **resumen(decisiones),
                    "afuera": afuera(decisiones),
                    "entran_ids": [d.tema.item_id for d in decisiones if d.entra]})
    return out


def estados(temas: Sequence[Tema]) -> list[dict]:
    """Cada tema con lo que lo deja afuera SIN mirar el género del set (`motivo` None = podría
    entrar en un set de su género). Para la pantalla cuando todavía no hay semilla elegida."""
    out = []
    for t in temas:
        m = _base(t)
        out.append({"item_id": t.item_id, "posicion": t.posicion, "titulo": t.titulo,
                    "artista": t.artista, "genero": t.genero, "motivo": m,
                    "texto": _texto(m, t, None) if m else None})
    return out


def semillas(temas: Sequence[Tema]) -> list[Tema]:
    """Los temas que pueden arrancar un set: los que entran en un set de SU género (un duplicado
    no: su lugar lo tiene el primero). En el orden de la playlist."""
    entran = set()
    for g in generos(temas):
        entran.update(g["entran_ids"])
    return [t for t in temas if t.item_id in entran]


def en_curso(temas: Iterable[Tema], clave: str | None = None) -> int:
    """Cuántos temas (del género `clave`, o de toda la playlist) se están analizando ahora."""
    return sum(1 for t in temas if t.analisis in EN_CURSO and t.archivo_ok
               and (clave is None or clave_genero(t.genero) == clave))


def puede_armar(temas: Sequence[Tema]) -> tuple[bool, str | None]:
    """¿Se puede armar un set con esta playlist? `(True, None)` si algún género tiene al menos
    `MINIMO_SET` temas que entran; si no, `(False, motivo)` en palabras."""
    gs = generos(temas)
    mejor = max((g["entran"] for g in gs), default=0)
    if mejor >= MINIMO_SET:
        return True, None
    if not temas:
        return False, "La playlist está vacía."
    analizando = en_curso(temas)
    base = [_base(t) for t in temas]
    sin_genero = sum(1 for b in base if b == SIN_GENERO)
    sin_analizar = sum(1 for b in base if b in (SIN_ANALIZAR, FALLO))
    partes = [f"Hace falta al menos {MINIMO_SET} temas analizados del mismo género"
              + (f" (el que más tiene, {mejor})." if gs else "; no hay ninguno.")]
    if analizando:
        partes.append(f"{analizando} todavía analizando.")
    if sin_analizar:
        partes.append(f"{sin_analizar} sin analizar.")
    if sin_genero:
        partes.append(f"{sin_genero} sin género.")
    return False, " ".join(partes)
