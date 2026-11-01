"""Respuesta de referencia de POST /api/predict (protege el refactor a vargento.servicio).

La referencia se generó con el código previo al refactor. La comparación es de
igualdad exacta sobre el JSON decodificado: la API ya redondea los floats y el
módulo json de Python los serializa con repr (ida y vuelta sin pérdida), así que
no hace falta tolerancia.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vargento.api import create_app
from vargento.db import Database
from vargento.seed import seed

REFERENCIA = Path(__file__).parent / "datos" / "predict_referencia.json"

OFFSIDE = {"decision_campo": 0.0, "distancia_cm": 12.0, "interfiere_juego": 1.0, "jugada_deliberada_rival": 0.0}
PENAL = {
    "decision_campo": 1.0,
    "intensidad_contacto": 2.0,
    "toca_pelota_primero": 0.0,
    "intento_jugar_balon": 1.0,
    "indicios_simulacion": 0.0,
    "contacto_dentro_area": 1.0,
}


def casos(client: TestClient) -> dict[str, dict]:
    """Los dos pedidos fijos: offside con el primer árbitro y penal sin árbitro."""
    primer_arbitro = client.get("/api/referees").json()[0]["id"]
    return {
        "offside_con_arbitro": {"play_type": "offside", "referee_id": primer_arbitro, "features": OFFSIDE},
        "penal_sin_arbitro": {"play_type": "penal", "referee_id": None, "features": PENAL},
    }


def respuestas(client: TestClient) -> dict[str, dict]:
    salida = {}
    for nombre, cuerpo in casos(client).items():
        r = client.post("/api/predict", json=cuerpo)
        assert r.status_code == 200
        salida[nombre] = r.json()
    return salida


@pytest.fixture
def client(tmp_path):
    db = Database(tmp_path / "test.db")
    seed(db, n_plays=800)
    yield TestClient(create_app(db, seed_demo=False, aprobadas_dir=None))
    db.close()


CLAVES_NUEVAS = {"jugadas_reales_similares", "jugadas_reales_arbitro"}


def test_predict_igual_a_referencia(client):
    """Las claves de la referencia no cambian; sólo se suman las de jugadas reales."""
    esperado = json.loads(REFERENCIA.read_text(encoding="utf-8"))
    obtenido = respuestas(client)
    assert obtenido.keys() == esperado.keys()
    for nombre, ref in esperado.items():
        assert set(obtenido[nombre]) - set(ref) == CLAVES_NUEVAS
        assert {k: obtenido[nombre][k] for k in ref} == ref
