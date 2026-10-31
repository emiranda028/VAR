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


SEPARADOR_FUENTES = " | Fuentes: "
CONFIANZAS = ("alta", "media", "baja")


@dataclass
class Jugada:
    linea: int
    play_type: str
    referee_name: str
    features: dict[str, float]
    called: bool
    texto: str  # descripción tal como vino, sin las fuentes
    fuentes: list[str]
    match: str
    date: str
    confianza: str

    @property
    def description(self) -> str:
        """Descripción con las fuentes al final, para no perder la trazabilidad."""
        return f"{self.texto}{SEPARADOR_FUENTES}{' '.join(self.fuentes)}".strip(" |")


@dataclass
class Resultado:
    validas: list[Jugada] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    duplicadas: list[str] = field(default_factory=list)
    baja_confianza: list[str] = field(default_factory=list)
    arbitros_nuevos: dict[str, str] = field(default_factory=dict)  # clave casefold -> nombre
    cargadas: int = 0


def _texto_sin_fuentes(description: str) -> str:
    return description.split(SEPARADOR_FUENTES.strip(), 1)[0].strip(" |")


def _fuentes(valor) -> list[str]:
    """Normaliza `fuentes` y exige al menos una URL http(s)."""
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        raise ValueError("fuentes tiene que ser una lista de URLs")
    urls = [str(f).strip() for f in valor if str(f).strip()]
    if not any(u.startswith(("http://", "https://")) for u in urls):
        raise ValueError("falta al menos una fuente con URL http(s) (sin fuente no hay jugada)")
    return urls


def _parsear(linea: int, obj: dict) -> Jugada:
    if not isinstance(obj, dict):
        raise ValueError("la línea no es un objeto JSON")
    pt = get_play_type(str(obj.get("play_type", "")))
    nombre = str(obj.get("referee_name") or "").strip()
    if not nombre:
        raise ValueError("falta referee_name")
    if not isinstance(obj.get("called"), bool):
        raise ValueError("called tiene que ser true o false")
    fuentes = _fuentes(obj.get("fuentes"))
    confianza = str(obj.get("confianza") or "media").strip().casefold()
    if confianza not in CONFIANZAS:
        raise ValueError(f"confianza tiene que ser alta, media o baja (vino {obj.get('confianza')!r})")
    features = validate_features(pt, obj.get("features") or {})
    return Jugada(
        linea=linea,
        play_type=pt.key,
        referee_name=nombre,
        features=features,
        called=obj["called"],
        texto=str(obj.get("description") or "").strip(),
        fuentes=fuentes,
        match=str(obj.get("match") or "").strip(),
        date=str(obj.get("date") or "").strip(),
        confianza=confianza,
    )


def importar(
    db: Database, lineas: list[str], aplicar: bool = False, incluir_baja: bool = False
) -> Resultado:
    """Valida las líneas y, si `aplicar`, carga las válidas en una sola transacción.

    Una jugada es duplicada si coincide tipo, partido, fecha y descripción (sin
    contar las fuentes), ya sea con otra línea del archivo o con la base.
    """
    res = Resultado()
    vistas: set[tuple] = set()
    for n, texto in enumerate(lineas, start=1):
        if not texto.strip():
            continue
        try:
            jugada = _parsear(n, json.loads(texto))
        except (ValueError, KeyError, TypeError) as exc:
            res.errores.append(f"línea {n}: {exc}")
            continue
        if jugada.confianza == "baja" and not incluir_baja:
            res.baja_confianza.append(f"línea {n}: {jugada.match or jugada.texto[:40]}")
            continue
        clave = (jugada.play_type, jugada.match, jugada.date, jugada.texto.casefold())
        en_base = {
            _texto_sin_fuentes(d).casefold()
            for d in db.descriptions_for(jugada.play_type, jugada.match, jugada.date)
        }
        if clave in vistas or clave[3] in en_base:
            res.duplicadas.append(f"línea {n}: {jugada.match} {jugada.date} {jugada.texto[:40]}")
            continue
        vistas.add(clave)
        ref_clave = jugada.referee_name.casefold()
        if ref_clave not in res.arbitros_nuevos and db.get_referee_by_name(jugada.referee_name) is None:
            res.arbitros_nuevos[ref_clave] = jugada.referee_name
        res.validas.append(jugada)

    if aplicar and res.validas:
        res.cargadas = db.import_plays(
            [
                (j.play_type, j.referee_name, j.features, j.called, j.description, j.match, j.date)
                for j in res.validas
            ]
        )
    return res


def _informe(res: Resultado, aplicar: bool) -> str:
    partes = [
        f"Válidas: {len(res.validas)}",
        f"Errores: {len(res.errores)}",
        f"Duplicadas: {len(res.duplicadas)}",
        f"Baja confianza (omitidas): {len(res.baja_confianza)}",
        f"Árbitros nuevos: {len(res.arbitros_nuevos)}"
        + (f" ({', '.join(sorted(res.arbitros_nuevos.values()))})" if res.arbitros_nuevos else ""),
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

    try:
        lineas = args.archivo.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        print(f"No se pudo leer {args.archivo}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    # En simulación no se crea una base nueva: si no existe, se valida contra una vacía en memoria.
    ruta = args.db if (args.aplicar or Path(args.db).exists()) else ":memory:"
    db = Database(ruta)
    try:
        res = importar(db, lineas, aplicar=args.aplicar, incluir_baja=args.incluir_baja)
    finally:
        db.close()
    print(_informe(res, args.aplicar))
    return 1 if res.errores else 0


if __name__ == "__main__":
    sys.exit(main())
