"""tagger embebe solo JPEG/PNG/GIF/WebP según los BYTES (auditoría de f28, A1).

Era el vector del XSS: `_descargar_cover` aceptaba cualquier Content-Type "image/*" (un
SVG con <script>) y lo embebía en el archivo; después /api/cover lo servía en el origen de
la app. Ahora el tipo sale del contenido y lo demás no se embebe.
"""
import sys
import types

import tagger

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _fuente(monkeypatch, contenido: bytes, content_type: str):
    class R:
        headers = {"Content-Type": content_type}
        content = contenido

        def raise_for_status(self):
            pass
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda url, timeout=15: R()))


def test_mime_por_contenido_solo_los_cuatro_formatos():
    assert tagger.mime_por_contenido(PNG) == "image/png"
    assert tagger.mime_por_contenido(JPEG) == "image/jpeg"
    assert tagger.mime_por_contenido(b"GIF89a" + b"\x00" * 10) == "image/gif"
    assert tagger.mime_por_contenido(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"
    for otro in (SVG, b"", b"RIFF\x10\x00\x00\x00WAVEfmt ", b"GIF8"):
        assert tagger.mime_por_contenido(otro) is None, otro[:12]


def test_svg_de_la_fuente_no_se_embebe_aunque_diga_image(monkeypatch):
    _fuente(monkeypatch, SVG, "image/svg+xml")
    assert tagger._descargar_cover("https://x/tapa.svg") == (None, None)
    _fuente(monkeypatch, SVG, "image/jpeg")          # el Content-Type miente
    assert tagger._descargar_cover("https://x/tapa.jpg") == (None, None)


def test_el_tipo_sale_del_contenido_no_del_content_type(monkeypatch):
    _fuente(monkeypatch, PNG, "image/jpeg")
    assert tagger._descargar_cover("https://x/tapa") == (PNG, "image/png")
    _fuente(monkeypatch, JPEG, "application/octet-stream")
    assert tagger._descargar_cover("https://x/tapa") == (JPEG, "image/jpeg")
