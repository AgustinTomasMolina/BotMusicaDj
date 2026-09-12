"""Parser del XML de Rekordbox → CSV (ground truth — tarea F0 #2.5).

Rekordbox exporta la colección como XML (File → Export Collection in xml format).
Este módulo lo lee y saca, por track: ruta, BPM, tonalidad (→ Camelot), género,
duración y cue points. El BPM/tonalidad de Rekordbox es el *baseline* contra el que se
mide el motor (la spec avisa: Rekordbox se equivoca bastante en tonalidad).

El campo Tonality viene en NOTACIÓN CLÁSICA (Am, Gm, Fm, Abm, …): confirmado contra
Rekordbox 7.2.16 sobre 193 tracks (Am 31, Gm 31, Fm 23, Abm 19, Bbm 17, F#m 11…). PERO
unos pocos tracks traen un tag preexistente en otro formato (Camelot '4A', o valores
sueltos como '11m'/'8m'), todos con AverageBpm="0.00": Rekordbox nunca los analizó y
copió el tag tal cual. Por eso el formato se detecta POR EL VALOR, no se asume por la
fuente: solo la notación clásica se mapea a Camelot; lo demás → None y se cuenta aparte.

    python -m ground_truth.rekordbox --xml Rekordbox.xml --out ground_truth/out
"""
import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

# Notación clásica de Rekordbox → código Camelot (rueda de mezcla armónica).
# Cubre sostenidos y bemoles (Rekordbox usa una u otra según config).
_CAMELOT = {
    # menores (lado A)
    "Abm": "1A", "G#m": "1A", "Ebm": "2A", "D#m": "2A", "Bbm": "3A", "A#m": "3A",
    "Fm": "4A", "Cm": "5A", "Gm": "6A", "Dm": "7A", "Am": "8A", "Em": "9A",
    "Bm": "10A", "F#m": "11A", "Gbm": "11A", "C#m": "12A", "Dbm": "12A",
    # mayores (lado B)
    "B": "1B", "F#": "2B", "Gb": "2B", "Db": "3B", "C#": "3B", "Ab": "4B", "G#": "4B",
    "Eb": "5B", "D#": "5B", "Bb": "6B", "A#": "6B", "F": "7B", "C": "8B", "G": "9B",
    "D": "10B", "A": "11B", "E": "12B",
}


_RE_CAMELOT = re.compile(r"\d{1,2}[AB]")

# Formatos posibles del campo Tonality, decididos MIRANDO EL VALOR.
CLASICA = "clasica"          # Am, Gm, F#, … → mapeable a Camelot
CAMELOT = "camelot"          # 8A, 4A, … → un tag ajeno; NO es el formato de Rekordbox
DESCONOCIDA = "desconocida"  # 11m, 8m, basura → no se sabe qué es
VACIA = "vacia"              # sin tonalidad


def formato_tonalidad(tonality: str) -> str:
    """Clasifica el formato del valor de Tonality por su forma, no por su origen."""
    t = (tonality or "").strip()
    if not t:
        return VACIA
    if t in _CAMELOT:            # notación clásica reconocida (Am, F#, Abm…)
        return CLASICA
    if _RE_CAMELOT.fullmatch(t):  # '4A', '8A' → Camelot leakeado en un tag ajeno
        return CAMELOT
    return DESCONOCIDA           # '11m', '8m', cualquier otra cosa


def a_camelot(tonality: str) -> str | None:
    """Notación clásica de Rekordbox → Camelot. SOLO mapea la notación clásica (el formato
    confirmado); Camelot leakeado, valores desconocidos o vacío → None. Nunca adivina."""
    t = (tonality or "").strip()
    return _CAMELOT.get(t)      # dict.get → None si no es una clásica reconocida


def _ruta_local(location: str) -> str:
    """'file://localhost/C:/Users/.../x.wav' → 'C:/Users/.../x.wav' (URL-decoded)."""
    if not location:
        return ""
    p = urlparse(location)
    return unquote(p.path).lstrip("/") if p.scheme == "file" else unquote(location)


def parsear(xml_path: Path) -> list[dict]:
    root = ET.parse(xml_path).getroot()
    col = root.find("COLLECTION")
    if col is None:
        raise ValueError("No encontré <COLLECTION> — ¿es un XML de Rekordbox?")
    tracks = []
    for tr in col.findall("TRACK"):
        cues = [{
            "name": pm.get("Name", ""), "type": pm.get("Type", ""),
            "start": float(pm.get("Start") or 0), "num": pm.get("Num", ""),
        } for pm in tr.findall("POSITION_MARK")]
        tracks.append({
            "track_id": tr.get("TrackID", ""),
            "artist": tr.get("Artist", ""),
            "name": tr.get("Name", ""),
            "bpm": float(tr.get("AverageBpm") or 0),
            "tonality": tr.get("Tonality", ""),
            "camelot": a_camelot(tr.get("Tonality", "")),
            "tonalidad_formato": formato_tonalidad(tr.get("Tonality", "")),
            "genre": tr.get("Genre", ""),
            "duration_s": int(tr.get("TotalTime") or 0),
            "kind": tr.get("Kind", ""),
            "num_cues": len(cues),
            "location": _ruta_local(tr.get("Location", "")),
            "cues": cues,
        })
    return tracks


_COLS = ["track_id", "artist", "name", "bpm", "tonality", "camelot", "tonalidad_formato",
         "genre", "duration_s", "kind", "num_cues", "location"]


def escribir_csv(tracks: list[dict], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks_csv = out_dir / "rekordbox_tracks.csv"
    cues_csv = out_dir / "rekordbox_cues.csv"
    with tracks_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_COLS)
        for t in tracks:
            w.writerow([t[c] for c in _COLS])
    with cues_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["track_id", "cue_name", "cue_type", "start_s", "num"])
        for t in tracks:
            for c in t["cues"]:
                w.writerow([t["track_id"], c["name"], c["type"], c["start"], c["num"]])
    return tracks_csv, cues_csv


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="ground_truth.rekordbox",
                                 description="Parsea el XML de Rekordbox a CSV (ground truth).")
    ap.add_argument("--xml", required=True, type=Path, help="XML exportado de Rekordbox.")
    ap.add_argument("--out", type=Path, default=Path("ground_truth/out"), help="Carpeta de salida.")
    args = ap.parse_args(argv)

    tracks = parsear(args.xml)
    tcsv, ccsv = escribir_csv(tracks, args.out)

    from collections import Counter

    con_bpm = sum(1 for t in tracks if t["bpm"] > 0)
    con_key = sum(1 for t in tracks if t["camelot"])
    con_key_raw = sum(1 for t in tracks if t["tonality"])
    con_cues = sum(1 for t in tracks if t["num_cues"] > 0)
    total_cues = sum(t["num_cues"] for t in tracks)
    formatos = Counter(t["tonalidad_formato"] for t in tracks)

    print(f"✅ {len(tracks)} tracks parseados desde {args.xml.name}")
    print(f"   con BPM (>0):        {con_bpm}")
    print(f"   con tonalidad:       {con_key_raw}  (mapeadas a Camelot: {con_key})")
    print(f"   formato de Tonality: clásica {formatos[CLASICA]} · camelot {formatos[CAMELOT]} "
          f"· desconocida {formatos[DESCONOCIDA]} · vacía {formatos[VACIA]}")
    if formatos[CAMELOT] or formatos[DESCONOCIDA]:
        raros = [f"{t['name'][:24]}={t['tonality']!r}" for t in tracks
                 if t["tonalidad_formato"] in (CAMELOT, DESCONOCIDA)]
        print(f"     no-clásicas (tag ajeno, no mapeadas): {', '.join(raros)}")
    print(f"   con cue points:      {con_cues} tracks · {total_cues} cues en total")
    print(f"   → {tcsv}")
    print(f"   → {ccsv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
