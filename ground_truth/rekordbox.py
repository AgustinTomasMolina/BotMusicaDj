"""Parser del XML de Rekordbox → CSV (ground truth — tarea F0 #2.5).

Rekordbox exporta la colección como XML (File → Export Collection in xml format).
Este módulo lo lee y saca, por track: ruta, BPM, tonalidad (→ Camelot), género,
duración y cue points. El BPM/tonalidad de Rekordbox es el *baseline* contra el que se
mide el motor (la spec avisa: Rekordbox se equivoca bastante en tonalidad).

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


def a_camelot(tonality: str) -> str:
    """Tonalidad de Rekordbox → Camelot. Si ya viene en Camelot (ej '8A'), la deja."""
    t = (tonality or "").strip()
    if not t:
        return ""
    if re.fullmatch(r"\d{1,2}[AB]", t):
        return t
    return _CAMELOT.get(t, "")


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
            "genre": tr.get("Genre", ""),
            "duration_s": int(tr.get("TotalTime") or 0),
            "kind": tr.get("Kind", ""),
            "num_cues": len(cues),
            "location": _ruta_local(tr.get("Location", "")),
            "cues": cues,
        })
    return tracks


_COLS = ["track_id", "artist", "name", "bpm", "tonality", "camelot",
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

    con_bpm = sum(1 for t in tracks if t["bpm"] > 0)
    con_key = sum(1 for t in tracks if t["camelot"])
    con_key_raw = sum(1 for t in tracks if t["tonality"])
    con_cues = sum(1 for t in tracks if t["num_cues"] > 0)
    total_cues = sum(t["num_cues"] for t in tracks)

    print(f"✅ {len(tracks)} tracks parseados desde {args.xml.name}")
    print(f"   con BPM (>0):        {con_bpm}")
    print(f"   con tonalidad:       {con_key_raw}  (mapeadas a Camelot: {con_key})")
    print(f"   con cue points:      {con_cues} tracks · {total_cues} cues en total")
    print(f"   → {tcsv}")
    print(f"   → {ccsv}")
    if con_key_raw and not con_key:
        print("   ⚠️  Hay tonalidades que no supe mapear a Camelot — revisá el formato en rekordbox_tracks.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
