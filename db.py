"""Persistencia local (SQLite + SQLAlchemy 2.0) del historial de MusiFlix:
búsquedas, playlists (modo lista) y descargas. Single-user por ahora, pero el
esquema ya tiene `user_id` (nullable) para no migrar cuando se agregue login.

Todas las escrituras se llaman con `asyncio.to_thread` desde server.py y están
envueltas en try/except: si la DB falla, la búsqueda/descarga NO se rompe.
"""
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        create_engine, desc, func, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

logger = logging.getLogger("bot_web")

BASE_DIR = Path(__file__).resolve().parent
# Rutas de datos configurables por entorno (para montar un volumen en Docker).
# Por defecto = comportamiento local de siempre (la raíz del proyecto).
DATA_DIR = Path(os.getenv("MUSIFLIX_DATA_DIR", str(BASE_DIR)))
DOWNLOADS_DIR = Path(os.getenv("MUSIFLIX_DOWNLOADS", str(BASE_DIR / "downloads")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "musiflix.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},  # se usa desde threads (to_thread)
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Busqueda(Base):
    __tablename__ = "busquedas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query: Mapped[str] = mapped_column(String(300))
    total: Mapped[int] = mapped_column(Integer, default=0)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class Playlist(Base):
    __tablename__ = "playlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nombre: Mapped[str] = mapped_column(String(300))
    origen: Mapped[str] = mapped_column(String(30), default="lista")
    total: Mapped[int] = mapped_column(Integer, default=0)
    encontradas: Mapped[int] = mapped_column(Integer, default=0)
    # Snapshot de {query, groups} tal cual, para re-renderizar con <ListResults>.
    data_json: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class Descarga(Base):
    # El nombre evitaba chocar con la tabla 'descargas' del database.py del bot de Discord
    # (ya borrado). Se conserva para no romper las DBs SQLite existentes.
    __tablename__ = "descargas_hist"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    titulo: Mapped[str] = mapped_column(String(400), default="")
    artista: Mapped[str] = mapped_column(String(400), default="")
    fuente: Mapped[str] = mapped_column(String(40), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    formato: Mapped[str] = mapped_column(String(10), default="")
    archivo: Mapped[str] = mapped_column(String(500), default="")
    ruta: Mapped[str] = mapped_column(Text, default="")
    grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    calidad_txt: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    camelot: Mapped[str | None] = mapped_column(String(8), nullable=True)
    genero: Mapped[str | None] = mapped_column(String(100), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class MiPlaylist(Base):
    """Playlist/crate creada por el usuario (distinta de la Playlist de búsqueda)."""
    __tablename__ = "mi_playlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nombre: Mapped[str] = mapped_column(String(300))
    activa: Mapped[bool] = mapped_column(Boolean, default=False)  # una sola activa (forzado en código)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=_ahora)
    # f53 — de dónde salió: 'musiflix' (armada en la página; NULL en bases viejas = esto),
    # 'rekordbox' (una playlist del XML) o 'carpeta' (una carpeta de la PC). Columnas agregadas
    # después: en bases viejas las crea `_migrar`.
    origen: Mapped[str | None] = mapped_column(String(20), nullable=True, default="musiflix")
    # Qué playlist/carpeta de ese origen es, SIN rutas absolutas de la PC: la ruta de carpetas
    # adentro de Rekordbox ("Techno / Peak") o la carpeta relativa a su raíz ("Musica/Techno").
    # Es la identidad para "ya importada": el nombre del XML no, porque el dueño exporta el XML
    # con nombres distintos y la misma playlist no tiene que entrar dos veces por eso.
    origen_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    origen_archivo: Mapped[str | None] = mapped_column(String(200), nullable=True)  # nombre del XML
    importada_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MiPlaylistItem(Base):
    __tablename__ = "mi_playlist_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("mi_playlists.id"), index=True)
    orden: Mapped[int] = mapped_column(Integer, default=0)
    # snapshot del track
    titulo: Mapped[str] = mapped_column(String(400), default="")
    artista: Mapped[str] = mapped_column(String(400), default="")
    fuente: Mapped[str] = mapped_column(String(40), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    duracion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Float y no Integer: el BPM va con su decimal (§6; antes `int()` guardaba 127.9 como 127).
    # Sin migración: create_all no toca una tabla existente y en una base vieja la columna
    # sigue siendo INTEGER, pero SQLite guarda igual 127.9 como REAL (afinidad: solo pasa a
    # entero lo que es entero sin perder nada). Las filas viejas ya truncadas no se recuperan.
    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    camelot: Mapped[str | None] = mapped_column(String(8), nullable=True)
    genero: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # campos de descarga: nullable → si ruta está vacía el item está "por bajar"
    archivo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ruta: Mapped[str | None] = mapped_column(Text, nullable=True)
    formato: Mapped[str | None] = mapped_column(String(10), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agregado_en: Mapped[datetime] = mapped_column(DateTime, default=_ahora)
    # El tema es un Go+ de SoundCloud (`solo_preview` de soundcloud_station): la fuente solo da
    # 30 s. Se guarda para que la playlist no ofrezca bajarlo como si fuera el tema (f41).
    # Columna agregada después: en bases viejas la crea `_migrar` (create_all no la agrega).
    solo_preview: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # f53 — temas importados (de Rekordbox o de una carpeta). El archivo ORIGINAL del dueño va en
    # `ruta` (el mismo campo que una descarga: así `armar_m3u8` y `_tiene_archivo` lo ven), pero
    # NO fue bajado por MusiFlix: la pantalla lo dice («En tu PC»), ver `_item_dict`.
    rb_track_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # TrackID del XML
    # Cómo se ubicó el archivo al importar: 'ok', 'ambiguo' (varios archivos DISTINTOS con ese
    # nombre: no se eligió uno a la suerte) o 'no-encontrado'. NULL = no se importó.
    resolucion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    homonimos: Mapped[int | None] = mapped_column(Integer, nullable=True)       # cuántos, si ambiguo
    # El dueño cambió el género a mano: al actualizar la importación, el del XML no lo pisa.
    genero_editado: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # Por qué un tema importado quedó a medias (tags ilegibles, por ejemplo). Sin rutas.
    import_motivo: Mapped[str | None] = mapped_column(String(200), nullable=True)


# Columnas agregadas a tablas que ya existían en bases de usuarios. `create_all` crea tablas
# nuevas pero NO agrega columnas a una tabla existente, así que cada una se agrega acá con un
# ALTER TABLE chico e idempotente (si ya está, no se toca). Las filas viejas quedan en NULL,
# que se lee como "no se sabe" (para solo_preview: False, lo que se suponía hasta ahora; para
# el origen de una playlist: 'musiflix', la única que existía).
_COLUMNAS_NUEVAS = (
    ("mi_playlist_items", "solo_preview", "BOOLEAN"),
    ("mi_playlists", "origen", "VARCHAR(20)"),
    ("mi_playlists", "origen_ref", "VARCHAR(300)"),
    ("mi_playlists", "origen_archivo", "VARCHAR(200)"),
    ("mi_playlists", "importada_en", "DATETIME"),
    ("mi_playlist_items", "rb_track_id", "VARCHAR(40)"),
    ("mi_playlist_items", "resolucion", "VARCHAR(20)"),
    ("mi_playlist_items", "homonimos", "INTEGER"),
    ("mi_playlist_items", "genero_editado", "BOOLEAN"),
    ("mi_playlist_items", "import_motivo", "VARCHAR(200)"),
)


def _migrar(eng) -> list[str]:
    """Agrega las columnas de `_COLUMNAS_NUEVAS` que falten. Devuelve las que agregó."""
    agregadas = []
    with eng.begin() as con:
        for tabla, col, tipo in _COLUMNAS_NUEVAS:
            existentes = {r[1] for r in con.exec_driver_sql(f"PRAGMA table_info({tabla})")}
            if existentes and col not in existentes:
                con.exec_driver_sql(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
                agregadas.append(f"{tabla}.{col}")
    return agregadas


def init_db() -> None:
    """Crea las tablas si no existen. Se llama una vez en el lifespan del server."""
    Base.metadata.create_all(engine)
    for c in _migrar(engine):
        logger.info(f"🗄️  Columna agregada a una base existente: {c}")
    logger.info(f"🗄️  Base de datos lista: {DB_PATH.name}")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# --------------------------------------------------------------------------
#  Escrituras (best-effort: nunca propagan excepción)
# --------------------------------------------------------------------------
def registrar_busqueda(query: str, total: int) -> None:
    try:
        with SessionLocal() as s:
            s.add(Busqueda(query=(query or "")[:300], total=int(total or 0)))
            s.commit()
    except Exception as e:
        logger.warning(f"⚠️ Historial: no pude registrar la búsqueda: {e}")


def guardar_playlist(nombre: str, query: str, groups: list, total: int, encontradas: int) -> None:
    try:
        data = json.dumps({"query": query, "groups": groups}, ensure_ascii=False)
        with SessionLocal() as s:
            s.add(Playlist(nombre=(nombre or "Playlist")[:300], origen="lista",
                           total=int(total or 0), encontradas=int(encontradas or 0),
                           data_json=data))
            s.commit()
    except Exception as e:
        logger.warning(f"⚠️ Historial: no pude guardar la playlist: {e}")


def registrar_descarga(*, titulo="", artista="", fuente="", url="", formato="",
                       archivo="", ruta="", grade=None, color=None, calidad_txt=None,
                       bpm=None, camelot=None, genero=None, thumbnail=None) -> None:
    try:
        with SessionLocal() as s:
            s.add(Descarga(
                titulo=(titulo or "")[:400], artista=(artista or "")[:400],
                fuente=(fuente or "")[:40], url=url or "", formato=(formato or "")[:10],
                archivo=(archivo or "")[:500], ruta=ruta or "", grade=grade, color=color,
                calidad_txt=(calidad_txt or None), bpm=(int(bpm) if bpm else None),
                camelot=camelot, genero=genero, thumbnail=thumbnail,
            ))
            s.commit()
    except Exception as e:
        logger.warning(f"⚠️ Historial: no pude registrar la descarga: {e}")


# --------------------------------------------------------------------------
#  Lecturas
# --------------------------------------------------------------------------
def listar_historial(limite: int = 20) -> dict:
    try:
        with SessionLocal() as s:
            busquedas = [
                {"id": b.id, "query": b.query, "total": b.total, "creado_en": _iso(b.creado_en)}
                for b in s.scalars(select(Busqueda).order_by(desc(Busqueda.id)).limit(limite))
            ]
            playlists = [
                {"id": p.id, "nombre": p.nombre, "total": p.total, "creado_en": _iso(p.creado_en)}
                for p in s.scalars(select(Playlist).order_by(desc(Playlist.id)).limit(limite))
            ]
            descargas = [
                {"id": d.id, "titulo": d.titulo, "artista": d.artista, "fuente": d.fuente,
                 "formato": d.formato, "archivo": d.archivo, "grade": d.grade, "color": d.color,
                 "creado_en": _iso(d.creado_en)}
                for d in s.scalars(select(Descarga).order_by(desc(Descarga.id)).limit(limite))
            ]
        return {"busquedas": busquedas, "playlists": playlists, "descargas": descargas}
    except Exception as e:
        logger.warning(f"⚠️ Historial: no pude leer el historial: {e}")
        return {"busquedas": [], "playlists": [], "descargas": []}


def get_playlist(pid: int) -> dict | None:
    try:
        with SessionLocal() as s:
            p = s.get(Playlist, pid)
            if not p:
                return None
            data = json.loads(p.data_json or "{}")
            groups = data.get("groups", [])
            return {"groups": groups, "sel": [0] * len(groups), "origen": "busqueda",
                    "query": data.get("query", ""), "nombre": p.nombre}
    except Exception as e:
        logger.warning(f"⚠️ Historial: no pude leer la playlist {pid}: {e}")
        return None


def limpiar_historial(que: str = "todo") -> bool:
    """Borra el historial. `que` ∈ busquedas | playlists | descargas | todo."""
    tablas = {"busquedas": [Busqueda], "playlists": [Playlist], "descargas": [Descarga],
              "todo": [Busqueda, Playlist, Descarga]}.get(que, [Busqueda, Playlist, Descarga])
    try:
        with SessionLocal() as s:
            for modelo in tablas:
                for row in s.scalars(select(modelo)):
                    s.delete(row)
            s.commit()
        return True
    except Exception as e:
        logger.warning(f"⚠️ Historial: no pude limpiar ({que}): {e}")
        return False


def borrar_playlist(pid: int) -> bool:
    try:
        with SessionLocal() as s:
            p = s.get(Playlist, pid)
            if p:
                s.delete(p)
                s.commit()
            return True
    except Exception as e:
        logger.warning(f"⚠️ Historial: no pude borrar la playlist {pid}: {e}")
        return False


# ==========================================================================
#  Mis Playlists (crates) — playlists creadas por el usuario
# ==========================================================================
def _ident(fuente, url, titulo, artista) -> str:
    return f"{(fuente or '').lower()}|{url or ''}|{(titulo or '').lower()}|{(artista or '').lower()}"


def _bpm_decimal(v) -> float | None:
    """BPM con un decimal (el que usa toda la app), o None si no hay o no es un número > 0."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 1) if math.isfinite(x) and x > 0 else None


def _snapshot(track: dict) -> dict:
    return {
        "titulo": (track.get("titulo") or "")[:400],
        "artista": (track.get("artista") or "")[:400],
        "fuente": (track.get("fuente") or "")[:40],
        "url": track.get("url") or "",
        "thumbnail": track.get("thumbnail"),
        "duracion": int(track["duracion"]) if track.get("duracion") else None,
        "bpm": _bpm_decimal(track.get("bpm")),
        "camelot": track.get("camelot"),
        # f53: saneado como el que se edita a mano ("Sin género" de la home = sin género).
        "genero": sanear_genero(track.get("genero")),
        "solo_preview": bool(track.get("solo_preview")),
    }


def _tiene_archivo(it: "MiPlaylistItem") -> bool:
    """¿El item está bajado DE VERDAD? Tener `ruta` no alcanza: si el archivo se borró o se
    movió, decir "descargado" miente (el botón no ofrecería bajarlo y el .m3u8 lo pierde)."""
    return bool(it.ruta) and Path(it.ruta).is_file()


# Fuentes de un tema que es un archivo PROPIO del dueño, no algo que bajó MusiFlix (f53): de su
# biblioteca de Rekordbox (la home o una playlist importada) o de una carpeta de la PC.
FUENTES_LOCALES = ("rekordbox", "carpeta", "biblioteca")


def _archivo_estado(it: "MiPlaylistItem") -> str:
    """Qué pasa con el archivo del tema, en una palabra (f53):

    'ok' está en disco · 'no-existe' tenía archivo y ya no está donde se guardó · 'ambiguo' al
    importar había varios archivos DISTINTOS con ese nombre y no se eligió uno · 'no-encontrado'
    al importar no apareció en las carpetas permitidas · 'sin-archivo' nunca tuvo (por bajar)."""
    if it.ruta:
        return "ok" if Path(it.ruta).is_file() else "no-existe"
    if it.resolucion in ("ambiguo", "no-encontrado"):
        return it.resolucion
    return "sin-archivo"


def _item_dict(it: "MiPlaylistItem") -> dict:
    return {
        "id": it.id, "orden": it.orden, "titulo": it.titulo, "artista": it.artista,
        "fuente": it.fuente, "url": it.url, "thumbnail": it.thumbnail, "duracion": it.duracion,
        "bpm": it.bpm, "camelot": it.camelot, "genero": it.genero, "grade": it.grade,
        "color": it.color, "formato": it.formato, "descargado": _tiene_archivo(it),
        "solo_preview": bool(it.solo_preview), "playlist_id": it.playlist_id,
        # f53. `descargado` sigue queriendo decir "tiene el archivo en disco" (lo usan la descarga
        # —409 si ya lo tiene— y el .m3u8). Lo que NO dice es quién lo trajo: `local` = es un
        # archivo propio del dueño (importado o de su biblioteca), y la pantalla lo muestra como
        # «En tu PC», no como «Descargado». `nombre_archivo` es solo el nombre, nunca la ruta.
        "archivo_estado": _archivo_estado(it),
        "local": (it.fuente or "").lower() in FUENTES_LOCALES,
        "nombre_archivo": it.archivo or (Path(it.ruta).name if it.ruta else None),
        "homonimos": it.homonimos, "genero_editado": bool(it.genero_editado),
        "import_motivo": it.import_motivo,
    }


def _camelot_num(camelot: str | None) -> int | None:
    if not camelot:
        return None
    m = re.match(r"(\d{1,2})", str(camelot))
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 12 else None


# De dónde sale el BPM de un tema, de más a menos confiable (f53, auditoría): lo medido por el
# motor (solo lo sabe el server, ver `server._analisis_items`), lo que trae Rekordbox (la home o
# una playlist importada de Rekordbox) y "otro" (la búsqueda, los tags de una carpeta): sin medir.
FUENTES_BPM = ("motor", "rekordbox", "otro")


def dato_bpm(fuente) -> str:
    """El origen del BPM guardado en un item, por su fuente (igual que `server._dato_externo`)."""
    return "rekordbox" if (fuente or "").lower() in ("rekordbox", "biblioteca") else "otro"


def metricas_bpm(pares) -> dict:
    """Promedio y rango de BPM de una playlist SIN MEZCLAR FUENTES: solo con los temas de la
    fuente más confiable que haya (`FUENTES_BPM`), con un decimal, y cuántos de otras fuentes
    quedaron afuera. Sin ningún BPM: todo None (la pantalla dice «sin medir», no un número).
    `pares`: [(bpm, dato)]."""
    con = []
    for bpm, dato in pares:
        try:
            v = float(bpm)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0:
            con.append((v, dato if dato in ("motor", "rekordbox") else "otro"))
    for fuente in FUENTES_BPM:
        vs = [v for v, d in con if d == fuente]
        if vs:
            return {"bpm_prom": round(sum(vs) / len(vs), 1), "bpm_min": round(min(vs), 1),
                    "bpm_max": round(max(vs), 1), "bpm_fuente": fuente, "bpm_n": len(vs),
                    "bpm_afuera": len(con) - len(vs)}
    return {"bpm_prom": None, "bpm_min": None, "bpm_max": None, "bpm_fuente": None,
            "bpm_n": 0, "bpm_afuera": 0}


def _metrics(items: list) -> dict:
    total = len(items)
    descargados = sum(1 for it in items if _tiene_archivo(it))
    dur = sum((it.duracion or 0) for it in items)
    keys = [0] * 12                       # histograma por número Camelot (1..12)
    for it in items:
        n = _camelot_num(it.camelot)
        if n:
            keys[n - 1] += 1
    peak = max(range(12), key=lambda i: keys[i]) if any(keys) else None
    compat = 0
    if peak is not None:
        vecinos = {peak, (peak + 1) % 12, (peak - 1) % 12}   # ±1 en la rueda + mismo
        compat = sum(1 for it in items if (_camelot_num(it.camelot) or 0) - 1 in vecinos)
    return {"total": total, "descargados": descargados, "duracion": dur,
            **metricas_bpm((it.bpm, dato_bpm(it.fuente)) for it in items),
            "keys": keys, "peak": (peak + 1) if peak is not None else None, "compat_dominante": compat}


def crear_playlist(nombre: str) -> dict | None:
    try:
        with SessionLocal() as s:
            p = MiPlaylist(nombre=(nombre or "Playlist")[:300])
            s.add(p); s.commit()
            return {"id": p.id, "nombre": p.nombre}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude crear la playlist: {e}")
        return None


def renombrar_playlist(pid: int, nombre: str) -> bool:
    try:
        with SessionLocal() as s:
            p = s.get(MiPlaylist, pid)
            if p:
                p.nombre = (nombre or p.nombre)[:300]; s.commit()
            return bool(p)
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude renombrar: {e}"); return False


def borrar_playlist_mia(pid: int) -> bool:
    try:
        with SessionLocal() as s:
            for it in s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)):
                s.delete(it)
            p = s.get(MiPlaylist, pid)
            if p:
                s.delete(p)
            s.commit()
            return True
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude borrar la playlist: {e}"); return False


def activar_playlist(pid: int) -> bool:
    try:
        with SessionLocal() as s:
            for p in s.scalars(select(MiPlaylist)):
                p.activa = (p.id == pid)
            s.commit()
            return True
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude activar: {e}"); return False


def get_playlist_activa() -> dict | None:
    try:
        with SessionLocal() as s:
            p = s.scalars(select(MiPlaylist).where(MiPlaylist.activa.is_(True))).first()
            return {"id": p.id, "nombre": p.nombre} if p else None
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude leer la activa: {e}"); return None


def listar_playlists() -> list:
    try:
        with SessionLocal() as s:
            out = []
            for p in s.scalars(select(MiPlaylist).order_by(desc(MiPlaylist.id))):
                items = list(s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == p.id)))
                out.append({"id": p.id, "nombre": p.nombre, "activa": p.activa, **_origen_dict(p),
                            **_metrics(items)})
            return out
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude listar: {e}"); return []


def _origen_dict(p: "MiPlaylist") -> dict:
    """De dónde salió la playlist (f53). NULL en una base vieja = armada en la página."""
    return {"origen": p.origen or "musiflix", "origen_ref": p.origen_ref,
            "origen_archivo": p.origen_archivo, "importada_en": _iso(p.importada_en)}


def get_playlist_mia(pid: int, con_ruta: bool = False) -> dict | None:
    """La playlist con sus items. `con_ruta=True` agrega la `ruta` de cada item: SOLO para uso
    del server (el análisis y el estado del motor, f53); lo que viaja al navegador no la lleva."""
    try:
        with SessionLocal() as s:
            p = s.get(MiPlaylist, pid)
            if not p:
                return None
            items = list(s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)
                                   .order_by(MiPlaylistItem.orden, MiPlaylistItem.id)))
            return {"id": p.id, "nombre": p.nombre, "activa": p.activa, **_origen_dict(p),
                    "metrics": _metrics(items),
                    "items": [{**_item_dict(it), "ruta": it.ruta} if con_ruta else _item_dict(it)
                              for it in items]}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude leer la playlist {pid}: {e}"); return None


def _next_orden(s, pid) -> int:
    mx = s.scalar(select(func.max(MiPlaylistItem.orden)).where(MiPlaylistItem.playlist_id == pid))
    return (mx or 0) + 1


def _buscar_item(s, pid, ident):
    for it in s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)):
        if _ident(it.fuente, it.url, it.titulo, it.artista) == ident:
            return it
    return None


def _con_archivo(it: "MiPlaylistItem", ruta_local: str) -> None:
    it.ruta = ruta_local
    it.archivo = Path(ruta_local).name[:500]
    it.formato = Path(ruta_local).suffix.lstrip(".").lower()[:10] or None


def agregar_item(pid: int, track: dict, ruta_local: str | None = None) -> dict | None:
    """Agrega un tema a la playlist. `ruta_local` es el archivo del tema YA RESUELTO POR EL
    SERVER (hoy: el índice de la biblioteca local, `server._lib_audio`, a partir del `lib_id`
    que manda la home; f53, tomado de f33). Nunca una ruta que haya mandado el cliente:
    `_snapshot` no lee `ruta` del cuerpo, y cualquier página abierta en el navegador puede
    pegarle a este endpoint — una ruta suya haría que el motor lea cualquier archivo de la PC.

    Con archivo, el dedupe es por ARCHIVO: dos archivos distintos con el mismo artista y título
    (el mismo master en dos carpetas) entran los dos. Si el tema ya estaba SIN archivo (la home
    lo guardaba así antes), se le completa: es la forma de arreglar esos items, agregándolos de
    nuevo (`archivo_completado`)."""
    try:
        snap = _snapshot(track)
        ident = _ident(snap["fuente"], snap["url"], snap["titulo"], snap["artista"])
        with SessionLocal() as s:
            if not s.get(MiPlaylist, pid):
                return None
            previo = _buscar_item(s, pid, ident)
            if ruta_local:
                clave = os.path.normcase(ruta_local)
                mismo = next((it for it in s.scalars(select(MiPlaylistItem).where(
                    MiPlaylistItem.playlist_id == pid))
                    if it.ruta and os.path.normcase(it.ruta) == clave), None)
                if mismo is not None:
                    return {"ok": True, "dup": True, "id": mismo.id, "archivo_completado": False}
                if previo is not None and previo.ruta:
                    previo = None       # mismo nombre, OTRO archivo: es otro item
            if previo is None and ruta_local and snap["fuente"]:
                # Un item viejo de la home se guardaba SIN fuente (""): el mismo tema que
                # llega ahora como "biblioteca" con su archivo lo completa, no se duplica.
                viejo = _buscar_item(s, pid, _ident("", snap["url"], snap["titulo"], snap["artista"]))
                if viejo is not None and not viejo.ruta:
                    previo = viejo
                    previo.fuente = snap["fuente"]
            if previo:   # dedupe
                completado = bool(ruta_local) and not previo.ruta
                if completado:
                    _con_archivo(previo, ruta_local)
                    s.commit()
                return {"ok": True, "dup": True, "id": previo.id, "archivo_completado": completado}
            it = MiPlaylistItem(playlist_id=pid, orden=_next_orden(s, pid), **snap)
            if ruta_local:
                _con_archivo(it, ruta_local)
            s.add(it); s.commit()
            return {"ok": True, "id": it.id}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude agregar item: {e}"); return None


# --------------------------------------------------------------------------
#  Playlists importadas (f53): de Rekordbox o de una carpeta de la PC
# --------------------------------------------------------------------------
# Un tema importado llega como dict con: titulo, artista, genero, bpm, camelot, duracion,
# ruta (el archivo YA resuelto por el server, o None), archivo (el nombre, para mostrarlo
# aunque no se haya encontrado), resolucion, homonimos, rb_track_id, import_motivo.
# El género del tema importado es el que trae el origen (vacío si no trae: no se inventa).

GENERO_MAX = 60
_SIN_GENERO = {"sin género", "sin genero"}


def sanear_genero(valor) -> str | None:
    """Un género como se guarda: texto corto, sin caracteres de control y con los espacios
    colapsados. Vacío, "Sin género" (como rotula la home al que no tiene) o algo que no es texto
    → None: un tema sin género no tiene género, no uno inventado."""
    if not isinstance(valor, str):
        return None
    limpio = re.sub(r"[\x00-\x1f\x7f]+", " ", valor)
    limpio = re.sub(r"\s+", " ", limpio).strip()[:GENERO_MAX].strip()
    if not limpio or limpio.casefold() in _SIN_GENERO:
        return None
    return limpio


def _item_importado(pid: int, orden: int, origen: str, t: dict) -> "MiPlaylistItem":
    it = MiPlaylistItem(
        playlist_id=pid, orden=orden, titulo=(t.get("titulo") or "")[:400],
        artista=(t.get("artista") or "")[:400], fuente=origen, url="",
        duracion=int(t["duracion"]) if t.get("duracion") else None,
        bpm=_bpm_decimal(t.get("bpm")), camelot=t.get("camelot") or None,
        genero=sanear_genero(t.get("genero")), solo_preview=False,
        rb_track_id=(str(t["rb_track_id"])[:40] if t.get("rb_track_id") else None),
        resolucion=t.get("resolucion"), homonimos=t.get("homonimos"),
        import_motivo=(t.get("import_motivo") or None), genero_editado=False)
    _refrescar_archivo(it, t)
    return it


def _refrescar_archivo(it: "MiPlaylistItem", t: dict) -> None:
    if t.get("ruta"):
        _con_archivo(it, t["ruta"])
    else:
        it.ruta, it.formato = None, None
        it.archivo = (t.get("archivo") or "")[:500] or None
    it.resolucion, it.homonimos = t.get("resolucion"), t.get("homonimos")


def buscar_importada(origen: str, origen_ref: str) -> dict | None:
    """La playlist ya importada de ese origen (`origen_ref`: la ruta de la playlist adentro de
    Rekordbox o la carpeta relativa), o None."""
    try:
        with SessionLocal() as s:
            p = s.scalars(select(MiPlaylist).where(MiPlaylist.origen == origen,
                                                   MiPlaylist.origen_ref == origen_ref)
                          .order_by(MiPlaylist.id)).first()
            return {"id": p.id, "nombre": p.nombre} if p else None
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude buscar la importada: {e}")
        return None


def crear_importada(nombre: str, origen: str, origen_ref: str, origen_archivo: str | None,
                    temas: list[dict]) -> dict | None:
    """Crea la playlist importada con sus temas EN EL ORDEN del origen. Todo en una
    transacción: si algo falla no queda una playlist a medias."""
    try:
        with SessionLocal() as s:
            p = MiPlaylist(nombre=(nombre or "Playlist")[:300], origen=origen,
                           origen_ref=(origen_ref or "")[:300] or None,
                           origen_archivo=(origen_archivo or "")[:200] or None,
                           importada_en=_ahora())
            s.add(p)
            s.flush()
            for i, t in enumerate(temas, 1):
                s.add(_item_importado(p.id, i, origen, t))
            s.commit()
            return {"id": p.id, "nombre": p.nombre, "temas": len(temas)}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude crear la playlist importada: {e}")
        return None


def _clave_importado(origen: str, t) -> str | None:
    """Con qué se reconoce el MISMO tema al actualizar: el TrackID de Rekordbox, o el archivo
    (en una carpeta no hay otro id)."""
    if isinstance(t, dict):
        rb, ruta, archivo = t.get("rb_track_id"), t.get("ruta"), t.get("archivo")
    else:
        rb, ruta, archivo = t.rb_track_id, t.ruta, t.archivo
    if origen == "rekordbox":
        return f"rb:{rb}" if rb else None
    if ruta:
        return "ruta:" + os.path.normcase(str(ruta))
    return f"nombre:{(archivo or '').casefold()}" if archivo else None


def actualizar_importada(pid: int, origen: str, temas: list[dict],
                         origen_archivo: str | None = None) -> dict | None:
    """Actualiza una playlist ya importada con lo que trae el origen HOY, sin borrar nada:

    - un tema nuevo se agrega al final, en el orden del origen;
    - uno que ya estaba refresca título, artista, BPM, key y duración del origen, y su archivo
      si antes no se había encontrado (o si el origen lo ubica en otro lado);
    - el GÉNERO editado a mano gana: el del origen solo se pone si el dueño no lo tocó;
    - lo que el dueño quitó, reordenó o agregó a mano no se toca, y un tema que el origen ya no
      trae tampoco se borra (el dueño decide).
    Devuelve {agregados, actualizados} o None si la playlist no existe."""
    try:
        with SessionLocal() as s:
            p = s.get(MiPlaylist, pid)
            if not p or (p.origen or "musiflix") != origen:
                return None
            existentes = {}
            for it in s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)):
                k = _clave_importado(origen, it)
                if k and k not in existentes:
                    existentes[k] = it
            orden = _next_orden(s, pid)
            agregados = actualizados = 0
            for t in temas:
                k = _clave_importado(origen, t)
                it = existentes.get(k) if k else None
                if it is None:
                    nuevo = _item_importado(pid, orden, origen, t)
                    s.add(nuevo)
                    orden += 1
                    agregados += 1
                    if k:
                        existentes[k] = nuevo
                    continue
                it.titulo = (t.get("titulo") or it.titulo or "")[:400]
                it.artista = (t.get("artista") or it.artista or "")[:400]
                it.bpm = _bpm_decimal(t.get("bpm"))
                it.camelot = t.get("camelot") or None
                if t.get("duracion"):
                    it.duracion = int(t["duracion"])
                if not it.genero_editado:
                    it.genero = sanear_genero(t.get("genero"))
                if t.get("ruta") or not _tiene_archivo(it):
                    _refrescar_archivo(it, t)
                it.import_motivo = t.get("import_motivo") or None
                actualizados += 1
            if origen_archivo:
                p.origen_archivo = origen_archivo[:200]
            p.importada_en = _ahora()
            s.commit()
            return {"id": p.id, "nombre": p.nombre, "agregados": agregados,
                    "actualizados": actualizados}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude actualizar la importada: {e}")
        return None


def editar_genero_item(pid: int, item_id: int, genero) -> dict | None:
    """Pone el género de UN tema (a mano: queda marcado y una actualización de la importación
    no lo pisa). `genero` vacío lo deja sin género. None si el item no es de esa playlist."""
    try:
        with SessionLocal() as s:
            it = s.get(MiPlaylistItem, item_id)
            if not it or it.playlist_id != pid:
                return None
            it.genero = sanear_genero(genero)
            it.genero_editado = True
            s.commit()
            return _item_dict(it)
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude editar el item {item_id}: {e}")
        return None


def genero_a_los_sin_genero(pid: int, genero) -> int | None:
    """Pone `genero` a TODOS los temas de la playlist que no tienen. Los que ya tienen uno no
    se tocan. Devuelve cuántos cambió, o None si la playlist no existe."""
    g = sanear_genero(genero)
    if g is None:
        return 0
    try:
        with SessionLocal() as s:
            if not s.get(MiPlaylist, pid):
                return None
            n = 0
            for it in s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)):
                if sanear_genero(it.genero) is None:
                    it.genero, it.genero_editado = g, True
                    n += 1
            s.commit()
            return n
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude poner el género: {e}")
        return None


def quitar_item(item_id: int) -> bool:
    try:
        with SessionLocal() as s:
            it = s.get(MiPlaylistItem, item_id)
            if it:
                s.delete(it); s.commit()
            return True
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude quitar item: {e}"); return False


def reordenar(pid: int, ids: list) -> bool:
    try:
        with SessionLocal() as s:
            pos = {int(iid): i for i, iid in enumerate(ids)}
            for it in s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)):
                if it.id in pos:
                    it.orden = pos[it.id]
            s.commit()
            return True
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude reordenar: {e}"); return False


def marcar_descargado(pid: int, track: dict, archivo, ruta, formato=None, grade=None, color=None) -> None:
    """Al bajar con una crate activa: completa el item (o lo agrega ya descargado)."""
    try:
        snap = _snapshot(track)
        ident = _ident(snap["fuente"], snap["url"], snap["titulo"], snap["artista"])
        with SessionLocal() as s:
            if not s.get(MiPlaylist, pid):
                return
            it = _buscar_item(s, pid, ident)
            if not it:
                it = MiPlaylistItem(playlist_id=pid, orden=_next_orden(s, pid), **snap)
                s.add(it)
            it.archivo, it.ruta, it.formato, it.grade, it.color = archivo, ruta, formato, grade, color
            s.commit()
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude marcar descargado: {e}")


def get_item(pid: int, item_id: int) -> dict | None:
    """El item `item_id` SOLO si es de la playlist `pid` (None si no existe o es de otra).

    Es lo que usa la descarga desde la playlist (f41): el server baja lo que dice el item
    guardado, nunca una url que mande el cliente. Trae `ruta` porque el endpoint necesita
    saber si ya está bajado; `_item_dict` no la expone a la pantalla."""
    try:
        with SessionLocal() as s:
            it = s.get(MiPlaylistItem, item_id)
            if not it or it.playlist_id != pid:
                return None
            return {**_item_dict(it), "ruta": it.ruta}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude leer el item {item_id}: {e}")
        return None


def marcar_item_descargado(pid: int, item_id: int, archivo, ruta, formato=None,
                           grade=None, color=None, duracion=None) -> bool:
    """Completa ESE item con el archivo bajado (f41). A diferencia de `marcar_descargado`,
    no busca por identidad aproximada ni agrega filas: si el item ya no está en la playlist
    (lo quitaron mientras se bajaba) devuelve False y no toca nada.

    `duracion`: la MEDIDA del archivo bajado (s). Solo completa un item que no tenía duración
    (hitplayer no la da): sirve para el #EXTINF del .m3u8 y la duración total. Una duración que
    ya estaba no se pisa: si no cuadraba con el archivo, la descarga ya se rechazó antes."""
    try:
        with SessionLocal() as s:
            it = s.get(MiPlaylistItem, item_id)
            if not it or it.playlist_id != pid:
                return False
            it.archivo, it.ruta, it.formato, it.grade, it.color = archivo, ruta, formato, grade, color
            if duracion and not it.duracion:
                it.duracion = int(round(duracion))
            s.commit()
            return True
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude marcar el item {item_id}: {e}")
        return False


def armar_m3u8(pid: int) -> dict | None:
    """Escribe un .m3u8 con los items que tienen archivo local. Devuelve el recibo."""
    try:
        downloads = DOWNLOADS_DIR
        with SessionLocal() as s:
            p = s.get(MiPlaylist, pid)
            if not p:
                return None
            items = list(s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)
                                   .order_by(MiPlaylistItem.orden, MiPlaylistItem.id)))
            nombre = p.nombre
        con_archivo = [it for it in items if _tiene_archivo(it)]
        # Excluido = todo lo que no entra, también un item con ruta cuyo archivo ya no está
        # (antes no contaba ni como incluido ni como excluido: la cuenta no cerraba).
        sin_bajar = [it for it in items if not _tiene_archivo(it)]
        bajos = [it.titulo for it in con_archivo if it.grade in ("D", "F")]
        nombre_safe = re.sub(r'[\\/:*?"<>|]+', "_", nombre)[:80].strip() or "playlist"
        destino = downloads / f"{nombre_safe}.m3u8"
        lineas, dur_total = ["#EXTM3U"], 0
        for it in con_archivo:
            dur = int(it.duracion or 0); dur_total += dur
            lineas.append(f"#EXTINF:{dur},{it.artista} - {it.titulo}")
            lineas.append(str(Path(it.ruta).resolve()))
        destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        return {"ok": True, "ruta": str(destino), "archivo": destino.name, "nombre": nombre,
                "incluidos": len(con_archivo), "excluidos": len(sin_bajar), "bajos": bajos, "duracion": dur_total}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude exportar el .m3u8: {e}")
        return None
