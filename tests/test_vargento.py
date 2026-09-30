import pytest
from fastapi.testclient import TestClient

from vargento.api import create_app
from vargento.db import Database
from vargento.domain import get_play_type, validate_features
from vargento.model import HistoricalPlay, predict, similarity
from vargento.seed import seed

OFFSIDE = get_play_type("offside")


def offside(cm, interfiere=True, campo=False, deliberada=False):
    return {
        "decision_campo": float(campo),
        "distancia_cm": cm,
        "interfiere_juego": float(interfiere),
        "jugada_deliberada_rival": float(deliberada),
    }


def hp(i, ref, feats, called):
    return HistoricalPlay(id=i, play_type="offside", referee_id=ref, features=feats, called=called)


def test_similarity_is_one_for_identical_and_decreases():
    a = offside(10)
    assert similarity(OFFSIDE, a, a) == pytest.approx(1.0)
    assert similarity(OFFSIDE, a, offside(12)) > similarity(OFFSIDE, a, offside(-30))


def test_prediction_follows_similar_plays():
    history = [hp(i, 1, offside(15 + i % 5), True) for i in range(20)]
    history += [hp(100 + i, 1, offside(-20 - i % 5), False) for i in range(20)]
    assert predict(OFFSIDE, offside(16), None, history).probability > 0.85
    assert predict(OFFSIDE, offside(-21), None, history).probability < 0.15


def test_referee_history_shifts_probability():
    feats = offside(2)
    # Todos los demás cobran la mitad; el árbitro 7 cobra siempre esta jugada.
    history = [hp(i, 1, feats, i % 2 == 0) for i in range(40)]
    history += [hp(100 + i, 7, feats, True) for i in range(15)]
    others = predict(OFFSIDE, feats, 1, history)
    strict = predict(OFFSIDE, feats, 7, history)
    assert strict.probability > others.probability + 0.15
    assert strict.referee_calls == 15


def test_no_history_falls_back_to_half():
    r = predict(OFFSIDE, offside(0), None, [])
    assert r.probability == pytest.approx(0.5)
    assert r.recommendation == "DUDOSA"
    assert r.confidence == "baja"


def test_validate_features_rejects_missing_and_out_of_range():
    with pytest.raises(ValueError):
        validate_features(OFFSIDE, {"distancia_cm": 1})
    with pytest.raises(ValueError):
        validate_features(OFFSIDE, offside(500))
    assert validate_features(OFFSIDE, {**offside(3), "interfiere_juego": True})["interfiere_juego"] == 1.0


@pytest.fixture
def client(tmp_path):
    db = Database(tmp_path / "test.db")
    seed(db, n_plays=800)
    yield TestClient(create_app(db, seed_demo=False))
    db.close()


def test_api_predict_end_to_end(client):
    refs = client.get("/api/referees").json()
    assert len(refs) == 8
    r = client.post(
        "/api/predict",
        json={"play_type": "offside", "referee_id": refs[0]["id"], "features": offside(25)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["probability_call"] + body["probability_no_call"] == pytest.approx(1.0)
    assert body["recommendation"] == "COBRAR"
    assert body["similar_plays"]


def test_api_record_play_and_errors(client):
    ref = client.post("/api/referees", json={"name": "Nuevo Árbitro"}).json()
    assert client.post("/api/referees", json={"name": "Nuevo Árbitro"}).status_code == 409
    created = client.post(
        "/api/plays",
        json={"play_type": "offside", "referee_id": ref["id"], "features": offside(5), "called": True},
    )
    assert created.status_code == 201
    stats = client.get(f"/api/referees/{ref['id']}/stats").json()
    assert next(s for s in stats["by_type"] if s["play_type"] == "offside")["plays"] == 1
    assert client.post("/api/predict", json={"play_type": "xx", "features": {}}).status_code == 422
    bad_ref = {"play_type": "offside", "referee_id": 9999, "features": offside(1)}
    assert client.post("/api/predict", json=bad_ref).status_code == 404


def test_ui_is_served(client):
    assert "VARgento" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
