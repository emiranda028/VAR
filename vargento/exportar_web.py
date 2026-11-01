"""Exporta VARgento como sitio estático (UI + código Python puro + datos JSON).

Uso: python -m vargento.exportar_web <carpeta_salida>

Arma una base SQLite temporal (nunca toca `vargento.db`) con la demo y los
lotes aprobados (respeta VARGENTO_APROBADAS igual que la app) y la vuelca a JSON.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from .api import STATIC_DIR, _POR_DEFECTO, _carpeta_aprobadas, cargar_aprobadas
from .db import Database
from .domain import PLAY_TYPES
from .seed import seed

PAQUETE = Path(__file__).parent
MODULOS_PUROS = ("__init__.py", "domain.py", "model.py", "servicio.py")
MARCA_ESTATICO = "<script>window.VARGENTO_ESTATICO = true;</script>\n  "


def _index_estatico(html: str) -> str:
    html = html.replace("/static/styles.css", "styles.css").replace("/static/app.js", "app.js")
    script = '<script src="app.js"></script>'
    if script not in html:
        raise ValueError("index.html no tiene el <script> de app.js esperado")
    return html.replace(script, MARCA_ESTATICO + script, 1)


def _escribir_json(ruta: Path, datos) -> None:
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def exportar_base(db: Database, salida: Path) -> None:
    """Escribe el sitio estático en `salida` a partir de una base ya cargada."""
    (salida / "py" / "vargento").mkdir(parents=True, exist_ok=True)
    (salida / "datos").mkdir(parents=True, exist_ok=True)

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    (salida / "index.html").write_text(_index_estatico(html), encoding="utf-8")
    for nombre in ("app.js", "styles.css"):
        shutil.copyfile(STATIC_DIR / nombre, salida / nombre)
    for nombre in MODULOS_PUROS:
        shutil.copyfile(PAQUETE / nombre, salida / "py" / "vargento" / nombre)

    _escribir_json(salida / "datos" / "play-types.json", [t.to_dict() for t in PLAY_TYPES.values()])
    _escribir_json(salida / "datos" / "referees.json", db.list_referees())
    _escribir_json(salida / "datos" / "history.json", [asdict(p) for p in db.history()])


def exportar(salida: Path, aprobadas_dir=_POR_DEFECTO) -> None:
    """Crea una base temporal con demo + lotes aprobados y la exporta a `salida`."""
    with tempfile.TemporaryDirectory(prefix="vargento-export-") as tmp:
        db = Database(Path(tmp) / "export.db")
        try:
            seed(db)
            cargar_aprobadas(db, _carpeta_aprobadas(aprobadas_dir))
            exportar_base(db, salida)
        finally:
            db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exporta VARgento como sitio estático.")
    parser.add_argument("carpeta_salida", type=Path)
    args = parser.parse_args(argv)
    exportar(args.carpeta_salida)
    print(f"Sitio estático exportado en {args.carpeta_salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
