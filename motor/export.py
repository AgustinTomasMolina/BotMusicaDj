"""Export del set a formatos que entienden Rekordbox y Traktor.

Implementado: M3U8 extendido. Los dos lo importan y **respetan el orden**, que es lo
único imprescindible para que el set llegue a la controladora tal como lo armó el motor.
Un export que reordena no es un export: es otra lista.

No está implementado el cue sheet (el tracklist con tiempos de entrada para publicar
junto a un mix grabado). Necesita el solape real entre tracks, que lo decide el mezclador
y todavía no existe.
"""
from __future__ import annotations

import contextlib
from collections.abc import Sequence
from pathlib import Path

from motor.modelos import Track

# Fin de línea del M3U8: CRLF, fijo, en toda plataforma.
#
# Por qué fijarlo: `Path.write_text(...)` sin `newline` abre en modo texto y traduce cada
# "\n" al separador del sistema. O sea que el MISMO set exportado en Windows y en Linux
# da dos archivos distintos byte a byte. Eso choca con el determinismo de spec §5, y
# además vuelve imposible comparar la salida contra un esperado fijo en los tests.
#
# Por qué CRLF y no LF: el M3U8 admite los dos — RFC 8216 §4.1 dice textualmente que cada
# línea termina en LF o en CRLF — así que la elección no es de corrección sino de
# compatibilidad. CRLF es la convención del M3U desde Winamp y lo que produce cualquier
# herramienta del lado Windows, que es donde se genera y donde corre Rekordbox acá. El
# riesgo conocido del CRLF es el parser ingenuo que corta por "\n" y se queda un "\r"
# pegado al final de la ruta; el test de round-trip de este módulo parsea con esa misma
# ingenuidad a propósito, para que ese caso quede cubierto y no dependa de suerte.
#
# NO verificado contra Rekordbox: no hay Rekordbox en esta máquina (ver informe, tarea 15).
NEWLINE = "\r\n"


def write_m3u8(
    tracks: Sequence[Track],
    output: Path | str,
    relative_to: Path | None = None,
) -> Path:
    """Escribe un M3U8 extendido con el set en orden y devuelve la ruta escrita.

    Por cada track, tres líneas:

    - ``#EXTINF:<segundos>,<label>`` — lo estándar, lo que muestra el reproductor.
    - ``#DJRADIO:bpm=… key=… energy=…`` — comentario propio. Rekordbox y Traktor
      ignoran cualquier ``#`` que no conozcan, así que no molesta, y deja leer el set a
      ojo: por qué un track sigue al otro (spec §6, "la radio muestra por qué eligió cada
      track"). El BPM va con UN decimal, no redondeado a entero: §6 dice que redondearlo
      es mentir, y la diferencia entre 128.0 y 128.4 es justo la que decide si dos tracks
      mezclan.
    - la ruta al archivo.

    `relative_to` escribe las rutas relativas a esa carpeta — para la playlist que viaja
    junto a los archivos, típicamente a un pendrive. Una ruta que cae FUERA de ese árbol
    se deja absoluta: es lo único honesto, porque la alternativa (un "../../.." al aire)
    apunta a un archivo que en el destino no va a estar. Sin `relative_to`, todo absoluto,
    que es lo robusto dentro de la misma máquina.

    Un `.m3u8` es UTF-8 por definición (esa es la diferencia con el `.m3u` viejo), y se
    escribe sin BOM: el BOM se le pegaría al ``#EXTM3U`` de la primera línea y hay
    parsers que entonces no reconocen la cabecera.
    """
    output = Path(output)
    lines = ["#EXTM3U"]

    for track in tracks:
        # EXTINF va en segundos enteros. -1 es el "no sé cuánto dura" del formato: mejor
        # eso que un 0, que algunos reproductores muestran como track de duración cero.
        duration = int(round(track.duration)) if track.duration and track.duration > 0 else -1
        lines.append(f"#EXTINF:{duration},{track.label}")
        lines.append(f"#DJRADIO:bpm={track.bpm:.1f} key={track.key} energy={track.energy:.2f}")

        path = track.path
        if relative_to is not None:
            # Si cae fuera del árbol, `relative_to` levanta ValueError y la ruta queda
            # absoluta: es lo correcto, no un caso que tapar.
            with contextlib.suppress(ValueError):
                path = path.relative_to(relative_to)
        lines.append(str(path))

    # El salto se pone a mano y `newline=""` apaga la traducción del sistema, así lo que
    # se escribe es exactamente lo que dice `NEWLINE` en cualquier SO. La última
    # línea también termina en salto: un archivo de texto sin salto final es el clásico
    # que le come la última entrada a algún parser.
    output.write_text(NEWLINE.join(lines) + NEWLINE, encoding="utf-8", newline="")
    return output
