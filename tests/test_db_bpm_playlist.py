"""El BPM de las playlists propias se guarda con su decimal (auditoría de f28, M3).

Antes `_snapshot` hacía `int(track["bpm"])`: un 127.9 quedaba "BPM 127" (§6: un dato que
miente). La columna pasó a Float sin migración: la base del dueño (volumen musiflix-data)
tiene la columna como INTEGER, y SQLite guarda igual el 127.9 como REAL. El test arma una
base con el esquema VIEJO para comprobarlo contra la base que existe de verdad.
"""
import sqlite3

import db
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable


@pytest.fixture
def base_vieja(tmp_path, monkeypatch):
    ruta = tmp_path / "musiflix.db"
    engine = create_engine(f"sqlite:///{ruta}")
    with engine.begin() as con:
        con.exec_driver_sql(str(CreateTable(db.MiPlaylist.__table__).compile(engine)))
        ddl = str(CreateTable(db.MiPlaylistItem.__table__).compile(engine))
        assert "bpm FLOAT" in ddl, ddl
        con.exec_driver_sql(ddl.replace("bpm FLOAT", "bpm INTEGER"))   # como la creó el código viejo
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    return ruta


def test_bpm_decimal_se_guarda_y_vuelve_en_una_base_con_la_columna_vieja(base_vieja):
    p = db.crear_playlist("Set")
    pid = p["id"]
    # Una fila de antes del arreglo, ya truncada: queda como estaba (no se inventa el decimal).
    con = sqlite3.connect(base_vieja)
    con.execute("INSERT INTO mi_playlist_items (playlist_id, orden, titulo, artista, fuente, url, bpm, agregado_en) "
                "VALUES (?, 1, 'Viejo', 'A', 'youtube', 'u0', 127, '2026-09-01 00:00:00')", (pid,))
    con.commit()
    con.close()
    assert db.agregar_item(pid, {"titulo": "Nuevo", "artista": "B", "fuente": "youtube", "url": "u1", "bpm": 127.9})["ok"]
    assert db.agregar_item(pid, {"titulo": "Redondeo", "artista": "C", "fuente": "youtube", "url": "u2", "bpm": "128.04"})["ok"]
    assert db.agregar_item(pid, {"titulo": "Sin", "artista": "D", "fuente": "youtube", "url": "u3", "bpm": 0})["ok"]
    items = {it["titulo"]: it["bpm"] for it in db.get_playlist_mia(pid)["items"]}
    assert items == {"Viejo": 127, "Nuevo": 127.9, "Redondeo": 128.0, "Sin": None}, items
    con = sqlite3.connect(base_vieja)
    tipos = dict(con.execute("SELECT titulo, typeof(bpm) FROM mi_playlist_items").fetchall())
    con.close()
    assert tipos["Nuevo"] == "real", "SQLite tenía que guardar el decimal aunque la columna diga INTEGER"


def test_metricas_con_bpm_decimal(base_vieja):
    pid = db.crear_playlist("M")["id"]
    for i, bpm in enumerate((127.9, 130.2)):
        db.agregar_item(pid, {"titulo": f"t{i}", "artista": "A", "fuente": "youtube", "url": f"u{i}", "bpm": bpm})
    m = db.get_playlist_mia(pid)["metrics"]
    assert (m["bpm_min"], m["bpm_max"]) == (127.9, 130.2)
    assert m["bpm_fuente"] == "otro"


def test_metricas_promedio_con_un_decimal(base_vieja):
    """El promedio iba redondeado a entero (`round(x)`): 127.9 y 130.5 daban 129."""
    pid = db.crear_playlist("Prom")["id"]
    for i, bpm in enumerate((127.9, 130.5)):
        db.agregar_item(pid, {"titulo": f"t{i}", "artista": "A", "fuente": "youtube", "url": f"u{i}", "bpm": bpm})
    m = db.get_playlist_mia(pid)["metrics"]
    assert m["bpm_prom"] == 129.2, f"el promedio va con un decimal: {m['bpm_prom']}"


def test_metricas_no_mezclan_fuentes(base_vieja):
    """Auditoría f53: la playlist «Techno» (temas de la búsqueda) y una con un tema de Rekordbox:
    el promedio y el rango salen SOLO de la fuente más confiable, y se dice cuántos quedan afuera."""
    pid = db.crear_playlist("Techno")["id"]
    for i, bpm in enumerate((99.0, 172.0)):
        db.agregar_item(pid, {"titulo": f"yt{i}", "artista": "A", "fuente": "youtube", "url": f"u{i}", "bpm": bpm})
    m = db.get_playlist_mia(pid)["metrics"]
    assert (m["bpm_prom"], m["bpm_min"], m["bpm_max"], m["bpm_fuente"], m["bpm_n"], m["bpm_afuera"]) == \
        (135.5, 99.0, 172.0, "otro", 2, 0)
    db.agregar_item(pid, {"titulo": "rb", "artista": "B", "fuente": "biblioteca", "bpm": 126.4})
    m = db.get_playlist_mia(pid)["metrics"]
    assert (m["bpm_prom"], m["bpm_min"], m["bpm_max"], m["bpm_fuente"], m["bpm_n"], m["bpm_afuera"]) == \
        (126.4, 126.4, 126.4, "rekordbox", 1, 2), "el rango mezcló los BPM de la búsqueda con el de Rekordbox"
    lista = {p["id"]: p for p in db.listar_playlists()}[pid]
    assert (lista["bpm_prom"], lista["bpm_fuente"]) == (126.4, "rekordbox")


def test_metricas_sin_bpm_no_inventan_un_numero(base_vieja):
    pid = db.crear_playlist("Vacía de BPM")["id"]
    db.agregar_item(pid, {"titulo": "t", "artista": "A", "fuente": "youtube", "url": "u", "bpm": 0})
    m = db.get_playlist_mia(pid)["metrics"]
    assert (m["bpm_prom"], m["bpm_min"], m["bpm_max"], m["bpm_fuente"], m["bpm_n"]) == (None, None, None, None, 0)


def test_metricas_bpm_prefiere_el_motor():
    m = db.metricas_bpm([(128.04, "motor"), (140, "rekordbox"), (99, "otro"), (None, "motor"), (0, "otro")])
    assert (m["bpm_prom"], m["bpm_min"], m["bpm_max"], m["bpm_fuente"], m["bpm_n"], m["bpm_afuera"]) == \
        (128.0, 128.0, 128.0, "motor", 1, 2)
