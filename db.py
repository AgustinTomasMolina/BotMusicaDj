"""Persistencia local (SQLite + SQLAlchemy 2.0) del historial de MusiFlix:
búsquedas, playlists (modo lista) y descargas. Single-user por ahora, pero el
esquema ya tiene `user_id` (nullable) para no migrar cuando se agregue login.

Todas las escrituras se llaman con `asyncio.to_thread` desde server.py y están
envueltas en try/except: si la DB falla, la búsqueda/descarga NO se rompe.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, String, Text,
                        create_engine, desc, func, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

logger = logging.getLogger("bot_web")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "musiflix.db"

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
    __tablename__ = "descargas_hist"  # no chocar con la 'descargas' de database.py (dead)
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
    bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    camelot: Mapped[str | None] = mapped_column(String(8), nullable=True)
    genero: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # campos de descarga: nullable → si ruta está vacía el item está "por bajar"
    archivo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ruta: Mapped[str | None] = mapped_column(Text, nullable=True)
    formato: Mapped[str | None] = mapped_column(String(10), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agregado_en: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


def init_db() -> None:
    """Crea las tablas si no existen. Se llama una vez en el lifespan del server."""
    Base.metadata.create_all(engine)
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


def _snapshot(track: dict) -> dict:
    return {
        "titulo": (track.get("titulo") or "")[:400],
        "artista": (track.get("artista") or "")[:400],
        "fuente": (track.get("fuente") or "")[:40],
        "url": track.get("url") or "",
        "thumbnail": track.get("thumbnail"),
        "duracion": int(track["duracion"]) if track.get("duracion") else None,
        "bpm": int(track["bpm"]) if track.get("bpm") else None,
        "camelot": track.get("camelot"),
        "genero": track.get("genero"),
    }


def _item_dict(it: "MiPlaylistItem") -> dict:
    return {
        "id": it.id, "orden": it.orden, "titulo": it.titulo, "artista": it.artista,
        "fuente": it.fuente, "url": it.url, "thumbnail": it.thumbnail, "duracion": it.duracion,
        "bpm": it.bpm, "camelot": it.camelot, "genero": it.genero, "grade": it.grade,
        "color": it.color, "formato": it.formato, "descargado": bool(it.ruta),
    }


def _camelot_num(camelot: str | None) -> int | None:
    if not camelot:
        return None
    m = re.match(r"(\d{1,2})", str(camelot))
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 12 else None


def _metrics(items: list) -> dict:
    total = len(items)
    descargados = sum(1 for it in items if it.ruta)
    dur = sum((it.duracion or 0) for it in items)
    bpms = [it.bpm for it in items if it.bpm]
    bpm_prom = round(sum(bpms) / len(bpms)) if bpms else None
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
    return {"total": total, "descargados": descargados, "duracion": dur, "bpm_prom": bpm_prom,
            "bpm_min": min(bpms) if bpms else None, "bpm_max": max(bpms) if bpms else None,
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
                out.append({"id": p.id, "nombre": p.nombre, "activa": p.activa, **_metrics(items)})
            return out
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude listar: {e}"); return []


def get_playlist_mia(pid: int) -> dict | None:
    try:
        with SessionLocal() as s:
            p = s.get(MiPlaylist, pid)
            if not p:
                return None
            items = list(s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)
                                   .order_by(MiPlaylistItem.orden, MiPlaylistItem.id)))
            return {"id": p.id, "nombre": p.nombre, "activa": p.activa,
                    "metrics": _metrics(items), "items": [_item_dict(it) for it in items]}
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


def agregar_item(pid: int, track: dict) -> dict | None:
    try:
        snap = _snapshot(track)
        ident = _ident(snap["fuente"], snap["url"], snap["titulo"], snap["artista"])
        with SessionLocal() as s:
            if not s.get(MiPlaylist, pid):
                return None
            if _buscar_item(s, pid, ident):   # dedupe
                return {"ok": True, "dup": True}
            it = MiPlaylistItem(playlist_id=pid, orden=_next_orden(s, pid), **snap)
            s.add(it); s.commit()
            return {"ok": True, "id": it.id}
    except Exception as e:
        logger.warning(f"⚠️ Crates: no pude agregar item: {e}"); return None


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


def armar_m3u8(pid: int) -> dict | None:
    """Escribe un .m3u8 con los items que tienen archivo local. Devuelve el recibo."""
    try:
        downloads = BASE_DIR / "downloads"
        with SessionLocal() as s:
            p = s.get(MiPlaylist, pid)
            if not p:
                return None
            items = list(s.scalars(select(MiPlaylistItem).where(MiPlaylistItem.playlist_id == pid)
                                   .order_by(MiPlaylistItem.orden, MiPlaylistItem.id)))
            nombre = p.nombre
        con_archivo = [it for it in items if it.ruta and Path(it.ruta).exists()]
        sin_bajar = [it for it in items if not it.ruta]
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
