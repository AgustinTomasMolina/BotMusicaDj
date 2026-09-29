"""Cambios a la base de juguete del E2E mientras el server corre (ver pantalla.mjs).

Simulan lo que en la vida real hace un re-escaneo, para probar que un set guardado es la FOTO
de lo que se vio y no un re-armado:

    bpm <db> <titulo> <bpm>                  cambia el BPM del track; imprime {"antes": <bpm>}
    ocultar <db> <titulo> <respaldo.json>    saca el track de la base (como si se hubiera
                                             borrado el archivo y re-escaneado) y guarda la fila
    restaurar <db> <respaldo.json>           vuelve a poner la fila tal cual estaba

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


if __name__ == "__main__":
    comando, *args = sys.argv[1:]
    print(json.dumps({"bpm": bpm, "ocultar": ocultar, "restaurar": restaurar}[comando](*args)))
