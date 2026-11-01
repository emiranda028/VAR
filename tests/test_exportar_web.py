import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vargento.api import cargar_aprobadas, create_app
from vargento.db import Database
from vargento.domain import PLAY_TYPES
from vargento.exportar_web import main
from vargento.seed import seed
from vargento.servicio import historia_desde_json, respuesta_prediccion

RAIZ = Path(__file__).parent.parent
OFFSIDE = {"decision_campo": 0.0, "distancia_cm": 4.0, "interfiere_juego": 1.0, "jugada_deliberada_rival": 0.0}
LOTE = {
    "play_type": "offside",
    "referee_name": "Árbitro Exportado",
    "called": True,
    "features": OFFSIDE,
    "match": "Equipo A vs Equipo B",
    "date": "2025-05-01",
    "description": "Jugada de prueba para el exportador.",
    "fuentes": ["https://example.com/nota"],
    "confianza": "alta",
}


def _leer(carpeta: Path, nombre: str):
    return json.loads((carpeta / "datos" / nombre).read_text(encoding="utf-8"))


@pytest.fixture
def aprobadas(tmp_path, monkeypatch):
    carpeta = tmp_path / "aprobadas"
    carpeta.mkdir()
    (carpeta / "lote.jsonl").write_text(json.dumps(LOTE, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("VARGENTO_APROBADAS", str(carpeta))
    return carpeta


@pytest.fixture
def exportado(tmp_path, aprobadas):
    salida = tmp_path / "sitio" / "anidada"  # la carpeta todavía no existe
    assert main([str(salida)]) == 0
    return salida


def test_escribe_todos_los_archivos(exportado):
    for ruta in (
        "index.html", "app.js", "styles.css",
        "py/vargento/__init__.py", "py/vargento/domain.py", "py/vargento/model.py", "py/vargento/servicio.py",
        "datos/play-types.json", "datos/referees.json", "datos/history.json",
    ):
        assert (exportado / ruta).is_file(), ruta
    assert (exportado / "app.js").read_bytes() == (RAIZ / "vargento" / "static" / "app.js").read_bytes()
    assert (exportado / "py/vargento/servicio.py").read_bytes() == (RAIZ / "vargento" / "servicio.py").read_bytes()


def test_index_apunta_a_rutas_relativas(exportado):
    html = (exportado / "index.html").read_text(encoding="utf-8")
    assert "/static/" not in html
    assert "VARGENTO_ESTATICO" in html
    assert html.index("VARGENTO_ESTATICO") < html.index('src="app.js"')
    assert 'href="styles.css"' in html


def test_play_types_y_arbitros(exportado):
    assert _leer(exportado, "play-types.json") == [t.to_dict() for t in PLAY_TYPES.values()]
    arbitros = _leer(exportado, "referees.json")
    history = _leer(exportado, "history.json")
    assert all(set(r) == {"id", "name", "country", "plays"} for r in arbitros)
    assert sum(r["plays"] for r in arbitros) == len(history)
    assert any(r["name"] == "Árbitro Exportado" for r in arbitros)  # se cargó el lote aprobado
    assert set(history[0]) == {"id", "play_type", "referee_id", "features", "called", "description", "match", "date"}
    assert "Árbitro Exportado" in (exportado / "datos" / "referees.json").read_text(encoding="utf-8")


def test_equivale_a_la_api(exportado, aprobadas, tmp_path):
    arbitros = _leer(exportado, "referees.json")
    history = historia_desde_json(_leer(exportado, "history.json"))
    db = Database(tmp_path / "misma.db")
    seed(db)
    cargar_aprobadas(db, aprobadas)
    client = TestClient(create_app(db, seed_demo=False, aprobadas_dir=None))
    try:
        for ref_id in (arbitros[0]["id"], None):
            esperado = client.post(
                "/api/predict", json={"play_type": "offside", "referee_id": ref_id, "features": OFFSIDE}
            ).json()
            assert respuesta_prediccion("offside", OFFSIDE, ref_id, history, arbitros) == esperado
    finally:
        db.close()


def test_no_toca_vargento_db_ni_data(tmp_path, aprobadas):
    def huella() -> dict:
        rutas = [RAIZ / "vargento.db", *sorted((RAIZ / "data").rglob("*"))]
        return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in rutas if p.exists()}

    antes = huella()
    assert main([str(tmp_path / "out")]) == 0
    assert huella() == antes


AVISO = "Versión de demostración: sólo consulta; los datos de demo son sintéticos"


def test_exportado_tiene_bandera_aviso_y_pyodide(exportado):
    html = (exportado / "index.html").read_text(encoding="utf-8")
    assert "window.VARGENTO_ESTATICO = true" in html
    assert AVISO in html
    assert 'id="aviso-demo"' in html
    js = (exportado / "app.js").read_text(encoding="utf-8")
    assert "pyodide/v0.26.4/full/pyodide.js" in js


def test_aviso_oculto_en_modo_servidor():
    html = (RAIZ / "vargento" / "static" / "index.html").read_text(encoding="utf-8")
    assert "VARGENTO_ESTATICO" not in html
    linea = next(l for l in html.splitlines() if AVISO in l)
    assert " hidden>" in linea


def test_app_js_sin_rutas_absolutas_para_py_ni_datos():
    js = (RAIZ / "vargento" / "static" / "app.js").read_text(encoding="utf-8")
    for prefijo in ("py/", "datos/"):
        for comilla in ('"', "'", "`"):
            assert f"{comilla}/{prefijo}" not in js, f"ruta absoluta {comilla}/{prefijo}"
    assert "`py/vargento/" in js and '"datos/history.json"' in js


def test_workflow_pages_es_yaml_valido():
    texto = (RAIZ / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "\t" not in texto
    try:
        import yaml
    except ImportError:  # PyYAML no es dependencia: sin él, chequeo mínimo del contenido
        for clave in ("workflow_dispatch", "id-token: write", "actions/deploy-pages@v4", "_site"):
            assert clave in texto
        return
    wf = yaml.safe_load(texto)
    disparadores = wf.get("on", wf.get(True))  # PyYAML interpreta `on` como booleano
    assert disparadores["push"]["branches"] == ["main"] and "workflow_dispatch" in disparadores
    assert wf["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    assert wf["concurrency"] == {"group": "pages", "cancel-in-progress": False}
    pasos = [p.get("run") or p.get("uses") for p in wf["jobs"]["build"]["steps"]]
    assert pasos.index("python -m pytest -q") < pasos.index("python -m vargento.exportar_web _site")
    deploy = wf["jobs"]["deploy"]
    assert deploy["needs"] == "build" and deploy["environment"]["name"] == "github-pages"
    assert deploy["steps"][0]["uses"] == "actions/deploy-pages@v4" and deploy["steps"][0]["id"] == "deployment"


def test_reexportar_borra_archivos_viejos_sin_tocar_afuera(exportado):
    (exportado / "viejo.txt").write_text("x", encoding="utf-8")
    (exportado / "py" / "vargento" / "api.py").write_text("x", encoding="utf-8")
    vecino = exportado.parent / "vecino.txt"
    vecino.write_text("no tocar", encoding="utf-8")
    assert main([str(exportado)]) == 0
    assert not (exportado / "viejo.txt").exists()
    assert not (exportado / "py" / "vargento" / "api.py").exists()
    assert (exportado / "index.html").is_file()
    assert vecino.read_text(encoding="utf-8") == "no tocar"


def test_carpeta_vacia_existente_se_usa(tmp_path, aprobadas):
    salida = tmp_path / "vacia"
    salida.mkdir()
    assert main([str(salida)]) == 0
    assert (salida / "index.html").is_file()


@pytest.mark.parametrize("contenido", [("notas.txt",), ("index.html",), ("datos/",)])
def test_no_borra_carpeta_que_no_es_exportacion(tmp_path, aprobadas, capsys, contenido):
    salida = tmp_path / "otra"
    salida.mkdir()
    for nombre in contenido:
        if nombre.endswith("/"):
            (salida / nombre).mkdir()
        else:
            (salida / nombre).write_text("mío", encoding="utf-8")
    antes = sorted(p.name for p in salida.iterdir())
    assert main([str(salida)]) == 2
    assert "no parece una exportación previa" in capsys.readouterr().err
    assert sorted(p.name for p in salida.iterdir()) == antes


def _carpeta(tmp_path: Path, archivos: dict[str, str]) -> Path:
    salida = tmp_path / "ajena"
    (salida / "datos").mkdir(parents=True)
    for nombre, texto in archivos.items():
        (salida / nombre).write_text(texto, encoding="utf-8")
    return salida


@pytest.mark.parametrize(
    "archivos",
    [
        {"index.html": "mio", "notas.txt": "mio"},  # index sin bandera
        {"index.html": "<script>window.VARGENTO_ESTATICO = true;</script>", "notas.txt": "mio"},  # sin marcador
        {".vargento-export": "", "index.html": "mio", "notas.txt": "mio"},  # marcador pero index sin bandera
    ],
)
def test_sin_marcador_y_bandera_no_borra_nada(tmp_path, aprobadas, capsys, archivos):
    salida = _carpeta(tmp_path, archivos)
    antes = sorted(str(p.relative_to(salida)) for p in salida.rglob("*"))
    assert main([str(salida)]) == 2
    assert "no parece una exportación previa" in capsys.readouterr().err
    assert sorted(str(p.relative_to(salida)) for p in salida.rglob("*")) == antes
    assert (salida / "notas.txt").read_text(encoding="utf-8") == "mio"


def test_exportacion_escribe_marcador(exportado):
    assert (exportado / ".vargento-export").is_file()


def test_symlink_roto_da_error_claro(tmp_path, aprobadas, capsys):
    enlace = tmp_path / "roto"
    enlace.symlink_to(tmp_path / "no-existe")
    assert main([str(enlace)]) == 2
    assert "enlace simbólico roto" in capsys.readouterr().err
    assert not (tmp_path / "no-existe").exists()
