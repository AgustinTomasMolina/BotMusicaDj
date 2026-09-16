"""Runner del benchmark: chequea las métricas del motor contra los umbrales (spec §4).

Uso:
    python -m benchmark                      # muestra el contrato (sin métricas → exit 2)
    python -m benchmark --metricas corr.json # evalúa y devuelve exit 0/1/2
    python -m benchmark --metricas corr.json --guardar   # además guarda la corrida

Exit code: 0 = todo cumple · 1 = se rompió un umbral · 2 = faltan métricas por medir.
Los umbrales "a calibrar" (límite todavía sin número, ver benchmark/umbrales.py) se
imprimen con su valor y la marca [~], pero no cuentan para el exit code.
Una métrica en nan (p.ej. el Spearman de una curva flat) cuenta como sin medir, no como rota.

El JSON puede ser plano ({clave: valor}) o la forma de `benchmark.evaluar.metricas()`
({"umbrales": ..., "extra": ...}). La cobertura del subconjunto tonal (`cobertura_unanimes`,
`n_key_unanimes`, `n_key`) se imprime al lado de las filas de tonalidad; si no viene, dice
"cobertura no informada". No tiene umbral y no cambia el exit code.
El JSON de métricas lo produce el motor corriendo sobre el set de referencia
(ground truth de Rekordbox) — tareas F0 #1 (traer el motor) y #2.5 (ground truth).
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from .umbrales import evaluar, no_definido, veredicto

# Claves de la cobertura del subconjunto tonal, con los mismos nombres que
# `benchmark.evaluar.metricas()["extra"]`.
CLAVES_COBERTURA = ("cobertura_unanimes", "n_key_unanimes", "n_key")


def separar(doc: dict) -> tuple[dict, dict]:
    """(métricas de umbrales, cobertura tonal) a partir del JSON de métricas.

    Acepta las dos formas: plana ({clave: valor}, con la cobertura como claves sueltas) o la
    que devuelve `benchmark.evaluar.metricas()` ({"umbrales": {...}, "extra": {...}})."""
    if isinstance(doc.get("umbrales"), dict):
        umbrales, extra = doc["umbrales"], doc.get("extra") or {}
    else:
        umbrales, extra = doc, doc
    return umbrales, {k: extra[k] for k in CLAVES_COBERTURA if extra.get(k) is not None}


def texto_cobertura(cobertura: dict) -> str:
    """La cobertura del subconjunto unánime, o que no vino. Nunca se calla: un contrato sobre
    un subconjunto se "cumple" achicando el subconjunto (spec §4, cambio 2026-09-14)."""
    pct = cobertura.get("cobertura_unanimes")
    if pct is None:
        return "cobertura no informada"
    n, total = cobertura.get("n_key_unanimes"), cobertura.get("n_key")
    cuantos = f"{n}/{total} " if n is not None and total is not None else ""
    return f"cobertura {cuantos}({float(pct):.1f}%)"


def _valor(f) -> str:
    if f.valor is None:
        return "— sin medir"
    if no_definido(f.valor):
        return "no definido (nan) — sin medir"
    u = f" {f.umbral.unidad}" if f.umbral.unidad else ""
    return f"{f.valor}{u}"


def _marca(f) -> str:
    if f.umbral.a_calibrar:
        return "~"
    if f.ok is None:
        return "·"
    return "✓" if f.ok else "✗"


def _imprimir(filas, cobertura: dict | None = None) -> None:
    """La tabla del contrato. Marcas: ✓ cumple · ✗ rota · · sin medir · ~ a calibrar.

    Una fila '~' (umbral a calibrar, ver `benchmark/umbrales.py`) muestra su valor medido
    pero no entra al veredicto: no rompe ni cuenta como 'falta medir'. Las filas de
    tonalidad con valor llevan al lado la cobertura del subconjunto unánime, o "cobertura no
    informada" si el JSON no la trae. La cobertura no tiene umbral y no toca el exit code."""
    print("Umbrales de calidad del motor (spec §4) — el contrato\n")
    for f in filas:
        marca = _marca(f)
        linea = (f"  [{marca}] {f.umbral.nombre:<34s} objetivo {f.umbral.objetivo():<22s}  "
                 f"medido: {_valor(f)}")
        if f.umbral.clave.startswith("tonalidad_") and f.valor is not None:
            linea += f"  · {texto_cobertura(cobertura or {})}"
        print(linea)
    if any(f.umbral.a_calibrar for f in filas):
        print("\n  [~] = umbral a calibrar: se reporta el valor, no decide el veredicto.")


def mensaje_final(filas, code: int) -> str:
    """La frase del veredicto. Con umbrales a calibrar, "todos cumplen" sería mentira: esas
    filas no tienen veredicto, así que se dice cuáles quedaron afuera."""
    if code == 0:
        a_calibrar = [f.umbral.nombre for f in filas if f.umbral.a_calibrar]
        if a_calibrar:
            return (f"\n✅ Los umbrales con número cumplen ({len(a_calibrar)} a calibrar: "
                    f"{', '.join(a_calibrar)}).")
        return "\n✅ Todos los umbrales cumplen."
    return {1: "\n❌ Se rompió al menos un umbral — este cambio NO entra.",
            2: "\n⚠️  Faltan métricas por medir."}[code]


def main(argv=None) -> int:
    try:  # la consola de Windows (cp1252) no imprime ✓/±/§ — forzamos UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="benchmark",
                                 description="Chequea las métricas del motor contra los umbrales de la spec §4.")
    ap.add_argument("--metricas", type=Path, help="JSON con las métricas medidas por el motor.")
    ap.add_argument("--guardar", action="store_true", help="Guardar la corrida en benchmark/corridas/.")
    args = ap.parse_args(argv)

    if not args.metricas:
        _imprimir(evaluar({}))
        print("\n⚠️  Todavía no hay métricas: falta el motor + el ground truth de Rekordbox")
        print("    (tareas F0 #1 y #2.5). Cuando existan:  python -m benchmark --metricas corrida.json")
        return 2

    metricas = json.loads(args.metricas.read_text(encoding="utf-8"))
    valores, cobertura = separar(metricas)
    filas = evaluar(valores)
    _imprimir(filas, cobertura)
    code = veredicto(filas)
    print(mensaje_final(filas, code))

    if args.guardar:
        carpeta = Path(__file__).resolve().parent / "corridas"
        carpeta.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        (carpeta / f"{stamp}.json").write_text(
            json.dumps({"fecha": stamp, "metricas": metricas, "exit_code": code}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"    corrida guardada en benchmark/corridas/{stamp}.json")

    return code


if __name__ == "__main__":
    sys.exit(main())
