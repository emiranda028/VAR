"""Evalúa el modelo sobre las jugadas reales.

Uso: python -m vargento.evaluar [--db RUTA] [--json]

Variantes:
- solo_demo: cada jugada real se predice con el histórico sin ninguna jugada real.
- leave_one_out: cada jugada real se predice con todo el histórico menos ella misma.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .db import Database
from .domain import get_play_type
from .model import HistoricalPlay, predict

RECORTE = 1e-3  # la probabilidad se recorta a [RECORTE, 1 - RECORTE] para el log-loss
VARIANTES = ("solo_demo", "leave_one_out")
NOMBRES = {"solo_demo": "Sólo demo", "leave_one_out": "Leave-one-out"}


def base_por_defecto(carpeta: Path | None) -> Database:
    """Base en memoria con el seed de demo más los lotes aprobados de `carpeta`."""
    from .api import cargar_aprobadas
    from .seed import seed

    db = Database(":memory:")
    seed(db)
    cargar_aprobadas(db, carpeta)
    return db


def _metricas(resultados: list[tuple[float, bool, bool]]) -> dict:
    """Resultados: (probabilidad recortada, cobrada, dudosa)."""
    n = len(resultados)
    aciertos = sum((q >= 0.5) == called for q, called, _ in resultados)
    perdida = sum(-math.log(q) if called else -math.log(1 - q) for q, called, _ in resultados)
    dudosas = sum(dudosa for _, _, dudosa in resultados)
    return {
        "n": n,
        "aciertos": aciertos,
        "acierto": aciertos / n if n else 0.0,
        "log_loss": perdida / n if n else 0.0,
        "dudosas": dudosas,
        "tasa_dudosa": dudosas / n if n else 0.0,
    }


def _evaluar_variante(reales: list[HistoricalPlay], historial_de) -> dict:
    por_tipo: dict[str, list[tuple[float, bool, bool]]] = {}
    for p in reales:
        pred = predict(get_play_type(p.play_type), p.features, p.referee_id, historial_de(p))
        q = min(max(pred.probability, RECORTE), 1 - RECORTE)
        por_tipo.setdefault(p.play_type, []).append((q, p.called, pred.recommendation == "DUDOSA"))
    todos = [r for lista in por_tipo.values() for r in lista]
    return {
        "total": _metricas(todos),
        "por_tipo": {tipo: _metricas(por_tipo[tipo]) for tipo in sorted(por_tipo)},
    }


def evaluar(db: Database) -> dict:
    """Métricas del modelo sobre las jugadas reales de `db`, en las dos variantes."""
    h = db.history()
    ids_reales = db.ids_por_origen("real")
    reales = [p for p in h if p.id in ids_reales]
    sin_reales = [p for p in h if p.id not in ids_reales]
    return {
        "n_reales": len(reales),
        "variantes": {
            "solo_demo": _evaluar_variante(reales, lambda p: sin_reales),
            "leave_one_out": _evaluar_variante(reales, lambda p: [x for x in h if x.id != p.id]),
        },
    }


def _coma(texto: str) -> str:
    """Decimales con coma, como se escribe en español."""
    return texto.replace(".", ",")


def _fila(etiqueta: str, m: dict) -> str:
    acierto = _coma(f"{m['acierto']:6.1%}")
    log_loss = _coma(f"{m['log_loss']:8.3f}")
    tasa_dudosa = _coma(f"{m['tasa_dudosa']:6.1%}")
    return (
        f"  {etiqueta:<10} {m['n']:>3}  {m['aciertos']:>3}/{m['n']:<3} ({acierto})"
        f"  {log_loss}  {m['dudosas']:>3}/{m['n']:<3} ({tasa_dudosa})"
    )


def formatear(resultado: dict) -> str:
    """Tabla legible en español."""
    lineas = [f"Jugadas reales evaluadas: {resultado['n_reales']}"]
    for clave in VARIANTES:
        v = resultado["variantes"][clave]
        lineas += [
            "",
            f"{NOMBRES[clave]}:",
            f"  {'Tipo':<10} {'N':>3}  {'Aciertos':<16}  {'Log-loss':>8}  {'DUDOSA':<16}",
            _fila("TOTAL", v["total"]),
        ]
        lineas += [_fila(tipo, m) for tipo, m in v["por_tipo"].items()]
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evalúa el modelo sobre las jugadas reales")
    parser.add_argument("--db", help="base SQLite existente a evaluar (no se modifica)")
    parser.add_argument("--json", action="store_true", help="salida en JSON")
    args = parser.parse_args(argv)

    if args.db is not None:
        if not Path(args.db).is_file():
            print(f"No existe la base {args.db}", file=sys.stderr)
            return 2
        db = Database(args.db)
    else:
        from .api import APROBADAS_DIR

        db = base_por_defecto(APROBADAS_DIR)
    try:
        resultado = evaluar(db)
    finally:
        db.close()

    if resultado["n_reales"] == 0:
        print("No hay jugadas reales para evaluar", file=sys.stderr)
        return 1
    print(json.dumps(resultado, ensure_ascii=False) if args.json else formatear(resultado))
    return 0


if __name__ == "__main__":
    sys.exit(main())
