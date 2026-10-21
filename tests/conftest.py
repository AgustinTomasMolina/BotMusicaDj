"""Fixtures compartidas de `tests/`.

f64: ningún test puede salir a la red de Deezer o Jamendo por accidente. El registro de fuentes
externas de la app (`fuentes.registro.default()`, que usan `server._fuentes_externas` y
`station_mezcla._registro`) arranca VACÍO en cada test, y sin `JAMENDO_CLIENT_ID` aunque el
`.env` de la PC la tenga. El test que necesita una fuente arma su propio registro con un HTTP
de juguete y lo inyecta.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _sin_fuentes_externas_reales(monkeypatch):
    from fuentes import registro

    monkeypatch.delenv("JAMENDO_CLIENT_ID", raising=False)
    monkeypatch.setattr(registro, "_default", registro.Registry([]))
