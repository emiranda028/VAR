import json
import math

import pytest

from vargento.db import Database
from vargento.domain import PLAY_TYPES
from vargento.evaluar import evaluar, main


def _feats(tipo: str) -> dict[str, float]:
    """Atributos válidos fijos (todos en su mínimo) para el tipo."""
    return {f.key: f.min for f in PLAY_TYPES[tipo].features}


def _base_minima(ruta) -> Database:
    """1000 penales demo idénticos y cobrados (árbitro A) + 2 jugadas reales del árbitro B.

    Cálculo a mano (sólo demo):
    - penal real no cobrado, idéntico a los demo: B no tiene penales demo, así que no hay
      ajuste por árbitro; la probabilidad (~0,99997) se recorta a 0,999 → falla y aporta
      -log(0,001). No es DUDOSA.
    - mano real cobrada: no hay manos en el histórico → probabilidad 0,5 → acierta
      (0,5 >= 0,5), aporta -log(0,5) y es DUDOSA.
    """
    db = Database(ruta)
    a = db.add_referee("Árbitro A")["id"]
    b = db.add_referee("Árbitro B")["id"]
    db.add_plays([("penal", a, _feats("penal"), True, "", "", "")] * 1000)
    db.add_play("penal", b, _feats("penal"), False)
    db.add_play("mano", b, _feats("mano"), True)
    return db


def test_metricas_coinciden_con_calculo_a_mano(tmp_path):
    db = _base_minima(tmp_path / "m.db")
    res = evaluar(db)
    assert res["n_reales"] == 2
    total = res["variantes"]["solo_demo"]["total"]
    assert total["n"] == 2
    assert total["aciertos"] == 1
    assert total["acierto"] == pytest.approx(0.5)
    assert total["log_loss"] == pytest.approx((-math.log(1e-3) - math.log(0.5)) / 2)
    assert total["dudosas"] == 1
    assert total["tasa_dudosa"] == pytest.approx(0.5)
    por_tipo = res["variantes"]["solo_demo"]["por_tipo"]
    assert set(por_tipo) == {"penal", "mano"}
    assert por_tipo["penal"] == {
        "n": 1, "aciertos": 0, "acierto": 0.0,
        "log_loss": pytest.approx(-math.log(1e-3)), "dudosas": 0, "tasa_dudosa": 0.0,
    }
    assert por_tipo["mano"] == {
        "n": 1, "aciertos": 1, "acierto": 1.0,
        "log_loss": pytest.approx(math.log(2)), "dudosas": 1, "tasa_dudosa": 1.0,
    }


def test_leave_one_out_excluye_la_propia_jugada(tmp_path):
    # Dos penales reales idénticos y cobrados, de árbitros distintos, sin demo.
    db = Database(tmp_path / "l.db")
    b = db.add_referee("Árbitro B")["id"]
    c = db.add_referee("Árbitro C")["id"]
    db.add_play("penal", b, _feats("penal"), True)
    db.add_play("penal", c, _feats("penal"), True)
    res = evaluar(db)
    # Sólo demo: histórico vacío → 0,5 y DUDOSA.
    solo = res["variantes"]["solo_demo"]["total"]
    assert solo["log_loss"] == pytest.approx(math.log(2))
    assert solo["dudosas"] == 2
    # Leave-one-out: cada una ve sólo a la otra (otro árbitro, sin ajuste).
    # tasa base = 2/3; general = (1 + 2/3) / (1 + 1) = 5/6. Si se viera a sí misma daría 11/12.
    loo = res["variantes"]["leave_one_out"]["total"]
    assert loo["aciertos"] == 2
    assert loo["log_loss"] == pytest.approx(-math.log(5 / 6))
    assert loo["dudosas"] == 0


def _estado(db: Database) -> tuple:
    return db.count_plays(), db.count_plays("real"), db.list_referees()


def test_cli_db_json_no_modifica_la_base(tmp_path, capsys):
    ruta = tmp_path / "c.db"
    db = _base_minima(ruta)
    antes = _estado(db)
    db.close()

    assert main(["--db", str(ruta), "--json"]) == 0
    salida = capsys.readouterr().out
    datos = json.loads(salida)  # sólo JSON en stdout
    assert set(datos) == {"n_reales", "variantes"}
    assert set(datos["variantes"]) == {"solo_demo", "leave_one_out"}
    for v in datos["variantes"].values():
        assert set(v) == {"total", "por_tipo"}
        assert set(v["total"]) == {"n", "aciertos", "acierto", "log_loss", "dudosas", "tasa_dudosa"}
    db = Database(ruta)
    assert datos == evaluar(db)
    assert _estado(db) == antes
    db.close()

    assert main(["--db", str(ruta)]) == 0
    texto = capsys.readouterr().out
    assert "Sólo demo" in texto and "Leave-one-out" in texto
    assert "3,800" in texto  # log-loss total sólo demo con 3 decimales y coma


def test_cli_db_inexistente_da_2_y_no_crea_archivo(tmp_path, capsys):
    ruta = tmp_path / "no-existe.db"
    assert main(["--db", str(ruta)]) == 2
    assert not ruta.exists()
    assert capsys.readouterr().out == ""


def test_cli_sin_reales_da_1(tmp_path, capsys):
    ruta = tmp_path / "s.db"
    db = Database(ruta)
    a = db.add_referee("Árbitro A")["id"]
    db.add_plays([("penal", a, _feats("penal"), True, "", "", "")])
    db.close()
    assert main(["--db", str(ruta)]) == 1
    assert "No hay jugadas reales para evaluar" in capsys.readouterr().err
