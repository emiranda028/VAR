import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from vargento.api import APROBADAS_DIR, create_app
from vargento.db import Database
from vargento.importar import importar
from vargento.seed import seed

LOTE_1 = "2026-10-31-penales-lpf.jsonl"
OFFSIDE = {"decision_campo": 0, "distancia_cm": 5, "interfiere_juego": 1, "jugada_deliberada_rival": 0}

ESQUEMA_VIEJO = """
CREATE TABLE referees (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                       country TEXT NOT NULL DEFAULT '');
CREATE TABLE plays (id INTEGER PRIMARY KEY AUTOINCREMENT, play_type TEXT NOT NULL,
                    referee_id INTEGER NOT NULL REFERENCES referees(id), features TEXT NOT NULL,
                    called INTEGER NOT NULL, description TEXT NOT NULL DEFAULT '',
                    match TEXT NOT NULL DEFAULT '', date TEXT NOT NULL DEFAULT '');
INSERT INTO referees (name) VALUES ('Árbitro');
INSERT INTO plays (play_type, referee_id, features, called, description)
VALUES ('offside', 1, '{}', 1, 'jugada común'),
       ('penal', 1, '{}', 0, 'Min 3 | Fuentes: https://ejemplo.com/nota');
"""


def _origenes(db: Database) -> list[str]:
    return [r[0] for r in db._conn.execute("SELECT origen FROM plays ORDER BY id")]


def test_migracion_de_base_vieja(tmp_path):
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA_VIEJO)
    con.close()
    for _ in range(2):  # reabrir no falla ni cambia nada
        db = Database(ruta)
        assert _origenes(db) == ["demo", "real"]
        db.close()


def test_origen_segun_como_se_carga(tmp_path):
    db = Database(tmp_path / "t.db")
    seed(db, n_plays=20)
    assert set(_origenes(db)) == {"demo"}
    lineas = (APROBADAS_DIR / LOTE_1).read_text(encoding="utf-8-sig").splitlines()
    assert importar(db, lineas, aplicar=True, incluir_baja=True).cargadas == db.count_plays("real") == 10
    client = TestClient(create_app(db, seed_demo=False, aprobadas_dir=None))
    ref = client.get("/api/referees").json()[0]["id"]
    body = {"play_type": "offside", "referee_id": ref, "features": OFFSIDE, "called": True}
    assert client.post("/api/plays", json=body).status_code == 201
    assert _origenes(db)[-1] == "real"
    with pytest.raises(ValueError):
        db.add_play("offside", ref, OFFSIDE, True, origen="otro")
    db.close()


def test_arranque_con_lotes_aprobados_reales(tmp_path):
    db = Database(tmp_path / "t.db")
    client = TestClient(create_app(db, seed_demo=True, aprobadas_dir=APROBADAS_DIR))
    health = client.get("/api/health").json()
    assert (health["plays_demo"], health["plays_real"], health["plays"]) == (1600, 10, 1610)
    items = client.get("/api/plays?limit=500").json()
    assert items and all(p["origen"] in {"demo", "real"} for p in items)
    db.close()


def test_carga_de_aprobadas_es_idempotente(tmp_path):
    carpeta = tmp_path / "aprobadas"
    carpeta.mkdir()
    shutil.copy(APROBADAS_DIR / LOTE_1, carpeta / LOTE_1)
    db = Database(tmp_path / "t.db")
    create_app(db, seed_demo=False, aprobadas_dir=carpeta)
    antes = (db.count_plays(), db.count_referees())
    create_app(db, seed_demo=False, aprobadas_dir=carpeta)
    assert (db.count_plays(), db.count_referees()) == antes == (10, 6)
    db.close()


@pytest.mark.parametrize("carpeta", ["vacia", "no-existe", None, "env-vacia"])
def test_sin_lotes_arranca_igual(tmp_path, monkeypatch, carpeta):
    (tmp_path / "vacia").mkdir()
    monkeypatch.setenv("VARGENTO_APROBADAS", "")
    db = Database(tmp_path / "t.db")
    # "env-vacia": sin aprobadas_dir, manda la variable de entorno vacía.
    kwargs = {} if carpeta == "env-vacia" else {"aprobadas_dir": carpeta and tmp_path / carpeta}
    client = TestClient(create_app(db, seed_demo=False, **kwargs))
    health = client.get("/api/health").json()
    assert (health["plays"], health["plays_demo"], health["plays_real"]) == (0, 0, 0)
    db.close()


def test_lote_roto_no_impide_arrancar(tmp_path, caplog):
    carpeta = tmp_path / "aprobadas"
    carpeta.mkdir()
    (carpeta / "roto.jsonl").write_text("{no es json\n", encoding="utf-8")
    db = Database(tmp_path / "t.db")
    create_app(db, seed_demo=False, aprobadas_dir=carpeta)
    assert db.count_plays() == 0
    assert "roto.jsonl" in caplog.text
    db.close()


@pytest.mark.parametrize("archivo", sorted(APROBADAS_DIR.glob("*.jsonl")), ids=lambda p: p.name)
def test_lotes_aprobados_son_validos(archivo):
    db = Database(":memory:")
    res = importar(db, archivo.read_text(encoding="utf-8-sig").splitlines(), incluir_baja=True)
    assert res.errores == [] and res.validas
    db.close()


def test_copia_aprobada_es_identica_a_la_candidata():
    candidata = APROBADAS_DIR.parent / "candidatas" / LOTE_1
    assert candidata.exists()
    assert (APROBADAS_DIR / LOTE_1).read_bytes() == candidata.read_bytes()
