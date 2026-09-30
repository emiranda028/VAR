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
    client = login("periodista")
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
    client = login("periodista")
    review_id = client.post("/api/predict", json={"play_type": "offside", "features": offside(-20)}).json()["review_id"]
    assert client.post(f"/api/reviews/{review_id}/decision", json={"called": False}).status_code == 422
    ref_id = client.get("/api/referees").json()[1]["id"]
    ok = client.post(f"/api/reviews/{review_id}/decision", json={"called": False, "referee_id": ref_id})
    assert ok.status_code == 200 and ok.json()["referee_id"] == ref_id


def test_role_permissions(login):
    editor = login("editor")
    assert editor.get("/api/users").status_code == 403
    assert editor.post("/api/referees", json={"name": "Nuevo"}).status_code == 201
    review = editor.post("/api/predict", json={"play_type": "offside", "features": offside(3)}).json()
    ref_id = editor.get("/api/referees").json()[0]["id"]
    decided = editor.post(f"/api/reviews/{review['review_id']}/decision", json={"called": True, "referee_id": ref_id})
    assert decided.status_code == 200

    periodista = login("periodista")
    assert periodista.get("/api/users").status_code == 403
    assert periodista.post("/api/referees", json={"name": "Otro"}).status_code == 403
    assert periodista.post("/api/plays/import", json={"csv": "tipo"}).status_code == 403
    assert periodista.post("/api/model/calibrate").status_code == 403
    assert periodista.post("/api/predict", json={"play_type": "offside", "features": offside(3)}).status_code == 200


def test_legacy_roles_are_migrated(tmp_path):
    from vargento import auth
    from vargento.db import Database

    path = tmp_path / "old.db"
    old = Database(path)
    ref = old.add_referee("Juez")
    old.add_user("viejo_arbitro", auth.hash_password("x" * 8), "arbitro", ref["id"])
    old.add_user("viejo_operador", auth.hash_password("x" * 8), "operador")
    old.add_user("viejo_analista", auth.hash_password("x" * 8), "analista")
    old.close()
    roles = {u["username"]: (u["role"], u["referee_id"]) for u in Database(path).list_users()}
    assert roles == {
        "viejo_analista": ("editor", None),
        "viejo_arbitro": ("periodista", None),
        "viejo_operador": ("periodista", None),
    }


def test_user_management(login, app):
    admin = login()
    assert admin.post("/api/users", json={"username": "juez", "password": "corta", "role": "periodista"}).status_code == 422
    assert admin.post("/api/users", json={"username": "juez", "password": "suficientemente", "role": "arbitro"}).status_code == 422
    created = admin.post("/api/users", json={"username": "juez", "password": "suficientemente", "role": "periodista"})
    assert created.status_code == 201
    uid = created.json()["id"]
    assert admin.post("/api/users", json={"username": "JUEZ", "password": "suficientemente", "role": "editor"}).status_code == 409
    assert admin.patch(f"/api/users/{uid}", json={"role": "editor"}).json()["role"] == "editor"

    juez = TestClient(app)
    assert juez.post("/api/auth/login", json={"username": "juez", "password": "suficientemente"}).status_code == 200
    assert admin.patch(f"/api/users/{uid}", json={"active": False}).json()["active"] is False
    assert juez.get("/api/auth/me").status_code == 401  # sesiones cerradas al desactivar
    assert juez.post("/api/auth/login", json={"username": "juez", "password": "suficientemente"}).status_code == 401

    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 400
    assert admin.patch(f"/api/users/{me['id']}", json={"role": "periodista"}).status_code == 400


def test_change_password(login, app):
    client = login("periodista")
    assert client.post("/api/auth/password", json={"current": "mal", "new": "nueva-clave-123"}).status_code == 400
    assert client.post("/api/auth/password", json={"current": "clave-de-prueba", "new": "nueva-clave-123"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"username": "periodista", "password": "nueva-clave-123"}).status_code == 200


def test_import_via_api_and_template(login, db):
    client = login("editor")
    template = client.get("/api/plays/template")
    assert template.status_code == 200 and template.text.startswith("tipo,arbitro")
    before = db.count_plays()
    dry = client.post("/api/plays/import", json={"csv": template.text, "dry_run": True}).json()
    assert dry["valid"] == 2 and dry["imported"] == 0 and dry["new_referees"] == ["Nombre Apellido"]
    done = client.post("/api/plays/import", json={"csv": template.text}).json()
    assert done["imported"] == 2 and db.count_plays() == before + 2


def test_model_evaluate_and_calibrate(login):
    client = login("editor")
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
