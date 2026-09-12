"""Detector de calidad real de audio (estilo FakinTheFunk/Spek) — versión honesta.

El detector VIEJO solo preguntaba "¿hay energía sobre X kHz?" y con eso se
dejaba engañar por el 'humito' de Opus (YouTube), gritando "lossless" sobre
un rip de 128-160k. Este mira la FORMA del corte, que es lo que delata al codec:

- Lossy con corte duro (MP3/AAC) → deja un MURO (brickwall): la energía cae
  30-60 dB en menos de ~1.5 kHz en una frecuencia fija. Ese muro = bitrate real.
      MP3 128k ≈ 16 kHz · 192k ≈ 18-19 · 256k ≈ 19-20 · 320k ≈ 20-20.5 kHz
- Opus / AAC alto / lossless → caída en RAMPA suave, sin muro. Por espectro
  NO se puede confirmar lossless (hay que mirar el bitrate del metadato).

Pensado para DJ: 🟢 solo si hay contenido real llegando a ~20 kHz SIN muro por
debajo. Un WAV que en realidad es un rip Opus de YouTube da 🟡 con la leyenda
honesta, no un 🟢 mentiroso.
"""
import os
import re
import subprocess
from pathlib import Path
from shutil import which

import numpy as np


def _resolver_bin(nombre: str, env_bin: str, env_dir: str) -> str:
    """Ubica ffmpeg/ffprobe SIN rutas hardcodeadas de una máquina (#5.16). Orden:
    1) binario explícito por entorno (FFMPEG_BIN / FFPROBE_BIN),
    2) el PATH (which),
    3) un directorio por entorno (FFMPEG_DIR, p. ej. la carpeta de winget),
    4) el nombre pelado (que falle claro al invocarlo si no está)."""
    exe = os.environ.get(env_bin)
    if exe and Path(exe).exists():
        return exe
    hit = which(nombre)
    if hit:
        return hit
    d = os.environ.get(env_dir)
    if d:
        for cand in (Path(d) / f"{nombre}.exe", Path(d) / nombre):
            if cand.exists():
                return str(cand)
    return nombre


FFMPEG = _resolver_bin("ffmpeg", "FFMPEG_BIN", "FFMPEG_DIR")
FFPROBE = _resolver_bin("ffprobe", "FFPROBE_BIN", "FFMPEG_DIR")

# Ventana de análisis del tema (segundos)
SS, DUR = 45, 30
# FFT
_NFFT = 8192
# Frecuencias de referencia para la tabla de bandas (Hz)
BANDAS_REF = [14000, 16000, 18000, 19000, 20000, 21000]
# Umbral de "muro": caída (dB) en la transición para llamarlo corte duro (lossy)
MURO_DB = 25.0
# Umbral de contenido REAL, relativo al pico (0 dB). Por encima de esto es señal
# musical; por debajo es el 'humito' de Opus / ruido de codec. Clave anti-mentira.
UMBRAL_PICO = -68.0
# No buscamos corte por encima de esto (ignora artefactos cerca de Nyquist)
CORTE_MAX = 22000


def _sample_rate(archivo: str, ua: str = None) -> int:
    """Sample rate nativo (Hz). NO resampleamos: perderíamos los agudos."""
    try:
        cmd = [FFPROBE, "-v", "error"]
        if ua:
            cmd += ["-user_agent", ua]
        cmd += ["-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                "-of", "csv=p=0", archivo]
        out = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return 44100


def _decodificar(archivo: str, sr: int, ss: float = SS, dur: float = DUR, ua: str = None) -> np.ndarray:
    """Decodifica un segmento mono a float32 al sample rate NATIVO.
    `ua` (User-Agent) es necesario para URLs de scrapers que si no dan 403;
    `ss`/`dur` se ajustan para clips cortos (ej. previews de 30s)."""
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-v", "error"]
    if ua:
        cmd += ["-user_agent", ua]
    cmd += [
        "-ss", str(ss), "-t", str(dur), "-i", archivo,
        "-ac", "1", "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-",
    ]
    out = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(out.stdout, dtype=np.float32)


def _espectro_db(samples: np.ndarray, sr: int):
    """Espectro de potencia promediado (Welch casero), en dB normalizado a pico=0.
    Devuelve (freqs_Hz, db_suavizado)."""
    n = _NFFT
    if samples.size < n:
        samples = np.pad(samples, (0, n - samples.size))
    win = np.hanning(n)
    hop = n // 2
    acc = np.zeros(n // 2 + 1)
    cuenta = 0
    for i in range(0, samples.size - n + 1, hop):
        seg = samples[i:i + n] * win
        mag = np.abs(np.fft.rfft(seg)) ** 2
        acc += mag
        cuenta += 1
    if cuenta == 0:
        seg = samples[:n] * win
        acc = np.abs(np.fft.rfft(seg)) ** 2
        cuenta = 1
    psd = acc / cuenta
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    db = 10 * np.log10(psd + 1e-12)
    db -= db.max()  # normalizar pico a 0 dB
    # Suavizado ~200 Hz para no morder picos/valles finos
    ancho = max(1, int(200 / (sr / n)))
    kernel = np.ones(ancho) / ancho
    db = np.convolve(db, kernel, mode="same")
    return freqs, db


def _db_en(freqs, db, f, tol=250):
    """dB medio en una banda ±tol alrededor de f (o -120 si fuera de rango)."""
    m = (freqs >= f - tol) & (freqs <= f + tol)
    return float(db[m].mean()) if m.any() else -120.0


def _analizar_espectro(freqs, db, sr):
    """Extrae corte, fuerza del muro y pendiente. Devuelve dict de métricas."""
    nyq = sr / 2.0
    piso = float(np.percentile(db[freqs >= min(8000, nyq * 0.6)], 10))

    # Corte = frecuencia más alta con contenido REAL (dentro de UMBRAL_PICO del
    # pico). Relativo al pico, NO al ruido de fondo: así el 'humito' de Opus a
    # -80 dB NO cuenta como banda completa (era la mentira del método viejo).
    tope = min(nyq - 300, CORTE_MAX)
    sobre = np.where((db > UMBRAL_PICO) & (freqs <= tope))[0]
    corte = float(freqs[sobre[-1]]) if sobre.size else float(freqs[0])

    # Muro: caída entre la banda justo debajo del corte y la de arriba
    debajo = _db_en(freqs, db, corte - 900, tol=700)
    arriba_f = min(corte + 900, nyq - 100)
    arriba = _db_en(freqs, db, arriba_f, tol=700)
    muro_db = debajo - arriba

    # Pendiente máxima (dB/kHz) en la zona de agudos 12 kHz→Nyquist
    zona = (freqs >= 12000) & (freqs <= nyq)
    if zona.sum() > 2:
        fz, dz = freqs[zona], db[zona]
        pend = np.diff(dz) / (np.diff(fz) / 1000.0)
        pendiente = float(-pend.min())  # caída más pronunciada, positiva
    else:
        pendiente = 0.0

    return {"corte": corte, "piso": piso, "muro_db": muro_db, "pendiente": pendiente}


def _veredicto(m: dict) -> dict:
    """Traduce las métricas a un badge honesto, con criterio de DJ (≥20 kHz)."""
    khz = m["corte"] / 1000
    es_muro = m["muro_db"] >= MURO_DB

    if m["corte"] >= 20000:
        if es_muro:  # llega a 20k pero con corte duro = 320k lossy (igual apto DJ)
            return {"badge": "🟢",
                    "calidad": f"llega a ~{khz:.1f} kHz con corte duro (lossy ~320k, apto DJ)"}
        return {"badge": "🟢",
                "calidad": f"Banda completa ~{khz:.1f} kHz (natural/lossless, apto DJ)"}
    if es_muro:
        # Corte duro = lossy; el corte da el bitrate real
        if m["corte"] >= 19500:
            cal = "corte duro ~256-320k"
            badge = "🟡"
        elif m["corte"] >= 18000:
            cal = "corte duro ~192k"
            badge = "🟡"
        elif m["corte"] >= 15500:
            cal = "corte duro ~128-160k (típico MP3 bajo)"
            badge = "🔴"
        else:
            cal = "corte duro ≤96k (muy comprimido)"
            badge = "🔴"
        return {"badge": badge, "calidad": f"{cal} @ {khz:.1f} kHz"}
    # Sin muro y no llega a 20k → rampa suave: NO confirmable por espectro
    if m["corte"] >= 16000:
        return {"badge": "🟡",
                "calidad": f"rampa suave hasta ~{khz:.1f} kHz — Opus/AAC probable, "
                           "no confirmable como lossless (mirá el bitrate)"}
    return {"badge": "🔴", "calidad": f"agudos pobres ~{khz:.1f} kHz"}


def analizar(archivo: str, ss: float = SS, dur: float = DUR, ua: str = None) -> dict:
    """Análisis completo. Mantiene las claves badge/calidad/khz que usa server.py.
    `ss`/`dur` acotan la ventana (útil para clips cortos) y `ua` pasa el User-Agent
    a ffmpeg/ffprobe (necesario para URLs de scrapers)."""
    sr = _sample_rate(archivo, ua=ua)
    samples = _decodificar(archivo, sr, ss=ss, dur=dur, ua=ua)
    if samples.size == 0:
        return {"badge": "⚪", "calidad": "sin audio legible", "khz": 0.0,
                "cutoff_hz": 0, "sr": sr, "archivo": Path(archivo).name}
    freqs, db = _espectro_db(samples, sr)
    m = _analizar_espectro(freqs, db, sr)
    res = _veredicto(m)
    res["khz"] = m["corte"] / 1000
    res["cutoff_hz"] = int(m["corte"])
    res["muro_db"] = round(m["muro_db"], 1)
    res["pendiente_db_khz"] = round(m["pendiente"], 1)
    res["sr"] = sr
    res["es_muro"] = m["muro_db"] >= MURO_DB
    res["bandas"] = {f"{f // 1000}k": round(_db_en(freqs, db, f), 1) for f in BANDAS_REF}
    res["archivo"] = Path(archivo).name
    return res


def _imprimir(r: dict) -> None:
    print(f"\n{r['badge']}  {r['archivo']}   ({r.get('sr', '?')} Hz)")
    print(f"    Corte:        {r['khz']:.1f} kHz")
    print(f"    Muro:         {r.get('muro_db', '?')} dB de caída "
          f"({'MURO → lossy' if r.get('es_muro') else 'rampa suave'})")
    print(f"    Veredicto:    {r['calidad']}")
    bandas = r.get("bandas")
    if bandas:
        tabla = "  ".join(f"{k}:{v:.0f}" for k, v in bandas.items())
        print(f"    Bandas (dB bajo pico): {tabla}")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # emojis en consola Windows
    except Exception:
        pass
    args = sys.argv[1:]
    downloads = Path(__file__).parent / "downloads"
    if args and args[0] in ("--batch", "-b"):
        for p in sorted(downloads.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                _imprimir(analizar(str(p)))
    else:
        objetivo = args[0] if args else None
        if not objetivo:
            files = sorted(downloads.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
            objetivo = str(files[0]) if files else None
        if not objetivo:
            print("No hay archivo para analizar.")
            sys.exit(1)
        _imprimir(analizar(objetivo))
