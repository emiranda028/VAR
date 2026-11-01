"""API HTTP de VARgento."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .db import Database
from .domain import PLAY_TYPES, get_play_type, validate_features
from .servicio import respuesta_prediccion

STATIC_DIR = Path(__file__).parent / "static"
APROBADAS_DIR = Path(__file__).parent.parent / "data" / "aprobadas"
_POR_DEFECTO = object()  # distingue "no se pasó aprobadas_dir" de None (desactivado)
log = logging.getLogger("vargento")


class RefereeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = ""


class PredictIn(BaseModel):
    play_type: str
    referee_id: int | None = None
    features: dict[str, float | bool]


class PlayIn(PredictIn):
    referee_id: int
    called: bool
    description: str = ""
    match: str = ""
    date: str = ""


def _carpeta_aprobadas(aprobadas_dir) -> Path | None:
    if aprobadas_dir is _POR_DEFECTO:
        aprobadas_dir = os.environ.get("VARGENTO_APROBADAS", APROBADAS_DIR) or None
    return Path(aprobadas_dir) if aprobadas_dir else None


def cargar_aprobadas(db: Database, carpeta: Path | None) -> None:
    """Carga cada lote aprobado (*.jsonl) en orden alfabético; las duplicadas se omiten solas."""
    if carpeta is None or not carpeta.is_dir():
        return
    from .importar import importar

    for archivo in sorted(carpeta.glob("*.jsonl")):
        try:
            lineas = archivo.read_text(encoding="utf-8-sig").splitlines()
            res = importar(db, lineas, aplicar=True, incluir_baja=True)
            if res.errores:
                log.warning("Lote %s con errores: %s", archivo.name, "; ".join(res.errores))
        except Exception as exc:  # un lote roto no debe impedir el arranque
            log.warning("No se pudo cargar el lote %s: %s", archivo.name, exc)


def create_app(
    db: Database | None = None, seed_demo: bool | None = None, aprobadas_dir=_POR_DEFECTO
) -> FastAPI:
    if db is None:
        db = Database(os.environ.get("VARGENTO_DB", "vargento.db"))
    if seed_demo is None:
        seed_demo = os.environ.get("VARGENTO_SEED_DEMO", "1") == "1"
    if seed_demo and db.count_plays() == 0 and not db.list_referees():
        from .seed import seed

        seed(db)
    cargar_aprobadas(db, _carpeta_aprobadas(aprobadas_dir))

    app = FastAPI(title="VARgento", version=__version__)
    app.state.db = db

    def _check(play_type: str, features: dict) -> tuple:
        try:
            pt = get_play_type(play_type)
            return pt, validate_features(pt, features)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def _referee_or_404(referee_id: int) -> dict:
        ref = db.get_referee(referee_id)
        if not ref:
            raise HTTPException(404, f"Árbitro {referee_id} no encontrado")
        return ref

    @app.get("/api/play-types")
    def play_types() -> list[dict]:
        return [t.to_dict() for t in PLAY_TYPES.values()]

    @app.get("/api/referees")
    def referees() -> list[dict]:
        return db.list_referees()

    @app.post("/api/referees", status_code=201)
    def create_referee(body: RefereeIn) -> dict:
        try:
            return db.add_referee(body.name, body.country)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Ya existe un árbitro con ese nombre") from exc

    @app.get("/api/referees/{referee_id}/stats")
    def referee_stats(referee_id: int) -> dict:
        ref = _referee_or_404(referee_id)
        return {**ref, "by_type": db.referee_stats(referee_id)}

    @app.get("/api/plays")
    def plays(limit: int = 50, referee_id: int | None = None) -> list[dict]:
        return db.list_plays(limit=min(max(limit, 1), 500), referee_id=referee_id)

    @app.post("/api/plays", status_code=201)
    def create_play(body: PlayIn) -> dict:
        pt, features = _check(body.play_type, body.features)
        _referee_or_404(body.referee_id)
        play_id = db.add_play(
            pt.key, body.referee_id, features, body.called, body.description, body.match, body.date
        )
        return {"id": play_id}

    @app.post("/api/predict")
    def predict_play(body: PredictIn) -> dict:
        try:
            return respuesta_prediccion(
                body.play_type, body.features, body.referee_id, db.history(body.play_type), db.list_referees()
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "plays": db.count_plays(),
            "plays_demo": db.count_plays("demo"),
            "plays_real": db.count_plays("real"),
            "referees": db.count_referees(),
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app

