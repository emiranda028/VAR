import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_PASSWORD, offside


def test_requires_login(app):
    client = TestClient(app)
    assert client.get("/api/referees").status_code == 401
    assert client.post("/api/predict", json={"play_type": "offside", "features": offside(1)}).status_code == 401
    assert client.get("/api/health").status_code == 200
    assert "VARgento" in client.get("/").text


def test_login_logout_and_bearer(app):
    client = TestClient(app)
    bad = client.post("/api/auth/login", json={"username": "admin", "password": "incorrecta"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"username": "ADMIN", "password": ADMIN_PASSWORD})
    assert ok.status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["role"] == "admin" and "manage_users" in me["permissions"]
    token = ok.json()["token"]
    other = TestClient(app)
    assert other.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert other.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_login_lockout_after_failures(app):
    client = TestClient(app)
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "mal"})
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 429


def test_predict_then_record_decision_grows_history(login, db):
    client = login("operador")
    ref = client.get("/api/referees").json()[0]
    before = db.count_plays()
    r = client.post("/api/predict", json={"play_type": "offside", "referee_id": ref["id"], "features": offside(25)})
    assert r.status_code == 200
    body = r.json()
    assert body["recommendation"] == "COBRAR"
    assert body["probability_call"] + body["probability_no_call"] == pytest.approx(1.0)
    review_id = body["review_id"]

    d = client.post(f"/api/reviews/{review_id}/decision", json={"called": True, "match": "A vs B", "date": "2025-05-01"})
    assert d.status_code == 200
    assert d.json()["final_called"] is True and d.json()["play_id"]
    assert db.count_plays() == before + 1
    assert client.post(f"/api/reviews/{review_id}/decision", json={"called": False}).status_code == 409

    stats = client.get("/api/reviews/stats").json()
    assert stats["decided"] == 1 and stats["accuracy"] == 1.0
    assert client.get("/api/reviews").json()[0]["id"] == review_id


def test_decision_without_referee_needs_one(login):
    client = login("operador")
    review_id = client.post("/api/predict", json={"play_type": "offside", "features": offside(-20)}).json()["review_id"]
    assert client.post(f"/api/reviews/{review_id}/decision", json={"called": False}).status_code == 422
    ref_id = client.get("/api/referees").json()[1]["id"]
    ok = client.post(f"/api/reviews/{review_id}/decision", json={"called": False, "referee_id": ref_id})
    assert ok.status_code == 200 and ok.json()["referee_id"] == ref_id


def test_role_permissions(login):
    analista = login("analista")
    assert analista.get("/api/users").status_code == 403
    assert analista.post("/api/referees", json={"name": "Nuevo"}).status_code == 201
    review = analista.post("/api/predict", json={"play_type": "offside", "features": offside(3)}).json()
    assert analista.post(f"/api/reviews/{review['review_id']}/decision", json={"called": True}).status_code == 403

    operador = login("operador")
    assert operador.post("/api/referees", json={"name": "Otro"}).status_code == 403
    assert operador.post("/api/plays/import", json={"csv": "tipo"}).status_code == 403
    assert operador.post("/api/model/calibrate").status_code == 403


def test_referee_user_is_bound_to_own_referee(login):
    admin = login()
    refs = admin.get("/api/referees").json()
    mine, other = refs[0]["id"], refs[1]["id"]
    arbitro = login("arbitro", referee_id=mine)
    r = arbitro.post("/api/predict", json={"play_type": "offside", "features": offside(5)})
    assert r.json()["referee"]["id"] == mine
    forbidden = arbitro.post("/api/predict", json={"play_type": "offside", "referee_id": other, "features": offside(5)})
    assert forbidden.status_code == 403
    rid = r.json()["review_id"]
    assert arbitro.post(f"/api/reviews/{rid}/decision", json={"called": True, "referee_id": other}).status_code == 403
    assert arbitro.post(f"/api/reviews/{rid}/decision", json={"called": True}).status_code == 200


def test_user_management(login, app):
    admin = login()
    ref_id = admin.get("/api/referees").json()[0]["id"]
    assert admin.post("/api/users", json={"username": "juez", "password": "corta", "role": "arbitro"}).status_code == 422
    created = admin.post(
        "/api/users", json={"username": "juez", "password": "suficientemente", "role": "arbitro", "referee_id": ref_id}
    )
    assert created.status_code == 201
    uid = created.json()["id"]
    assert admin.post("/api/users", json={"username": "JUEZ", "password": "suficientemente", "role": "operador"}).status_code == 409

    juez = TestClient(app)
    assert juez.post("/api/auth/login", json={"username": "juez", "password": "suficientemente"}).status_code == 200
    assert admin.patch(f"/api/users/{uid}", json={"active": False}).json()["active"] is False
    assert juez.get("/api/auth/me").status_code == 401  # sesiones cerradas al desactivar
    assert juez.post("/api/auth/login", json={"username": "juez", "password": "suficientemente"}).status_code == 401

    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 400
    assert admin.patch(f"/api/users/{me['id']}", json={"role": "operador"}).status_code == 400


def test_change_password(login, app):
    client = login("operador")
    assert client.post("/api/auth/password", json={"current": "mal", "new": "nueva-clave-123"}).status_code == 400
    assert client.post("/api/auth/password", json={"current": "clave-de-prueba", "new": "nueva-clave-123"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"username": "operador", "password": "nueva-clave-123"}).status_code == 200


def test_import_via_api_and_template(login, db):
    client = login("analista")
    template = client.get("/api/plays/template")
    assert template.status_code == 200 and template.text.startswith("tipo,arbitro")
    before = db.count_plays()
    dry = client.post("/api/plays/import", json={"csv": template.text, "dry_run": True}).json()
    assert dry["valid"] == 2 and dry["imported"] == 0 and dry["new_referees"] == ["Nombre Apellido"]
    done = client.post("/api/plays/import", json={"csv": template.text}).json()
    assert done["imported"] == 2 and db.count_plays() == before + 2


def test_model_evaluate_and_calibrate(login):
    client = login("analista")
    m = client.post("/api/model/evaluate").json()
    assert m["n"] > 0 and 0 <= m["accuracy"] <= 1
    c = client.post("/api/model/calibrate").json()
    assert c["after"]["log_loss"] <= c["before"]["log_loss"] or not c["applied"]
    assert client.get("/api/model").json()["params"]["bandwidth"] > 0


def test_validation_errors(login):
    client = login()
    assert client.post("/api/predict", json={"play_type": "xx", "features": {}}).status_code == 422
    bad_ref = {"play_type": "offside", "referee_id": 9999, "features": offside(1)}
    assert client.post("/api/predict", json=bad_ref).status_code == 404
    assert client.post("/api/predict", json={"play_type": "offside", "features": offside(900)}).status_code == 422
