"""Importa jugadas reales desde un archivo JSONL (formato del agente scout-datos).

Uso:
    python -m vargento.importar data/candidatas/lote.jsonl            # sólo simula
    python -m vargento.importar data/candidatas/lote.jsonl --aplicar  # carga en la base

Cada línea es un objeto JSON con: play_type, referee_name, called, features y,
opcionalmente, match, date, description, fuentes, inferidos y confianza.
Por defecto sólo simula: valida todo e informa qué cargaría, sin tocar la base.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .domain import get_play_type, validate_features


@dataclass
class Jugada:
    linea: int
    play_type: str
    referee_name: str
    features: dict[str, float]
    called: bool
    description: str
    match: str
    date: str


@dataclass
class Resultado:
    validas: list[Jugada] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    duplicadas: list[str] = field(default_factory=list)
    baja_confianza: list[str] = field(default_factory=list)
    arbitros_nuevos: set[str] = field(default_factory=set)
    cargadas: int = 0


def _descripcion(obj: dict) -> str:
    """Descripción con las fuentes al final, para no perder la trazabilidad."""
    texto = str(obj.get("description", "")).strip()
    fuentes = [str(f).strip() for f in obj.get("fuentes") or [] if str(f).strip()]
    if fuentes:
        texto = f"{texto} | Fuentes: {' '.join(fuentes)}".strip(" |")
    return texto


def _parsear(linea: int, obj: dict) -> Jugada:
    if not isinstance(obj, dict):
        raise ValueError("la línea no es un objeto JSON")
    pt = get_play_type(str(obj.get("play_type", "")))
    nombre = str(obj.get("referee_name", "")).strip()
    if not nombre:
        raise ValueError("falta referee_name")
    if not isinstance(obj.get("called"), bool):
        raise ValueError("called tiene que ser true o false")
    if not obj.get("fuentes"):
        raise ValueError("falta al menos una fuente (sin fuente no hay jugada)")
    features = validate_features(pt, obj.get("features") or {})
    return Jugada(
        linea=linea,
        play_type=pt.key,
        referee_name=nombre,
        features=features,
        called=obj["called"],
        description=_descripcion(obj),
        match=str(obj.get("match", "")).strip(),
        date=str(obj.get("date", "")).strip(),
    )


def importar(
    db: Database, lineas: list[str], aplicar: bool = False, incluir_baja: bool = False
) -> Resultado:
    """Valida las líneas y, si `aplicar`, carga las jugadas válidas (todas juntas en una transacción)."""
    res = Resultado()
    vistas: set[tuple] = set()
    for n, texto in enumerate(lineas, start=1):
        if not texto.strip():
            continue
        try:
            obj = json.loads(texto)
            jugada = _parsear(n, obj)
        except (ValueError, KeyError, TypeError) as exc:
            res.errores.append(f"línea {n}: {exc}")
            continue
        if obj.get("confianza") == "baja" and not incluir_baja:
            res.baja_confianza.append(f"línea {n}: {jugada.match or jugada.description[:40]}")
            continue
        clave = (jugada.play_type, jugada.match, jugada.date, jugada.description)
        if clave in vistas or db.play_exists(*clave):
            res.duplicadas.append(f"línea {n}: {jugada.match} {jugada.date}")
            continue
        vistas.add(clave)
        if db.get_referee_by_name(jugada.referee_name) is None:
            res.arbitros_nuevos.add(jugada.referee_name)
        res.validas.append(jugada)

    if aplicar and res.validas:
        res.cargadas = _cargar(db, res.validas)
    return res


def _cargar(db: Database, jugadas: list[Jugada]) -> int:
    ids: dict[str, int] = {}
    for j in jugadas:
        clave = j.referee_name.lower()
        if clave not in ids:
            ref = db.get_referee_by_name(j.referee_name) or db.add_referee(j.referee_name)
            ids[clave] = ref["id"]
    db.add_plays(
        [
            (j.play_type, ids[j.referee_name.lower()], j.features, j.called, j.description, j.match, j.date)
            for j in jugadas
        ]
    )
    return len(jugadas)


def _informe(res: Resultado, aplicar: bool) -> str:
    partes = [
        f"Válidas: {len(res.validas)}",
        f"Errores: {len(res.errores)}",
        f"Duplicadas: {len(res.duplicadas)}",
        f"Baja confianza (omitidas): {len(res.baja_confianza)}",
        f"Árbitros nuevos: {len(res.arbitros_nuevos)}"
        + (f" ({', '.join(sorted(res.arbitros_nuevos))})" if res.arbitros_nuevos else ""),
    ]
    for titulo, items in (("Errores", res.errores), ("Duplicadas", res.duplicadas),
                          ("Baja confianza", res.baja_confianza)):
        if items:
            partes.append(f"\n{titulo}:")
            partes.extend(f"  - {i}" for i in items)
    if aplicar:
        partes.append(f"\nCargadas en la base: {res.cargadas}")
    else:
        partes.append("\nSimulación: no se cargó nada. Usá --aplicar para cargar.")
    return "\n".join(partes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importar jugadas reales a VARgento")
    parser.add_argument("archivo", type=Path, help="archivo .jsonl con las jugadas")
    parser.add_argument("--db", default=os.environ.get("VARGENTO_DB", "vargento.db"))
    parser.add_argument("--aplicar", action="store_true", help="cargar en la base (si no, sólo simula)")
    parser.add_argument("--incluir-baja", action="store_true", help="incluir jugadas de confianza baja")
    args = parser.parse_args(argv)

    lineas = args.archivo.read_text(encoding="utf-8").splitlines()
    db = Database(args.db)
    try:
        res = importar(db, lineas, aplicar=args.aplicar, incluir_baja=args.incluir_baja)
    finally:
        db.close()
    print(_informe(res, args.aplicar))
    return 1 if res.errores else 0


if __name__ == "__main__":
    sys.exit(main())
