"""API HTTP de VARgento."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, auth
from . import media as mediautil
from .db import Database
from .domain import PLAY_TYPES, get_play_type, validate_features
from .evaluate import calibrate, evaluate
from .importer import import_csv, template_csv
from .model import ModelParams, ScoredPlay, predict

STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "vargento_session"
log = logging.getLogger("vargento")

Role = Literal["periodista", "editor", "admin"]


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(max_length=256)
    role: Role


class UserPatch(BaseModel):
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(default=None, max_length=256)


class RefereeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = Field(default="", max_length=60)


class PredictIn(BaseModel):
    play_type: str
    referee_id: int | None = None
    features: dict[str, float | bool]


class PlayIn(PredictIn):
    referee_id: int
    called: bool
    description: str = Field(default="", max_length=500)
    match: str = Field(default="", max_length=120)
    date: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")


class DecisionIn(BaseModel):
    called: bool
    referee_id: int | None = None
    description: str = Field(default="", max_length=500)
    match: str = Field(default="", max_length=120)
    date: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")


class MediaLinkIn(BaseModel):
    url: str = Field(max_length=2000)
    start_seconds: float | None = Field(default=None, ge=0, le=36000)
    caption: str = Field(default="", max_length=200)
    review_id: int | None = None
    play_id: int | None = None


class ImportIn(BaseModel):
    csv: str = Field(max_length=20_000_000)
    dry_run: bool = False


def _bootstrap_admin(db: Database, password: str | None) -> None:
    if db.count_users():
        return
    username = os.environ.get("VARGENTO_ADMIN_USER", "admin")
    password = password or os.environ.get("VARGENTO_ADMIN_PASSWORD")
    generated = not password
    if generated:
        password = auth.generate_password()
    auth.validate_password(password)
    db.add_user(username, auth.hash_password(password), "admin")
    if generated:
        # Se muestra una sola vez; conviene cambiarla al entrar.
        msg = f"Usuario administrador creado: {username} / contraseña: {password}"
        log.warning(msg)
        print(msg, flush=True)


def _request_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE)


def create_app(
    db: Database | None = None,
    seed_demo: bool | None = None,
    admin_password: str | None = None,
    media_dir: str | Path | None = None,
) -> FastAPI:
    if db is None:
        db = Database(os.environ.get("VARGENTO_DB", "vargento.db"))
    media_dir = Path(media_dir or os.environ.get("VARGENTO_MEDIA_DIR", "media"))
    media_dir.mkdir(parents=True, exist_ok=True)
    if seed_demo is None:
        seed_demo = os.environ.get("VARGENTO_SEED_DEMO", "1") == "1"
    if seed_demo and db.count_plays() == 0 and not db.list_referees():
        from .seed import seed

        seed(db)
    _bootstrap_admin(db, admin_password)

    secure_cookies = os.environ.get("VARGENTO_SECURE_COOKIES", "0") == "1"
    throttle = auth.LoginThrottle()

    app = FastAPI(title="VARgento", version=__version__)
    app.state.db = db
    app.state.params = db.get_model_params()

    # --- autenticación --------------------------------------------------
    def current_user(request: Request) -> dict:
        token = _request_token(request)
        user = db.session_user(auth.token_hash(token)) if token else None
        if not user:
            raise HTTPException(401, "Sesión no iniciada o vencida")
        return user

    def require(permission: str):
        def dep(user: dict = Depends(current_user)) -> dict:
            if not auth.can(user["role"], permission):
                raise HTTPException(403, "Tu rol no tiene permiso para esta acción")
            return user

        return dep

    def _public_user(user: dict) -> dict:
        return {**user, "permissions": auth.permissions_for(user["role"]), "role_label": auth.ROLES[user["role"]]}

    @app.post("/api/auth/login")
    def login(body: LoginIn, response: Response) -> dict:
        if throttle.locked(body.username):
            raise HTTPException(429, "Demasiados intentos fallidos. Probá de nuevo en unos minutos.")
        cred = db.get_user_credentials(body.username)
        ok = auth.verify_password(body.password, cred["password_hash"] if cred else auth.DUMMY_HASH)
        if not (cred and ok and cred["active"]):
            throttle.failure(body.username)
            raise HTTPException(401, "Usuario o contraseña incorrectos")
        throttle.success(body.username)
        token = auth.new_token()
        db.create_session(auth.token_hash(token), cred["id"], auth.session_expiry())
        response.set_cookie(
            COOKIE, token, max_age=auth.SESSION_HOURS * 3600, httponly=True, samesite="strict", secure=secure_cookies
        )
        return {"user": _public_user(db.get_user(cred["id"])), "token": token}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, _: dict = Depends(current_user)) -> dict:
        db.delete_session(auth.token_hash(_request_token(request)))
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return _public_user(user)

    @app.post("/api/auth/password")
    def change_password(body: PasswordIn, user: dict = Depends(current_user)) -> dict:
        cred = db.get_user_credentials(user["username"])
        if not auth.verify_password(body.current, cred["password_hash"]):
            raise HTTPException(400, "La contraseña actual no es correcta")
        _check_password(body.new)
        db.update_user(user["id"], password_hash=auth.hash_password(body.new))
        return {"ok": True, "detail": "Contraseña actualizada. Volvé a iniciar sesión."}

    # --- helpers --------------------------------------------------------
    def _check_password(password: str) -> None:
        try:
            auth.validate_password(password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

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

    def _media_by_play(play_ids: list[int]) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for m in db.media_for(play_ids=play_ids):
            out.setdefault(m["play_id"], []).append(mediautil.public(m))
        return out

    def _scored(items: list[ScoredPlay], names: dict[int, str], limit: int) -> list[dict]:
        items = items[:limit]
        media = _media_by_play([s.play.id for s in items])
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
                "media": media.get(s.play.id, []),
            }
            for s in items
        ]

    # --- catálogo, árbitros y jugadas -----------------------------------
    @app.get("/api/play-types")
    def play_types(_: dict = Depends(require("view"))) -> list[dict]:
        return [t.to_dict() for t in PLAY_TYPES.values()]

    @app.get("/api/referees")
    def referees(_: dict = Depends(require("view"))) -> list[dict]:
        return db.list_referees()

    @app.post("/api/referees", status_code=201)
    def create_referee(body: RefereeIn, _: dict = Depends(require("manage_data"))) -> dict:
        try:
            return db.add_referee(body.name, body.country)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Ya existe un árbitro con ese nombre") from exc

    @app.get("/api/referees/{referee_id}/stats")
    def referee_stats(referee_id: int, _: dict = Depends(require("view"))) -> dict:
        ref = _referee_or_404(referee_id)
        return {**ref, "by_type": db.referee_stats(referee_id)}

    @app.get("/api/plays")
    def plays(limit: int = 50, referee_id: int | None = None, _: dict = Depends(require("view"))) -> list[dict]:
        return db.list_plays(limit=min(max(limit, 1), 500), referee_id=referee_id)

    @app.post("/api/plays", status_code=201)
    def create_play(body: PlayIn, _: dict = Depends(require("manage_data"))) -> dict:
        pt, features = _check(body.play_type, body.features)
        _referee_or_404(body.referee_id)
        play_id = db.add_play(
            pt.key, body.referee_id, features, body.called, body.description, body.match, body.date
        )
        return {"id": play_id}

    @app.get("/api/plays/template")
    def plays_template(_: dict = Depends(require("view"))) -> PlainTextResponse:
        return PlainTextResponse(
            template_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="vargento_plantilla.csv"'},
        )

    @app.get("/api/plays/{play_id:int}")
    def play_detail(play_id: int, _: dict = Depends(require("view"))) -> dict:
        play = db.get_play(play_id)
        if not play:
            raise HTTPException(404, "Jugada no encontrada")
        return {**play, "media": _media_by_play([play_id]).get(play_id, [])}

    @app.post("/api/plays/import")
    def plays_import(body: ImportIn, _: dict = Depends(require("manage_data"))) -> dict:
        return import_csv(db, body.csv, dry_run=body.dry_run)

    # --- predicción y revisiones ----------------------------------------
    @app.post("/api/predict")
    def predict_play(body: PredictIn, user: dict = Depends(require("predict"))) -> dict:
        pt, features = _check(body.play_type, body.features)
        referee_id = body.referee_id
        referee = _referee_or_404(referee_id) if referee_id is not None else None
        result = predict(pt, features, referee_id, db.history(pt.key), app.state.params)
        review_id = db.add_review(
            user["id"], referee_id, pt.key, features, result.probability, result.recommendation
        )
        names = {r["id"]: r["name"] for r in db.list_referees()}
        return {
            "review_id": review_id,
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

    @app.get("/api/reviews/{review_id:int}")
    def review_detail(review_id: int, _: dict = Depends(require("view"))) -> dict:
        review = db.get_review(review_id)
        if not review:
            raise HTTPException(404, "Revisión no encontrada")
        return {**review, "media": [mediautil.public(m) for m in db.media_for(review_id=review_id)]}

    @app.post("/api/reviews/{review_id}/decision")
    def decide(review_id: int, body: DecisionIn, user: dict = Depends(require("decide"))) -> dict:
        review = db.get_review(review_id)
        if not review:
            raise HTTPException(404, "Revisión no encontrada")
        if review["final_called"] is not None:
            raise HTTPException(409, "Esta revisión ya tiene decisión final")
        referee_id = body.referee_id or review["referee_id"]
        if referee_id is None:
            raise HTTPException(422, "Indicá qué árbitro tomó la decisión")
        _referee_or_404(referee_id)
        decided = db.decide_review(
            review_id, body.called, user["id"], referee_id, body.description, body.match, body.date
        )
        if decided is None:
            raise HTTPException(409, "Esta revisión ya tiene decisión final")
        return decided

    @app.get("/api/reviews")
    def reviews(limit: int = 50, _: dict = Depends(require("view"))) -> list[dict]:
        return db.list_reviews(min(max(limit, 1), 500))

    @app.get("/api/reviews/stats")
    def reviews_stats(_: dict = Depends(require("view"))) -> dict:
        outcomes = db.review_outcomes()
        n = len(outcomes)
        decisive = [(p, y) for p, y, rec in outcomes if rec != "DUDOSA"]
        return {
            "decided": n,
            "pending": db.count_pending_reviews(),
            "accuracy": (sum((p >= 0.5) == y for p, y, _ in outcomes) / n) if n else None,
            "decisive": len(decisive),
            "decisive_accuracy": (
                sum((p >= 0.5) == y for p, y in decisive) / len(decisive) if decisive else None
            ),
            "brier": (sum((p - y) ** 2 for p, y, _ in outcomes) / n) if n else None,
        }

    # --- videos y fotos ------------------------------------------------
    def _media_target(user: dict, review_id: int | None, play_id: int | None) -> tuple[int | None, int | None]:
        """Valida a qué se adjunta el medio y si el usuario puede hacerlo."""
        if (review_id is None) == (play_id is None):
            raise HTTPException(422, "Indicá review_id o play_id (uno solo)")
        if review_id is not None:
            review = db.get_review(review_id)
            if not review:
                raise HTTPException(404, "Revisión no encontrada")
            if review["user_id"] != user["id"] and not auth.can(user["role"], "manage_data"):
                raise HTTPException(403, "Sólo quien creó la revisión puede adjuntarle videos")
            if review["play_id"]:  # ya decidida: el medio va a la jugada del histórico
                return None, review["play_id"]
            return review_id, None
        if not auth.can(user["role"], "manage_data"):
            raise HTTPException(403, "Tu rol no puede adjuntar videos a jugadas del histórico")
        if not db.get_play(play_id):
            raise HTTPException(404, "Jugada no encontrada")
        return None, play_id

    @app.post("/api/media/link", status_code=201)
    def media_link(body: MediaLinkIn, user: dict = Depends(require("predict"))) -> dict:
        review_id, play_id = _media_target(user, body.review_id, body.play_id)
        try:
            url = mediautil.validate_url(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        start = body.start_seconds if body.start_seconds is not None else mediautil.start_from_url(url)
        item = db.add_media("link", user["id"], play_id, review_id, url=url, start_seconds=start, caption=body.caption)
        return mediautil.public(item)

    @app.post("/api/media/upload", status_code=201)
    async def media_upload(
        request: Request,
        review_id: int | None = None,
        play_id: int | None = None,
        caption: str = "",
        start_seconds: float = 0,
        user: dict = Depends(require("predict")),
    ) -> dict:
        review_id, play_id = _media_target(user, review_id, play_id)
        mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if mime not in mediautil.ALLOWED_MIME:
            raise HTTPException(415, "Formato no admitido. Usá JPG, PNG, WEBP, GIF, MP4, WEBM o MOV.")
        declared = int(request.headers.get("content-length") or 0)
        if declared > mediautil.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "El archivo supera los 300 MB")
        name = mediautil.new_filename(mime)
        size = 0
        with tempfile.NamedTemporaryFile(dir=media_dir, delete=False, suffix=".part") as tmp:
            try:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > mediautil.MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "El archivo supera los 300 MB")
                    tmp.write(chunk)
            except BaseException:
                tmp.close()
                os.unlink(tmp.name)
                raise
        if size == 0:
            os.unlink(tmp.name)
            raise HTTPException(422, "El archivo está vacío")
        os.replace(tmp.name, mediautil.safe_path(media_dir, name))
        item = db.add_media(
            "file", user["id"], play_id, review_id, mime=mime, path=name,
            start_seconds=max(0.0, start_seconds), caption=caption[:200],
        )
        return mediautil.public(item)

    @app.get("/api/media/{media_id}/file")
    def media_file(media_id: int, _: dict = Depends(require("view"))) -> FileResponse:
        item = db.get_media(media_id)
        if not item or item["kind"] != "file":
            raise HTTPException(404, "Archivo no encontrado")
        path = mediautil.safe_path(media_dir, item["path"])
        if not path.exists():
            raise HTTPException(404, "Archivo no encontrado")
        return FileResponse(
            path,
            media_type=item["mime"],
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
            content_disposition_type="inline",
        )

    @app.delete("/api/media/{media_id}")
    def media_delete(media_id: int, user: dict = Depends(require("predict"))) -> dict:
        item = db.get_media(media_id)
        if not item:
            raise HTTPException(404, "Archivo no encontrado")
        if item["created_by"] != user["id"] and not auth.can(user["role"], "manage_data"):
            raise HTTPException(403, "Sólo quien lo subió o un editor puede borrarlo")
        db.delete_media(media_id)
        if item["kind"] == "file":
            mediautil.safe_path(media_dir, item["path"]).unlink(missing_ok=True)
        return {"ok": True}

    # --- modelo ---------------------------------------------------------
    @app.get("/api/model")
    def model_info(_: dict = Depends(require("view"))) -> dict:
        return {"params": app.state.params.to_dict(), "plays": db.count_plays()}

    @app.post("/api/model/evaluate")
    def model_evaluate(_: dict = Depends(require("manage_data"))) -> dict:
        return evaluate(db.history(), app.state.params)

    @app.post("/api/model/calibrate")
    def model_calibrate(_: dict = Depends(require("manage_data"))) -> dict:
        history = db.history()
        if len(history) < 50:
            raise HTTPException(422, "Se necesitan al menos 50 jugadas para recalibrar")
        before = evaluate(history, app.state.params)
        result = calibrate(history, app.state.params)
        params = ModelParams.from_dict(result["best"])
        after = evaluate(history, params)
        applied = after["log_loss"] <= before["log_loss"]
        if applied:
            db.set_model_params(params)
            app.state.params = params
        return {"before": before, "after": after, "applied": applied, "trials": result["trials"]}

    # --- usuarios -------------------------------------------------------
    @app.get("/api/users")
    def users(_: dict = Depends(require("manage_users"))) -> list[dict]:
        return db.list_users()

    @app.post("/api/users", status_code=201)
    def create_user(body: UserIn, _: dict = Depends(require("manage_users"))) -> dict:
        _check_password(body.password)
        try:
            return db.add_user(body.username, auth.hash_password(body.password), body.role)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Ese nombre de usuario ya existe") from exc

    @app.patch("/api/users/{user_id}")
    def update_user(user_id: int, body: UserPatch, admin: dict = Depends(require("manage_users"))) -> dict:
        if not db.get_user(user_id):
            raise HTTPException(404, "Usuario no encontrado")
        if user_id == admin["id"] and (body.active is False or (body.role and body.role != "admin")):
            raise HTTPException(400, "No podés desactivarte ni quitarte el rol de administrador")
        changes: dict = {}
        if body.role:
            changes["role"] = body.role
        if body.active is not None:
            changes["active"] = int(body.active)
        if body.password:
            _check_password(body.password)
            changes["password_hash"] = auth.hash_password(body.password)
        return db.update_user(user_id, **changes)

    # --- varios ---------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
