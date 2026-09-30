import pytest
from fastapi.testclient import TestClient

from vargento import auth
from vargento.api import create_app
from vargento.db import Database
from vargento.seed import seed

ADMIN_PASSWORD = "admin-clave-segura"
PASSWORD = "clave-de-prueba"


def offside(cm, interfiere=True, campo=False, deliberada=False):
    return {
        "decision_campo": campo,
        "distancia_cm": cm,
        "interfiere_juego": interfiere,
        "jugada_deliberada_rival": deliberada,
    }


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    seed(database, n_plays=600)
    yield database
    database.close()


@pytest.fixture
def app(db, tmp_path):
    return create_app(db, seed_demo=False, admin_password=ADMIN_PASSWORD, media_dir=tmp_path / "media")


@pytest.fixture
def login(app, db):
    """Devuelve un TestClient con sesión iniciada para el rol pedido."""

    def _login(role="admin", referee_id=None):
        client = TestClient(app)
        username = "admin"
        password = ADMIN_PASSWORD
        if role != "admin":
            username = f"{role}{referee_id or ''}"
            if not db.get_user_credentials(username):
                db.add_user(username, auth.hash_password(PASSWORD), role, referee_id)
            password = PASSWORD
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return client

    return _login
