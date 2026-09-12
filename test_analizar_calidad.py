"""Tests de analizar_calidad._resolver_bin (#5.16).

Módulo de raíz: la raíz NO está en `testpaths`, así que `pytest -q` no lo colecta;
correr apuntándolo: `pytest test_analizar_calidad.py`.
"""
import analizar_calidad as A


def test_env_bin_explicito_gana(tmp_path, monkeypatch):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_text("x")
    monkeypatch.setenv("FFMPEG_BIN", str(exe))
    monkeypatch.setattr(A, "which", lambda _n: "/otra/cosa")   # aunque PATH tenga otra
    assert A._resolver_bin("ffmpeg", "FFMPEG_BIN", "FFMPEG_DIR") == str(exe)


def test_cae_al_path_si_no_hay_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    monkeypatch.setattr(A, "which", lambda n: f"/usr/bin/{n}")
    assert A._resolver_bin("ffmpeg", "FFMPEG_BIN", "FFMPEG_DIR") == "/usr/bin/ffmpeg"


def test_cae_al_dir_de_entorno(tmp_path, monkeypatch):
    (tmp_path / "ffprobe.exe").write_text("x")
    monkeypatch.delenv("FFPROBE_BIN", raising=False)
    monkeypatch.setenv("FFMPEG_DIR", str(tmp_path))
    monkeypatch.setattr(A, "which", lambda _n: None)           # no está en PATH
    assert A._resolver_bin("ffprobe", "FFPROBE_BIN", "FFMPEG_DIR") == str(tmp_path / "ffprobe.exe")


def test_ultimo_recurso_nombre_pelado(monkeypatch):
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    monkeypatch.setattr(A, "which", lambda _n: None)
    assert A._resolver_bin("ffmpeg", "FFMPEG_BIN", "FFMPEG_DIR") == "ffmpeg"
