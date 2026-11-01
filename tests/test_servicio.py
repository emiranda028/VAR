import ast
from pathlib import Path

import pytest

from vargento import servicio
from vargento.servicio import historia_desde_json, respuesta_prediccion

OFFSIDE = {"decision_campo": 0.0, "distancia_cm": 10.0, "interfiere_juego": 1.0, "jugada_deliberada_rival": 0.0}
ARBITROS = [{"id": 1, "name": "Árbitro Uno", "country": "Argentina", "plays": 2}]
HISTORIA = historia_desde_json(
    [
        {"id": 1, "play_type": "offside", "referee_id": 1, "features": OFFSIDE, "called": True,
         "description": "d", "match": "A-B", "date": "2025-01-01"},
        {"id": 2, "play_type": "penal", "referee_id": 1, "features": {}, "called": False},
    ]
)


PROHIBIDOS = {"fastapi", "pydantic", "sqlite3", ".db", "vargento.db"}


def _modulos_importados(codigo: str) -> set[str]:
    """Módulos importados; en `from X import y` incluye también `X.y` (cubre `from . import db`)."""
    modulos = set()
    for nodo in ast.walk(ast.parse(codigo)):
        if isinstance(nodo, ast.Import):
            modulos |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            base = ("." * nodo.level) + (nodo.module or "")
            modulos.add(base)
            sep = "" if base.endswith(".") else "."
            modulos |= {base + sep + a.name for a in nodo.names}
    return modulos


def _prohibidos(codigo: str) -> set[str]:
    return {m for m in _modulos_importados(codigo) if m.split(".")[0] in PROHIBIDOS or m in PROHIBIDOS}


def test_no_importa_web_ni_base():
    assert not _prohibidos(Path(servicio.__file__).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "linea", ["from . import db", "from .db import Database", "from vargento import db", "import sqlite3", "from fastapi import FastAPI"]
)
def test_detector_de_imports_prohibidos(linea):
    codigo = Path(servicio.__file__).read_text(encoding="utf-8") + "\n" + linea + "\n"
    assert _prohibidos(codigo)


def test_historia_desde_json_arma_jugadas():
    assert HISTORIA[0].features == OFFSIDE and HISTORIA[0].called is True
    assert HISTORIA[0].match == "A-B"
    assert HISTORIA[1].description == ""


def test_respuesta_con_arbitro_sin_plays_y_filtra_tipo():
    r = respuesta_prediccion("offside", OFFSIDE, 1, HISTORIA, ARBITROS)
    assert r["referee"] == {"id": 1, "name": "Árbitro Uno", "country": "Argentina"}
    assert [p["id"] for p in r["similar_plays"]] == [1]
    assert r["similar_plays"][0]["referee"] == "Árbitro Uno"


def test_tipo_desconocido_da_value_error():
    with pytest.raises(ValueError):
        respuesta_prediccion("xx", {}, None, HISTORIA, ARBITROS)


def test_atributo_fuera_de_rango_da_value_error():
    with pytest.raises(ValueError):
        respuesta_prediccion("offside", {**OFFSIDE, "distancia_cm": 500}, None, HISTORIA, ARBITROS)


def test_arbitro_inexistente_da_lookup_error():
    with pytest.raises(LookupError):
        respuesta_prediccion("offside", OFFSIDE, 9999, HISTORIA, ARBITROS)
