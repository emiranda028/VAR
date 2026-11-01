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


# --- jugadas reales similares -------------------------------------------------
ARBITROS_2 = ARBITROS + [{"id": 2, "name": "Árbitro Dos", "country": "", "plays": 0}]
VIDEO = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "inicio": 30}


def _offside(id_: int, distancia: float, origen: str = "real", referee_id: int = 1, **extra) -> dict:
    return {"id": id_, "play_type": "offside", "referee_id": referee_id, "called": id_ % 2 == 0,
            "features": {**OFFSIDE, "distancia_cm": distancia}, "origen": origen, "match": f"P{id_}",
            "date": "2025-02-02", **extra}


def _historia_reales() -> list:
    return historia_desde_json(
        [
            _offside(1, 10.0, origen="demo"),  # idéntica, pero demo: nunca aparece
            _offside(9, 30.0),
            _offside(3, 12.0, referee_id=2),
            _offside(4, 10.0, description="Min 5 | Fuentes: https://a.com/x http://b.com/y", video=VIDEO),
            _offside(5, 12.0),  # empata con la 3: gana el id menor
            _offside(6, -40.0, referee_id=2),
            _offside(7, 20.0),
            {**_offside(8, 10.0), "play_type": "penal", "features": {}},  # otro tipo
        ]
    )


def test_reales_similares_sin_demo_ordenadas_y_maximo_5():
    r = respuesta_prediccion("offside", OFFSIDE, None, _historia_reales(), ARBITROS_2)
    assert [p["id"] for p in r["jugadas_reales_similares"]] == [4, 3, 5, 7, 9]
    assert r["jugadas_reales_arbitro"] == []
    assert r["jugadas_reales_similares"][0]["similitud"] == 1.0
    assert [p["id"] for p in r["similar_plays"]][0] == 1  # similar_plays sigue igual (incluye demo)


def test_reales_del_arbitro():
    r = respuesta_prediccion("offside", OFFSIDE, 2, _historia_reales(), ARBITROS_2)
    assert [p["id"] for p in r["jugadas_reales_arbitro"]] == [3, 6]
    assert {p["referee"] for p in r["jugadas_reales_arbitro"]} == {"Árbitro Dos"}


def test_campos_descripcion_fuentes_y_video():
    r = respuesta_prediccion("offside", OFFSIDE, None, _historia_reales(), ARBITROS_2)
    con_video, sin_video = r["jugadas_reales_similares"][0], r["jugadas_reales_similares"][1]
    assert con_video == {
        "id": 4, "similitud": 1.0, "match": "P4", "date": "2025-02-02", "referee_id": 1,
        "referee": "Árbitro Uno", "decision_campo": False, "called": True, "descripcion": "Min 5",
        "fuentes": ["https://a.com/x", "http://b.com/y"],
        "video": {"id": "dQw4w9WgXcQ", "url": VIDEO["url"], "inicio": 30},
    }
    assert sin_video["video"] is None and sin_video["descripcion"] == "" and sin_video["fuentes"] == []


def test_sin_reales_del_tipo_listas_vacias():
    r = respuesta_prediccion("offside", OFFSIDE, 1, HISTORIA, ARBITROS)
    assert r["jugadas_reales_similares"] == [] and r["jugadas_reales_arbitro"] == []


@pytest.mark.parametrize(
    "descripcion, esperado",
    [
        ("Min 5 | Fuentes: https://a.com/x https://b.com/y", ("Min 5", ["https://a.com/x", "https://b.com/y"])),
        ("Fuentes: https://a.com/x", ("", ["https://a.com/x"])),
        ("Sin fuentes", ("Sin fuentes", [])),
    ],
)
def test_separar_fuentes(descripcion, esperado):
    assert servicio.separar_fuentes(descripcion) == esperado


def test_separar_fuentes_invierte_al_importador():
    from vargento.importar import Jugada

    for texto in ("Min 5, penal", ""):
        j = Jugada(1, "penal", "A", {}, True, texto, ["https://a.com/x", "https://b.com/y"], "", "", "alta")
        assert servicio.separar_fuentes(j.description) == (texto, j.fuentes)
