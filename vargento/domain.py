"""Catálogo de tipos de jugada revisables por el VAR y sus atributos.

Cada tipo de jugada define los atributos que el árbitro (o el operador VAR)
carga al revisarla. Esos mismos atributos se usan para medir la similitud
entre la jugada actual y las jugadas históricas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["bool", "scale", "number"]


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    kind: Kind
    # Rango usado para normalizar la distancia entre jugadas (0..1).
    min: float = 0.0
    max: float = 1.0
    # Importancia relativa del atributo al comparar jugadas.
    weight: float = 1.0
    help: str = ""
    # Etiquetas para escalas discretas (kind == "scale").
    options: tuple[str, ...] = ()

    def normalize(self, value: float) -> float:
        span = self.max - self.min
        if span <= 0:
            return 0.0
        v = (float(value) - self.min) / span
        return min(1.0, max(0.0, v))

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "min": self.min,
            "max": self.max,
            "help": self.help,
            "options": list(self.options),
        }


@dataclass(frozen=True)
class PlayType:
    key: str
    label: str
    question: str
    features: tuple[Feature, ...] = field(default_factory=tuple)

    def feature(self, key: str) -> Feature:
        for f in self.features:
            if f.key == key:
                return f
        raise KeyError(key)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "question": self.question,
            "features": [f.to_dict() for f in self.features],
        }


INTENSIDAD = ("Nula", "Leve", "Moderada", "Fuerte")

# La decisión tomada en el campo pesa mucho: el protocolo VAR sólo corrige
# errores "claros y obvios", por lo que los árbitros tienden a sostenerla.
DECISION_CAMPO = Feature(
    "decision_campo",
    "¿Se cobró en el campo?",
    "bool",
    weight=1.5,
    help="Decisión original del árbitro antes de la revisión.",
)

PLAY_TYPES: dict[str, PlayType] = {
    t.key: t
    for t in (
        PlayType(
            "penal",
            "Penal por falta",
            "¿Cobrar penal?",
            (
                DECISION_CAMPO,
                Feature("intensidad_contacto", "Intensidad del contacto", "scale", 0, 3, 2.0, options=INTENSIDAD),
                Feature("toca_pelota_primero", "El defensor toca primero la pelota", "bool", weight=2.0),
                Feature("intento_jugar_balon", "El defensor intenta jugar la pelota", "bool", weight=1.0),
                Feature("indicios_simulacion", "Indicios de simulación / exageración", "bool", weight=1.5),
                Feature("contacto_dentro_area", "El contacto es dentro del área", "bool", weight=2.5),
            ),
        ),
        PlayType(
            "mano",
            "Mano en el área",
            "¿Cobrar mano?",
            (
                DECISION_CAMPO,
                Feature("brazo_antinatural", "Brazo en posición antinatural (agranda el cuerpo)", "bool", weight=2.5),
                Feature("movimiento_hacia_pelota", "Movimiento del brazo hacia la pelota", "bool", weight=2.0),
                Feature("rebote_propio", "Rebote en el propio cuerpo", "bool", weight=1.5),
                Feature("distancia_m", "Distancia del remate (m)", "number", 0, 20, 1.0),
                Feature("brazo_de_apoyo", "Brazo de apoyo al caer", "bool", weight=1.5),
            ),
        ),
        PlayType(
            "offside",
            "Fuera de juego",
            "¿Cobrar offside?",
            (
                DECISION_CAMPO,
                Feature(
                    "distancia_cm",
                    "Distancia adelantado (cm, negativo = habilitado)",
                    "number",
                    -50,
                    50,
                    3.0,
                    help="Medida de la línea del VAR.",
                ),
                Feature("interfiere_juego", "Interfiere en el juego / con un rival", "bool", weight=2.0),
                Feature("jugada_deliberada_rival", "Jugada deliberada de un rival", "bool", weight=1.5),
            ),
        ),
        PlayType(
            "roja",
            "Roja directa",
            "¿Mostrar roja?",
            (
                DECISION_CAMPO,
                Feature("intensidad", "Intensidad / fuerza excesiva", "scale", 0, 3, 2.5, options=INTENSIDAD),
                Feature("con_tapones", "Entrada con plantilla / tapones", "bool", weight=2.0),
                Feature("pierna_extendida", "Pierna extendida / sin control", "bool", weight=1.5),
                Feature("dogso", "Oportunidad manifiesta de gol (DOGSO)", "bool", weight=2.0),
                Feature("disputa_pelota", "Hay disputa real de la pelota", "bool", weight=1.0),
            ),
        ),
    )
}


def get_play_type(key: str) -> PlayType:
    try:
        return PLAY_TYPES[key]
    except KeyError as exc:
        raise ValueError(f"Tipo de jugada desconocido: {key!r}") from exc


def validate_features(play_type: PlayType, features: dict[str, float]) -> dict[str, float]:
    """Valida y normaliza el diccionario de atributos de una jugada."""
    clean: dict[str, float] = {}
    for f in play_type.features:
        if f.key not in features or features[f.key] is None:
            raise ValueError(f"Falta el atributo {f.key!r} para {play_type.key}")
        raw = features[f.key]
        if f.kind == "bool":
            if isinstance(raw, str):
                raw = raw.lower() in ("1", "true", "si", "sí")
            clean[f.key] = 1.0 if raw else 0.0
        else:
            value = float(raw)
            if not (f.min <= value <= f.max):
                raise ValueError(f"{f.key} fuera de rango [{f.min}, {f.max}]: {value}")
            clean[f.key] = round(value) if f.kind == "scale" else value
    unknown = set(features) - {f.key for f in play_type.features}
    if unknown:
        raise ValueError(f"Atributos desconocidos para {play_type.key}: {sorted(unknown)}")
    return clean
