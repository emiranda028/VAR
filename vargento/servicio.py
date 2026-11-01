"""Servicio de predicción puro: sin web ni base de datos.

Lo usa la API y también se copia al sitio estático (corre en el navegador),
por eso sólo importa de `.domain`, `.model` y la biblioteca estándar.
"""

from __future__ import annotations

from .domain import PlayType, get_play_type, validate_features
from .model import REFEREE_BANDWIDTH, HistoricalPlay, ScoredPlay, distance, predict, similarity

# Igual que en importar.py (que no se puede importar acá: no corre en el navegador).
SEPARADOR_FUENTES = "| Fuentes:"
MAX_REALES = 5


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
            origen=j.get("origen", "demo"),
            video=j.get("video"),
        )
        for j in jugadas
    ]


def separar_fuentes(description: str) -> tuple[str, list[str]]:
    """Separa "texto | Fuentes: url1 url2" en el texto y la lista de URLs http(s)."""
    texto, sep, resto = description.partition(SEPARADOR_FUENTES)
    if not sep and description.startswith(SEPARADOR_FUENTES[2:]):  # jugada sin texto
        texto, resto = "", description[len(SEPARADOR_FUENTES) - 2:]
    fuentes = [u for u in resto.split() if u.startswith(("http://", "https://"))]
    return texto.strip(" |"), fuentes


def _video(video: dict | None) -> dict | None:
    if not video:
        return None
    return {"id": video["url"].rsplit("v=", 1)[-1], "url": video["url"], "inicio": video["inicio"]}


def _jugada_real(pt: PlayType, features: dict, p: HistoricalPlay, names: dict[int, str]) -> dict:
    descripcion, fuentes = separar_fuentes(p.description)
    return {
        "id": p.id,
        "similitud": round(similarity(pt, features, p.features, REFEREE_BANDWIDTH), 3),
        "match": p.match,
        "date": p.date,
        "referee_id": p.referee_id,
        "referee": names.get(p.referee_id, "?"),
        "decision_campo": bool(p.features.get("decision_campo")),
        "called": p.called,
        "descripcion": descripcion,
        "fuentes": fuentes,
        "video": _video(p.video),
    }


def reales_cercanas(
    pt: PlayType, features: dict, plays: list[HistoricalPlay], names: dict[int, str]
) -> list[dict]:
    """Las jugadas reales más cercanas (distancia ascendente, desempate por id), sin umbral."""
    reales = [p for p in plays if p.origen == "real"]
    reales.sort(key=lambda p: (distance(pt, features, p.features), p.id))
    return [_jugada_real(pt, features, p, names) for p in reales[:MAX_REALES]]


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
        "jugadas_reales_similares": reales_cercanas(pt, clean, same_type, names),
        "jugadas_reales_arbitro": (
            reales_cercanas(pt, clean, [p for p in same_type if p.referee_id == referee_id], names)
            if referee_id is not None
            else []
        ),
    }
