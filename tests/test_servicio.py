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


def test_no_importa_web_ni_base():
    arbol = ast.parse(Path(servicio.__file__).read_text(encoding="utf-8"))
    modulos = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            modulos |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            modulos.add(("." * nodo.level) + (nodo.module or ""))
    prohibidos = {"fastapi", "pydantic", "sqlite3", ".db", "vargento.db"}
    assert not {m for m in modulos if m.split(".")[0] in prohibidos or m in prohibidos}


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
