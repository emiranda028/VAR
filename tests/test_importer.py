from vargento.db import Database
from vargento.importer import import_csv, template_csv


def test_template_imports_cleanly(tmp_path):
    db = Database(tmp_path / "i.db")
    result = import_csv(db, template_csv())
    assert result["errors"] == [] and result["imported"] == 2
    assert [r["name"] for r in db.list_referees()] == ["Nombre Apellido"]
    assert db.list_referees()[0]["country"] == "Argentina"


def test_semicolon_accents_and_labels(tmp_path):
    db = Database(tmp_path / "i.db")
    db.add_referee("Juan Pérez")
    csv_text = (
        "﻿Tipo;Árbitro;Cobrado;Fecha;decision_campo;distancia_cm;interfiere_juego;jugada_deliberada_rival\n"
        "Fuera de juego;juan pérez;Sí;2025-01-02;No;3,5;si;no\n"
    )
    result = import_csv(db, csv_text)
    assert result["errors"] == [] and result["imported"] == 1 and result["new_referees"] == []
    play = db.list_plays()[0]
    assert play["features"]["distancia_cm"] == 3.5 and play["called"] is True


def test_errors_are_all_or_nothing(tmp_path):
    db = Database(tmp_path / "i.db")
    lines = template_csv().splitlines()
    bad = lines[1].replace("offside", "corner")
    missing_called = lines[2].replace(",no,2025", ",,2025")
    result = import_csv(db, "\n".join([lines[0], lines[1], bad, missing_called]))
    assert result["imported"] == 0 and db.count_plays() == 0 and db.list_referees() == []
    assert [e["row"] for e in result["errors"]] == [3, 4]
    assert "corner" in result["errors"][0]["error"]
    assert "cobrado" in result["errors"][1]["error"]


def test_missing_columns(tmp_path):
    result = import_csv(Database(tmp_path / "i.db"), "tipo,cobrado\noffside,si\n")
    assert result["imported"] == 0 and "arbitro" in result["errors"][0]["error"]
