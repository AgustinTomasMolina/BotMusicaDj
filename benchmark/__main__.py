"""Runner del benchmark: chequea las métricas del motor contra los umbrales (spec §4).

Uso:
    python -m benchmark                      # muestra el contrato (sin métricas → exit 2)
    python -m benchmark --metricas corr.json # evalúa y devuelve exit 0/1/2
    python -m benchmark --metricas corr.json --guardar   # además guarda la corrida

Exit code: 0 = todo cumple · 1 = se rompió un umbral · 2 = faltan métricas por medir.
El JSON de métricas lo produce el motor corriendo sobre el set de referencia
(ground truth de Rekordbox) — tareas F0 #1 (traer el motor) y #2.5 (ground truth).
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from .umbrales import evaluar, veredicto


def _valor(f) -> str:
    if f.valor is None:
        return "— sin medir"
    u = f" {f.umbral.unidad}" if f.umbral.unidad else ""
    return f"{f.valor}{u}"


def _imprimir(filas) -> None:
    print("Umbrales de calidad del motor (spec §4) — el contrato\n")
    for f in filas:
        marca = "·" if f.ok is None else ("✓" if f.ok else "✗")
        u = f" {f.umbral.unidad}" if f.umbral.unidad else ""
        objetivo = f"{f.umbral.op} {f.umbral.limite:g}{u}"
        print(f"  [{marca}] {f.umbral.nombre:<34s} objetivo {objetivo:<14s}  medido: {_valor(f)}")


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
    filas = evaluar(metricas)
    _imprimir(filas)
    code = veredicto(filas)
    print({0: "\n✅ Todos los umbrales cumplen.",
           1: "\n❌ Se rompió al menos un umbral — este cambio NO entra.",
           2: "\n⚠️  Faltan métricas por medir."}[code])

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
