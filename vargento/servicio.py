"""Servicio de predicción puro: sin web ni base de datos.

Lo usa la API y también se copia al sitio estático (corre en el navegador),
por eso sólo importa de `.domain`, `.model` y la biblioteca estándar.
"""

from __future__ import annotations

from .domain import get_play_type, validate_features
from .model import HistoricalPlay, ScoredPlay, predict


def historia_desde_json(jugadas: list[dict]) -> list[HistoricalPlay]:
    """Convierte la lista plana de `history.json` en jugadas históricas."""
    return [
        HistoricalPlay(
            id=j["id"],
            play_type=j["play_type"],
            referee_id=j["referee_id"],
            features=j["features"],
            called=bool(j["called"]),
            description=j.get("description", ""),
            match=j.get("match", ""),
            date=j.get("date", ""),
        )
        for j in jugadas
    ]


def _redondear(valor: float | None, digitos: int) -> float | None:
    return round(valor, digitos) if valor is not None else None


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


def respuesta_prediccion(
    play_type: str,
    features: dict,
    referee_id: int | None,
    history: list[HistoricalPlay],
    referees: list[dict],
) -> dict:
    """Arma la respuesta de `/api/predict`.

    `history` puede traer jugadas de cualquier tipo: se filtran por `play_type`.
    Lanza ValueError si el tipo o los atributos son inválidos, y LookupError si
    `referee_id` no está en `referees`.
    """
    pt = get_play_type(play_type)
    clean = validate_features(pt, features)
    referee = None
    if referee_id is not None:
        ref = next((r for r in referees if r["id"] == referee_id), None)
        if ref is None:
            raise LookupError(f"Árbitro {referee_id} no encontrado")
        referee = {"id": ref["id"], "name": ref["name"], "country": ref["country"]}
    same_type = [p for p in history if p.play_type == pt.key]
    result = predict(pt, clean, referee_id, same_type)
    names = {r["id"]: r["name"] for r in referees}
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
            "referee_similar_probability": _redondear(result.referee_probability, 4),
            "referee_similar_effective_n": round(result.referee_effective_similar, 2),
            "peers_similar_probability": _redondear(result.peers_probability, 4),
            "referee_bias_logodds": round(result.referee_bias, 4),
            "referee_adjustment": round(result.probability - result.general_probability, 4),
            "referee_type_plays": result.referee_calls,
            "referee_type_rate": _redondear(result.referee_rate, 4),
        },
        "similar_plays": _scored(result.similar, names, 8),
        "referee_similar_plays": _scored(result.referee_similar, names, 5),
    }
