"""Los colores de las marcas en pantalla son los de la tabla del motor (f51).

La única fuente es `motor/cue_marks.py` (`color_de_marca`, `COLOR_MEMORY_PANTALLA`): ahí se
decide qué color va al XML de Rekordbox. La pantalla los necesita como variables CSS
(`frontend/src/nocturne.css`: --cue-1..8, --cue-mem, --cue-loop), y este test es lo que
impide que las dos copias se separen: si alguien cambia un color en un lado y no en el otro,
el editor mostraría un color y Rekordbox otro.

Se lee el CSS como texto (no hace falta Node): cada variable tiene que estar definida UNA
vez y con un hex literal, no con `var(...)` — un token del sistema podría cambiar de valor
sin que nadie toque estas líneas.
"""
import re
from pathlib import Path

import pytest

from motor.cue_marks import COLOR_MEMORY_PANTALLA, HOT_CUES, color_hex, color_hex_de_marca

CSS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "nocturne.css"
_VARIABLE = re.compile(r"--cue-(\d+|mem|loop)\s*:\s*([^;]+);")


def _variables_css() -> dict[str, list[str]]:
    """{nombre: [valor, ...]} de cada --cue-N / --cue-mem / --cue-loop DEFINIDA en el CSS."""
    definidas: dict[str, list[str]] = {}
    for nombre, valor in _VARIABLE.findall(CSS.read_text(encoding="utf-8")):
        definidas.setdefault(nombre, []).append(valor.strip().lower())
    return definidas


def _esperado() -> dict[str, str]:
    """Lo que la tabla del motor dice que tiene que valer cada variable."""
    esperado = {str(n + 1): color_hex_de_marca("cue", n) for n in range(HOT_CUES)}
    esperado["loop"] = color_hex_de_marca("loop")
    esperado["mem"] = color_hex(COLOR_MEMORY_PANTALLA)
    return esperado


def test_las_variables_css_son_la_tabla_del_motor():
    definidas = _variables_css()
    assert set(definidas) == set(_esperado()), \
        f"el CSS define {sorted(definidas)} y la tabla tiene {sorted(_esperado())}"
    repetidas = {n: v for n, v in definidas.items() if len(v) != 1}
    assert not repetidas, f"variables definidas más de una vez (¿un tema que las pisa?): {repetidas}"
    assert {n: v[0] for n, v in definidas.items()} == _esperado()


@pytest.mark.parametrize("num", [None, *range(HOT_CUES)])
def test_el_loop_es_naranja_con_o_sin_pad(num):
    """Un hot loop no toma el color de su pad: en pantalla va con la clase `cue-loop`
    (CueEditor), que usa --cue-loop, y la tabla dice lo mismo para cualquier pad."""
    assert color_hex_de_marca("loop", num) == _variables_css()["loop"][0] == "#ff9a2e"
