"""Cambios a la base de juguete del E2E mientras el server corre (ver pantalla.mjs).

Simulan lo que en la vida real hace un re-escaneo, para probar que un set guardado es la FOTO
de lo que se vio y no un re-armado:

    bpm <db> <titulo> <bpm>                  cambia el BPM del track; imprime {"antes": <bpm>}
    ocultar <db> <titulo> <respaldo.json>    saca el track de la base (como si se hubiera
                                             borrado el archivo y re-escaneado) y guarda la fila
    restaurar <db> <respaldo.json>           vuelve a poner la fila tal cual estaba
    editor_alta <db> <json>                  f54: alta de los temas del editor D (editor_d)
    editor_baja <db> <json>                  f54: los saca de la base

Cada caso que lo usa deja la base como la encontró (en un `finally`): los demás casos arman
sets contra la misma base. Toca SQLite directo y no `Store.upsert` porque lo que se quiere es
cambiar UN dato y nada más. Invalida `norm_stats` igual que las altas y bajas de `Store`.
"""
import json
import sqlite3
import sys


def _abrir(db: str) -> sqlite3.Connection:
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _fila(con: sqlite3.Connection, titulo: str) -> sqlite3.Row:
    filas = con.execute("SELECT * FROM tracks WHERE title = ?", (titulo,)).fetchall()
    if len(filas) != 1:
        raise SystemExit(f"esperaba un track «{titulo}» en la base y hay {len(filas)}")
    return filas[0]


def bpm(db: str, titulo: str, nuevo: str) -> dict:
    with _abrir(db) as con:
        antes = _fila(con, titulo)["bpm"]
        con.execute("UPDATE tracks SET bpm = ? WHERE title = ?", (float(nuevo), titulo))
        con.execute("DELETE FROM norm_stats")
    return {"antes": antes}


def ocultar(db: str, titulo: str, respaldo: str) -> dict:
    with _abrir(db) as con:
        fila = _fila(con, titulo)
        # `.keys()` a propósito: iterar un sqlite3.Row da los VALORES, no los nombres.
        datos = {k: ({"hex": fila[k].hex()} if isinstance(fila[k], bytes) else fila[k])
                 for k in fila.keys()}  # noqa: SIM118
        with open(respaldo, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        con.execute("DELETE FROM tracks WHERE path_key = ?", (fila["path_key"],))
        con.execute("DELETE FROM norm_stats")
    return {"path": fila["path"]}


def restaurar(db: str, respaldo: str) -> dict:
    with open(respaldo, encoding="utf-8") as f:
        datos = json.load(f)
    valores = {k: (bytes.fromhex(v["hex"]) if isinstance(v, dict) else v) for k, v in datos.items()}
    with _abrir(db) as con:
        cols = list(valores)
        con.execute(f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [valores[c] for c in cols])
        con.execute("DELETE FROM norm_stats")
    return {"path": valores["path"]}


def _temas_editor(datos: str) -> dict:
    """Los temas del editor D que armó base_juguete.py (`editor_d`), en JSON."""
    temas = json.loads(datos)
    if not isinstance(temas, dict) or not temas:
        raise SystemExit("editor: esperaba el JSON de base_juguete.editor_d")
    return temas


def editor_alta(db: str, datos: str) -> dict:
    """f54: da de alta en la base del motor los WAV del editor D con lo que se conoce por
    construcción (el BPM y la key con que se GENERARON, base_juguete.editor_d): es lo que haría
    un análisis que acierta, sin esperar a librosa. Por `Store.upsert` (y no SQLite directo):
    guarda el mtime del archivo, así el tema queda «analizado» y no vuelve a la cola."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(raiz), str(raiz / "tests")]
    from sinteticos import embedding

    from motor.modelos import TrackFeatures
    from motor.store import Store

    with Store(Path(db)) as store:
        for i, (titulo, t) in enumerate(_temas_editor(datos).items()):
            unanime = t["camelot"] != ""
            store.upsert(Path(t["ruta"]),
                         TrackFeatures(bpm=float(t["bpm"]), key=t["camelot"], energy_raw=0.2,
                                       embedding=embedding(100 + i),
                                       key_acuerdo="3/3" if unanime else "0/0",
                                       key_tramos="|".join([t["camelot"]] * 3) if unanime else ""),
                         duration=float(t["dur"]), artist="Generador", title=titulo)
    return {"alta": len(_temas_editor(datos))}


def editor_baja(db: str, datos: str) -> dict:
    """Lo inverso de `editor_alta`: deja la base del motor como estaba (los WAV quedan)."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(raiz)]
    from motor.store import Store

    with Store(Path(db)) as store:
        n = sum(store.delete(Path(t["ruta"])) for t in _temas_editor(datos).values())
    return {"baja": n}


if __name__ == "__main__":
    comando, *args = sys.argv[1:]
    print(json.dumps({"bpm": bpm, "ocultar": ocultar, "restaurar": restaurar,
                      "editor_alta": editor_alta, "editor_baja": editor_baja}[comando](*args)))
