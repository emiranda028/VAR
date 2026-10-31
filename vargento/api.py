"""API HTTP de VARgento."""

from __future__ import annotations

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
from .model import ScoredPlay, predict

STATIC_DIR = Path(__file__).parent / "static"


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


def create_app(db: Database | None = None, seed_demo: bool | None = None) -> FastAPI:
    if db is None:
        db = Database(os.environ.get("VARGENTO_DB", "vargento.db"))
    if seed_demo is None:
        seed_demo = os.environ.get("VARGENTO_SEED_DEMO", "1") == "1"
    if seed_demo and db.count_plays() == 0 and not db.list_referees():
        from .seed import seed

        seed(db)

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

    def _scored(items: list[ScoredPlay], names: dict[int, str], limit: int) -> list[dict]:
        return [
            {
                "id": s.play.id,
                "similarity": round(s.similarity, 3),
                "called": s.play.called,
                "referee_id": s.play.referee_id,
                "referee": names.get(s.play.referee_id, "?"),
                "description": s.play.description,
                "match": s.play.match,
                "date": s.play.date,
                "features": s.play.features,
            }
            for s in items[:limit]
        ]

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
        pt, features = _check(body.play_type, body.features)
        referee = _referee_or_404(body.referee_id) if body.referee_id is not None else None
        result = predict(pt, features, body.referee_id, db.history(pt.key))
        names = {r["id"]: r["name"] for r in db.list_referees()}
        return {
            "play_type": pt.key,
            "question": pt.question,
            "referee": referee,
            "probability_call": round(result.probability, 4),
            "probability_no_call": round(1 - result.probability, 4),
            "recommendation": result.recommendation,
            "confidence": result.confidence,
            "components": {
                "base_rate": round(result.base_rate, 4),
                "similar_plays_probability": round(result.general_probability, 4),
                "similar_plays_effective_n": round(result.effective_similar, 2),
                "referee_similar_probability": (
                    round(result.referee_probability, 4) if result.referee_probability is not None else None
                ),
                "referee_similar_effective_n": round(result.referee_effective_similar, 2),
                "peers_similar_probability": (
                    round(result.peers_probability, 4) if result.peers_probability is not None else None
                ),
                "referee_bias_logodds": round(result.referee_bias, 4),
                "referee_adjustment": round(result.probability - result.general_probability, 4),
                "referee_type_plays": result.referee_calls,
                "referee_type_rate": (
                    round(result.referee_rate, 4) if result.referee_rate is not None else None
                ),
            },
            "similar_plays": _scored(result.similar, names, 8),
            "referee_similar_plays": _scored(result.referee_similar, names, 5),
        }

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "plays": db.count_plays(),
            "referees": db.count_referees(),
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app

