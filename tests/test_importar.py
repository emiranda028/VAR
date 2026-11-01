import json

import pytest

from vargento.db import Database
from vargento.importar import importar, main

PENAL = {
    "decision_campo": False,
    "intensidad_contacto": 2,
    "toca_pelota_primero": False,
    "intento_jugar_balon": True,
    "indicios_simulacion": False,
    "contacto_dentro_area": True,
}


def jugada(**extra):
    base = {
        "play_type": "penal",
        "referee_name": "Árbitro Real",
        "called": True,
        "features": PENAL,
        "match": "A vs B",
        "date": "2025-08-17",
        "description": "Min 34",
        "fuentes": ["https://ejemplo.com/nota"],
        "confianza": "alta",
    }
    base.update(extra)
    return json.dumps(base)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_simulacion_no_toca_la_base(db):
    res = importar(db, [jugada()])
    assert len(res.validas) == 1 and res.cargadas == 0
    assert list(res.arbitros_nuevos.values()) == ["Árbitro Real"]
    assert db.count_plays() == 0 and db.count_referees() == 0


def test_aplicar_crea_arbitro_y_guarda_fuentes(db):
    res = importar(db, [jugada(), jugada(description="Min 80")], aplicar=True)
    assert res.cargadas == 2
    assert db.count_referees() == 1
    play = db.list_plays()[0]
    assert play["referee"] == "Árbitro Real" and play["called"] is True
    assert "https://ejemplo.com/nota" in play["description"]


def test_reutiliza_arbitro_existente_sin_importar_mayusculas(db):
    db.add_referee("Árbitro Real")
    res = importar(db, [jugada(referee_name="árbitro real")], aplicar=True)
    assert res.arbitros_nuevos == {} and db.count_referees() == 1


def test_errores_duplicados_y_baja_confianza(db):
    importar(db, [jugada()], aplicar=True)
    lineas = [
        jugada(),  # ya cargada
        jugada(description="Min 50"),
        jugada(description="Min 50"),  # repetida en el archivo
        jugada(description="Min 60", confianza="baja"),
        jugada(description="Min 70", fuentes=[]),
        jugada(description="Min 75", play_type="corner"),
        jugada(description="Min 77", features={**PENAL, "intensidad_contacto": 9}),
        "esto no es json",
        "",
    ]
    res = importar(db, lineas)
    assert len(res.validas) == 1
    assert len(res.duplicadas) == 2
    assert len(res.baja_confianza) == 1
    assert len(res.errores) == 4
    assert len(importar(db, lineas, incluir_baja=True).validas) == 2


def test_cli(tmp_path, capsys):
    archivo = tmp_path / "lote.jsonl"
    archivo.write_text(jugada() + "\n", encoding="utf-8")
    base = str(tmp_path / "cli.db")
    assert main([str(archivo), "--db", base]) == 0
    assert "Simulación" in capsys.readouterr().out
    assert main([str(archivo), "--db", base, "--aplicar"]) == 0
    assert "Cargadas en la base: 1" in capsys.readouterr().out
    d = Database(base)
    assert d.count_plays() == 1
    d.close()


def test_fuentes_tienen_que_ser_urls(db):
    res = importar(db, [jugada(fuentes=[""]), jugada(fuentes=["ver el diario"]), jugada(fuentes=5)])
    assert len(res.errores) == 3 and not res.validas
    res = importar(db, [jugada(fuentes="https://x.com/n")], aplicar=True)
    assert res.cargadas == 1
    assert db.list_plays()[0]["description"] == "Min 34 | Fuentes: https://x.com/n"


def test_arbitro_con_distinta_capitalizacion_cuenta_una_vez(db):
    res = importar(db, [jugada(referee_name="Juan Pérez"), jugada(referee_name="JUAN PÉREZ", description="b")])
    assert len(res.arbitros_nuevos) == 1
    importar(db, [jugada(referee_name="Juan Pérez"), jugada(referee_name="JUAN PÉREZ", description="b")], aplicar=True)
    assert db.count_referees() == 1


def test_duplicada_aunque_cambie_la_fuente(db):
    importar(db, [jugada()], aplicar=True)
    res = importar(db, [jugada(fuentes=["https://otra.com/y"]), jugada(description="MIN 34")])
    assert len(res.duplicadas) == 2 and not res.validas


def test_confianza_sin_distinguir_mayusculas_y_valores_invalidos(db):
    res = importar(db, [jugada(confianza="Baja"), jugada(description="x", confianza="segura")])
    assert len(res.baja_confianza) == 1 and len(res.errores) == 1


def test_simulacion_no_crea_la_base_y_cli_robusta(tmp_path, capsys):
    archivo = tmp_path / "lote.jsonl"
    archivo.write_text(jugada() + "\n", encoding="utf-8-sig")  # con BOM
    base = tmp_path / "nueva.db"
    assert main([str(archivo), "--db", str(base)]) == 0
    assert not base.exists()
    assert main([str(tmp_path / "no-existe.jsonl"), "--db", str(base)]) == 2
    assert "No se pudo leer" in capsys.readouterr().err


ID = "dQw4w9WgXcQ"
CANONICA = '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","inicio":30}'


def _videos(db: Database) -> list:
    return [r[0] for r in db._conn.execute("SELECT video FROM plays ORDER BY id")]


def test_video_opcional_y_url_canonica(db):
    lineas = [
        jugada(),  # sin video
        jugada(description="Min 40", video=None),
        jugada(description="Min 50", video={"url": f"https://youtu.be/{ID}", "inicio": 30}),
        jugada(description="Min 60", video={"url": f"https://m.youtube.com/watch?v={ID}&t=30", "inicio": 30,
                                            "titulo": "se ignora"}),
        jugada(description="Min 70", video={"url": f"http://youtube.com/watch?feature=share&v={ID}", "inicio": 30}),
    ]
    res = importar(db, lineas, aplicar=True)
    assert res.errores == [] and res.cargadas == 5
    assert _videos(db) == [None, None, CANONICA, CANONICA, CANONICA]


@pytest.mark.parametrize(
    "video, motivo",
    [
        ({"url": "https://vimeo.com/123456789", "inicio": 0}, "enlace de YouTube"),
        ({"url": "https://youtu.be/dQw4w9WgXc", "inicio": 0}, "ID de YouTube"),  # 10 caracteres
        ({"url": "https://www.youtube.com/watch?v=dQw4w9WgX$c", "inicio": 0}, "ID de YouTube"),
        ({"url": f"https://youtu.be/{ID}", "inicio": -1}, "video.inicio"),
        ({"url": f"https://youtu.be/{ID}", "inicio": True}, "video.inicio"),
        ({"url": f"https://youtu.be/{ID}", "inicio": "30"}, "video.inicio"),
        ({"inicio": 30}, "falta video.url"),
        ("https://youtu.be/dQw4w9WgXcQ", "objeto con url e inicio"),
    ],
)
def test_video_invalido_va_a_errores(db, video, motivo):
    res = importar(db, [jugada(video=video)])
    assert not res.validas and len(res.errores) == 1
    assert res.errores[0].startswith("línea 1: ") and motivo in res.errores[0]


def test_duplicada_con_video_actualiza_sin_cargar(db):
    importar(db, [jugada()], aplicar=True)
    con_video = jugada(video={"url": f"https://youtu.be/{ID}", "inicio": 30})
    simulado = importar(db, [con_video])
    assert len(simulado.duplicadas) == 1 and len(simulado.videos_actualizados) == 1
    assert _videos(db) == [None]  # sin aplicar no toca la base
    res = importar(db, [con_video], aplicar=True)
    assert len(res.duplicadas) == 1 and res.cargadas == 0 and len(res.videos_actualizados) == 1
    assert db.count_plays() == 1 and _videos(db) == [CANONICA]
    # mismo video: no cuenta; sin video: no borra el guardado
    assert importar(db, [con_video, jugada()], aplicar=True).videos_actualizados == []
    assert _videos(db) == [CANONICA]
    # video distinto: se reemplaza
    otro = jugada(video={"url": f"https://youtu.be/{ID}", "inicio": 45})
    assert len(importar(db, [otro], aplicar=True).videos_actualizados) == 1
    assert '"inicio":45' in _videos(db)[0]


def test_video_no_se_escribe_en_jugadas_demo(db):
    ref = db.add_referee("Árbitro Real")["id"]
    db.add_play("penal", ref, PENAL, True, "Min 34", "A vs B", "2025-08-17", origen="demo")
    res = importar(db, [jugada(video={"url": f"https://youtu.be/{ID}", "inicio": 0})], aplicar=True)
    assert len(res.duplicadas) == 1 and res.videos_actualizados == []
    assert _videos(db) == [None]


def test_informe_muestra_videos_actualizados(tmp_path, capsys):
    archivo = tmp_path / "lote.jsonl"
    base = str(tmp_path / "cli.db")
    archivo.write_text(jugada() + "\n", encoding="utf-8")
    assert main([str(archivo), "--db", base, "--aplicar"]) == 0
    assert "Videos actualizados: 0" in capsys.readouterr().out
    archivo.write_text(jugada(video={"url": f"https://youtu.be/{ID}", "inicio": 0}) + "\n", encoding="utf-8")
    assert main([str(archivo), "--db", base, "--aplicar"]) == 0
    assert "Videos actualizados: 1" in capsys.readouterr().out
