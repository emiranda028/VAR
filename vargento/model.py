"""Modelo de probabilidad de cobro.

El cálculo se hace en tres pasos, todos explicables:

1. **Similitud**: se compara la jugada nueva contra el histórico del mismo
   tipo con una distancia euclídea ponderada sobre atributos normalizados y
   un kernel gaussiano (similitud 1 = idéntica, ~0 = nada que ver).

2. **Probabilidad general**: promedio ponderado por similitud de las
   decisiones finales de las K jugadas más parecidas, suavizado hacia la
   tasa base del tipo de jugada cuando hay poca evidencia.

3. **Ajuste por árbitro**: en un vecindario más amplio de jugadas
   comparables se mide cuánto cobra el árbitro frente a sus colegas en esas
   mismas jugadas (diferencia en log-odds, suavizada con un prior bayesiano
   Beta). Ese sesgo se aplica sobre la probabilidad general. Si el árbitro
   tiene pocas jugadas comparables, casi no mueve el resultado.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields

from .domain import PlayType

MIN_SIMILARITY = 0.01  # por debajo de esto una jugada no se considera parecida
DOUBT_MARGIN = 0.10  # |p - 0.5| menor que esto => jugada dudosa


@dataclass(frozen=True)
class ModelParams:
    """Parámetros ajustables. `vargento.evaluate.calibrate` los elige con los datos cargados.

    Los valores por defecto minimizan el log-loss de validación en los datos de demo.
    """

    bandwidth: float = 0.08  # ancho del kernel para la probabilidad general
    referee_bandwidth: float = 0.25  # más ancho: el sesgo del árbitro es un rasgo general
    top_k: int = 30  # jugadas similares consideradas para la probabilidad general
    base_prior: float = 1.0  # peso (en "jugadas") de la tasa base del tipo
    referee_prior: float = 4.0  # peso (en "jugadas") del criterio de sus colegas frente al árbitro

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "ModelParams":
        if not data:
            return cls()
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


DEFAULT_PARAMS = ModelParams()


@dataclass(frozen=True)
class HistoricalPlay:
    id: int
    play_type: str
    referee_id: int
    features: dict[str, float]
    called: bool
    description: str = ""
    match: str = ""
    date: str = ""


@dataclass
class ScoredPlay:
    play: HistoricalPlay
    similarity: float


@dataclass
class Prediction:
    probability: float  # P(cobrar) final, ya ajustada por árbitro
    general_probability: float  # P(cobrar) sólo por jugadas similares
    base_rate: float  # tasa de cobro del tipo de jugada en todo el histórico
    recommendation: str  # "COBRAR" | "NO COBRAR" | "DUDOSA"
    confidence: str  # "alta" | "media" | "baja"
    effective_similar: float  # suma de similitudes (tamaño efectivo de muestra)
    referee_effective_similar: float
    referee_probability: float | None  # tasa del árbitro en comparables (sin suavizar)
    peers_probability: float | None  # tasa del resto de árbitros en esas mismas jugadas
    referee_bias: float  # sesgo del árbitro en log-odds (+ cobra más, - cobra menos)
    referee_calls: int  # jugadas del tipo dirigidas por el árbitro
    referee_rate: float | None  # tasa de cobro del árbitro en el tipo
    similar: list[ScoredPlay] = field(default_factory=list)
    referee_similar: list[ScoredPlay] = field(default_factory=list)


def distance(play_type: PlayType, a: dict[str, float], b: dict[str, float]) -> float:
    """Distancia ponderada normalizada a [0, 1]."""
    num = 0.0
    den = 0.0
    for f in play_type.features:
        d = f.normalize(a[f.key]) - f.normalize(b[f.key])
        num += f.weight * d * d
        den += f.weight
    return math.sqrt(num / den) if den else 0.0


def similarity(
    play_type: PlayType, a: dict[str, float], b: dict[str, float], bandwidth: float = DEFAULT_PARAMS.bandwidth
) -> float:
    d = distance(play_type, a, b)
    return math.exp(-(d * d) / (2 * bandwidth * bandwidth))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _weighted_rate(scored: list[ScoredPlay]) -> tuple[float, float]:
    """Devuelve (suma de similitudes, suma de similitudes de jugadas cobradas)."""
    w = sum(s.similarity for s in scored)
    wy = sum(s.similarity for s in scored if s.play.called)
    return w, wy


def _confidence(effective: float, probability: float) -> str:
    margin = abs(probability - 0.5)
    if effective >= 8 and margin >= 0.25:
        return "alta"
    if effective >= 3 and margin >= DOUBT_MARGIN:
        return "media"
    return "baja"


def predict(
    play_type: PlayType,
    features: dict[str, float],
    referee_id: int | None,
    history: list[HistoricalPlay],
    params: ModelParams = DEFAULT_PARAMS,
) -> Prediction:
    same_type = [p for p in history if p.play_type == play_type.key]

    base_rate = (sum(p.called for p in same_type) + 1) / (len(same_type) + 2)

    scored = [ScoredPlay(p, similarity(play_type, features, p.features, params.bandwidth)) for p in same_type]
    scored = [s for s in scored if s.similarity >= MIN_SIMILARITY]
    scored.sort(key=lambda s: s.similarity, reverse=True)

    top = scored[: params.top_k]
    w, wy = _weighted_rate(top)
    general = (wy + params.base_prior * base_rate) / (w + params.base_prior)

    ref_all = [p for p in same_type if referee_id is not None and p.referee_id == referee_id]
    ref_similar: list[ScoredPlay] = []
    rw = rwy = 0.0
    peers_rate: float | None = None
    bias = 0.0
    if ref_all:
        wide = [
            ScoredPlay(p, similarity(play_type, features, p.features, params.referee_bandwidth)) for p in same_type
        ]
        wide = [s for s in wide if s.similarity >= MIN_SIMILARITY]
        ref_similar = sorted(
            (s for s in wide if s.play.referee_id == referee_id), key=lambda s: s.similarity, reverse=True
        )
        peers = [s for s in wide if s.play.referee_id != referee_id]
        rw, rwy = _weighted_rate(ref_similar)
        pw, pwy = _weighted_rate(peers)
        if rw > 0:
            peers_rate = (pwy + params.base_prior * base_rate) / (pw + params.base_prior)
            ref_rate = (rwy + params.referee_prior * peers_rate) / (rw + params.referee_prior)
            bias = _logit(ref_rate) - _logit(peers_rate)
    final = _sigmoid(_logit(general) + bias)

    if final >= 0.5 + DOUBT_MARGIN:
        recommendation = "COBRAR"
    elif final <= 0.5 - DOUBT_MARGIN:
        recommendation = "NO COBRAR"
    else:
        recommendation = "DUDOSA"

    return Prediction(
        probability=final,
        general_probability=general,
        base_rate=base_rate,
        recommendation=recommendation,
        confidence=_confidence(w + rw, final),
        effective_similar=w,
        referee_effective_similar=rw,
        referee_probability=(rwy / rw) if rw > 0 else None,
        peers_probability=peers_rate,
        referee_bias=bias,
        referee_calls=len(ref_all),
        referee_rate=(sum(p.called for p in ref_all) / len(ref_all)) if ref_all else None,
        similar=top,
        referee_similar=ref_similar,
    )
