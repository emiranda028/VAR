"""Importación de jugadas reales desde CSV.

Formato: una fila por jugada revisada, con estas columnas (el orden no importa,
separador `,` o `;`):

- `tipo`: penal, mano, offside o roja (también acepta el nombre largo).
- `arbitro`: nombre del árbitro. Si no existe, se crea.
- `pais_arbitro` (opcional): país, sólo se usa al crear un árbitro nuevo.
- `cobrado`: decisión final tras el VAR (si/no).
- `fecha` (AAAA-MM-DD), `partido`, `descripcion`: opcionales.
- `video` (opcional): enlace al video o foto (YouTube, Vimeo, .mp4, .jpg...).
- `video_inicio` (opcional): segundo donde empieza la jugada (o `1m30s`).
- Una columna por atributo del tipo de jugada (ver `/api/play-types`). Las
  columnas de atributos de otros tipos se dejan vacías.

La importación es todo o nada: si alguna fila tiene errores no se guarda
ninguna, y se informan todos los errores juntos.

Uso por consola:
    python -m vargento.importer jugadas.csv [--db vargento.db] [--probar]
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import unicodedata

from .db import Database
from .domain import PLAY_TYPES, PlayType, validate_features
from .media import start_from_url, validate_url

BASE_COLUMNS = ["tipo", "arbitro", "pais_arbitro", "cobrado", "fecha", "partido", "descripcion", "video", "video_inicio"]
TRUE = {"si", "s", "1", "true", "verdadero", "x", "yes", "y"}
FALSE = {"no", "n", "0", "false", "falso", ""}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ROWS = 50_000


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return text.strip().lower()


def _bool(value: str, column: str) -> bool:
    v = _norm(value)
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    raise ValueError(f"{column}: valor '{value}' no es sí/no")


def feature_columns() -> list[str]:
    cols: list[str] = []
    for pt in PLAY_TYPES.values():
        for f in pt.features:
            if f.key not in cols:
                cols.append(f.key)
    return cols


def _play_type(value: str) -> PlayType:
    v = _norm(value)
    for pt in PLAY_TYPES.values():
        if v in (pt.key, _norm(pt.label)):
            return pt
    raise ValueError(f"tipo '{value}' desconocido (usar: {', '.join(PLAY_TYPES)})")


def _features(pt: PlayType, row: dict[str, str]) -> dict[str, float]:
    raw: dict[str, float] = {}
    for f in pt.features:
        cell = (row.get(f.key) or "").strip()
        if f.kind == "bool":
            raw[f.key] = float(_bool(cell, f.key))
        else:
            if not cell:
                raise ValueError(f"{f.key}: falta el valor")
            try:
                raw[f.key] = float(cell.replace(",", "."))
            except ValueError:
                raise ValueError(f"{f.key}: '{cell}' no es un número") from None
    return validate_features(pt, raw)


def _reader(text: str) -> csv.DictReader:
    text = text.lstrip("﻿")  # BOM de Excel
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    reader.fieldnames = [_norm(c) for c in (reader.fieldnames or [])]
    return reader


def import_csv(db: Database, text: str, dry_run: bool = False, create_referees: bool = True) -> dict:
    reader = _reader(text)
    missing = [c for c in ("tipo", "arbitro", "cobrado") if c not in reader.fieldnames]
    if missing:
        return {"imported": 0, "valid": 0, "errors": [{"row": 1, "error": f"Faltan columnas: {', '.join(missing)}"}],
                "new_referees": []}

    known = {_norm(r["name"]): r["id"] for r in db.list_referees()}
    new_refs: dict[str, tuple[str, str]] = {}
    plays, errors = [], []
    for line, row in enumerate(reader, start=2):
        if line - 1 > MAX_ROWS:
            errors.append({"row": line, "error": f"Máximo {MAX_ROWS} filas por importación"})
            break
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue  # fila vacía
        try:
            pt = _play_type(row.get("tipo", ""))
            name = (row.get("arbitro") or "").strip()
            if not name:
                raise ValueError("arbitro: falta el nombre")
            key = _norm(name)
            if key in known:
                ref = known[key]
            elif create_referees:
                new_refs.setdefault(key, (name, (row.get("pais_arbitro") or "").strip()))
                ref = new_refs[key][0]
            else:
                raise ValueError(f"arbitro '{name}' no existe")
            if not (row.get("cobrado") or "").strip():
                raise ValueError("cobrado: falta el valor")
            called = _bool(row["cobrado"], "cobrado")
            date = (row.get("fecha") or "").strip()
            if date and not DATE_RE.match(date):
                raise ValueError(f"fecha: '{date}' no tiene formato AAAA-MM-DD")
            features = _features(pt, row)
            link = None
            url = (row.get("video") or "").strip()
            if url:
                url = validate_url(url)
                start_cell = (row.get("video_inicio") or "").strip()
                start = start_from_url(url)
                if start_cell:
                    start = float(start_cell) if start_cell.replace(".", "", 1).isdigit() else start_from_url(f"?t={start_cell}")
                link = (url, start)
            plays.append((
                pt.key, ref, features, called,
                (row.get("descripcion") or "").strip(), (row.get("partido") or "").strip(), date, link,
            ))
        except ValueError as exc:
            errors.append({"row": line, "error": str(exc)})

    result = {
        "valid": len(plays),
        "imported": 0,
        "errors": errors[:200],
        "error_count": len(errors),
        "new_referees": [n for n, _ in new_refs.values()],
    }
    if errors or dry_run or not plays:
        return result
    db.add_plays(plays, list(new_refs.values()))
    result["imported"] = len(plays)
    return result


def template_csv() -> str:
    cols = BASE_COLUMNS + feature_columns()
    examples = [
        {"tipo": "offside", "arbitro": "Nombre Apellido", "pais_arbitro": "Argentina", "cobrado": "si",
         "fecha": "2025-03-09", "partido": "Local vs Visitante", "descripcion": "Delantero adelantado en el gol",
         "video": "https://www.youtube.com/watch?v=ID_DEL_VIDEO", "video_inicio": "1m05s",
         "decision_campo": "no", "distancia_cm": "7", "interfiere_juego": "si", "jugada_deliberada_rival": "no"},
        {"tipo": "penal", "arbitro": "Nombre Apellido", "cobrado": "no", "fecha": "2025-03-09",
         "partido": "Local vs Visitante", "descripcion": "Contacto leve en el área",
         "decision_campo": "si", "intensidad_contacto": "1", "toca_pelota_primero": "si",
         "intento_jugar_balon": "si", "indicios_simulacion": "no", "contacto_dentro_area": "si"},
    ]
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for e in examples:
        w.writerow(e)
    return out.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa jugadas desde un CSV a VARgento")
    parser.add_argument("archivo", nargs="?", help="CSV a importar")
    parser.add_argument("--db", default="vargento.db")
    parser.add_argument("--probar", action="store_true", help="sólo validar, sin guardar")
    parser.add_argument("--plantilla", action="store_true", help="imprimir un CSV de ejemplo")
    args = parser.parse_args()
    if args.plantilla or not args.archivo:
        print(template_csv(), end="")
        return
    with open(args.archivo, encoding="utf-8-sig") as fh:
        result = import_csv(Database(args.db), fh.read(), dry_run=args.probar)
    for e in result["errors"]:
        print(f"Fila {e['row']}: {e['error']}")
    if result["errors"]:
        print(f"{result['error_count']} errores: no se importó nada.")
    elif args.probar:
        print(f"OK: {result['valid']} jugadas válidas (no se guardaron).")
    else:
        print(f"Importadas {result['imported']} jugadas. Árbitros nuevos: {len(result['new_referees'])}.")


if __name__ == "__main__":
    main()
