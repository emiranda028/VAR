"""Genera un histórico SINTÉTICO para probar la app.

Los árbitros y partidos son ficticios. Las decisiones se generan con un
modelo logístico por tipo de jugada más un sesgo propio de cada árbitro
(más estricto o más permisivo), para que el ajuste por árbitro tenga algo
que detectar. Reemplazar por datos reales antes de cualquier uso serio.
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import date, timedelta

from .db import Database
from .domain import PLAY_TYPES

REFEREES = [
    # (nombre ficticio, país, sesgo en logit: + estricto / - permisivo)
    ("Árbitro Demo A", "Argentina", 0.9),
    ("Árbitro Demo B", "Argentina", -0.8),
    ("Árbitro Demo C", "Uruguay", 0.3),
    ("Árbitro Demo D", "Chile", -0.3),
    ("Árbitro Demo E", "Brasil", 0.0),
    ("Árbitro Demo F", "Paraguay", 0.5),
    ("Árbitro Demo G", "Colombia", -0.5),
    ("Árbitro Demo H", "Perú", 0.1),
]

TEAMS = ["Boca", "River", "Racing", "Independiente", "San Lorenzo", "Vélez", "Estudiantes", "Talleres"]


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _random_features(rng: random.Random, play_type: str) -> dict[str, float]:
    b = lambda p: 1.0 if rng.random() < p else 0.0  # noqa: E731
    if play_type == "penal":
        return {
            "decision_campo": b(0.45),
            "intensidad_contacto": float(rng.choices([0, 1, 2, 3], [0.15, 0.35, 0.3, 0.2])[0]),
            "toca_pelota_primero": b(0.25),
            "intento_jugar_balon": b(0.6),
            "indicios_simulacion": b(0.2),
            "contacto_dentro_area": b(0.85),
        }
    if play_type == "mano":
        return {
            "decision_campo": b(0.4),
            "brazo_antinatural": b(0.45),
            "movimiento_hacia_pelota": b(0.3),
            "rebote_propio": b(0.25),
            "distancia_m": round(rng.uniform(0.5, 20), 1),
            "brazo_de_apoyo": b(0.15),
        }
    if play_type == "offside":
        return {
            "decision_campo": b(0.35),
            "distancia_cm": round(max(-50.0, min(50.0, rng.gauss(3, 18))), 1),
            "interfiere_juego": b(0.75),
            "jugada_deliberada_rival": b(0.1),
        }
    if play_type == "roja":
        return {
            "decision_campo": b(0.3),
            "intensidad": float(rng.choices([0, 1, 2, 3], [0.1, 0.35, 0.35, 0.2])[0]),
            "con_tapones": b(0.35),
            "pierna_extendida": b(0.4),
            "dogso": b(0.2),
            "disputa_pelota": b(0.6),
        }
    raise ValueError(play_type)


def _true_logit(play_type: str, f: dict[str, float]) -> float:
    field = 1.2 * f["decision_campo"] - 0.6  # peso de la decisión de campo
    if play_type == "penal":
        return (
            field
            + 1.3 * f["intensidad_contacto"]
            - 2.5 * f["toca_pelota_primero"]
            - 0.4 * f["intento_jugar_balon"]
            - 2.0 * f["indicios_simulacion"]
            + 3.0 * f["contacto_dentro_area"]
            - 3.5
        )
    if play_type == "mano":
        return (
            field
            + 2.2 * f["brazo_antinatural"]
            + 2.0 * f["movimiento_hacia_pelota"]
            - 2.0 * f["rebote_propio"]
            + 0.12 * f["distancia_m"]
            - 1.8 * f["brazo_de_apoyo"]
            - 2.0
        )
    if play_type == "offside":
        return field + 0.35 * f["distancia_cm"] + 2.5 * f["interfiere_juego"] - 3.0 * f["jugada_deliberada_rival"] - 1.5
    if play_type == "roja":
        return (
            field
            + 1.2 * f["intensidad"]
            + 1.5 * f["con_tapones"]
            + 1.0 * f["pierna_extendida"]
            + 2.5 * f["dogso"]
            - 0.8 * f["disputa_pelota"]
            - 4.0
        )
    raise ValueError(play_type)


def _describe(play_type: str, f: dict[str, float]) -> str:
    if play_type == "offside":
        cm = f["distancia_cm"]
        pos = f"adelantado {cm:.0f} cm" if cm > 0 else f"habilitado por {abs(cm):.0f} cm"
        return f"Posición dudosa: {pos}" + ("" if f["interfiere_juego"] else ", sin interferir")
    if play_type == "penal":
        return f"Contacto {('nulo', 'leve', 'moderado', 'fuerte')[int(f['intensidad_contacto'])]} en el área" + (
            ", defensor toca pelota" if f["toca_pelota_primero"] else ""
        )
    if play_type == "mano":
        return (
            "Mano " + ("con brazo abierto" if f["brazo_antinatural"] else "con brazo pegado")
            + f" a {f['distancia_m']:.0f} m"
        )
    return f"Entrada de intensidad {('nula', 'leve', 'moderada', 'fuerte')[int(f['intensidad'])]}" + (
        " con plancha" if f["con_tapones"] else ""
    )


def seed(db: Database, n_plays: int = 1600, rng_seed: int = 42) -> None:
    rng = random.Random(rng_seed)
    refs = [(db.add_referee(name, country)["id"], bias) for name, country, bias in REFEREES]
    start = date(2023, 1, 1)
    types = list(PLAY_TYPES)
    rows = []
    for _ in range(n_plays):
        play_type = rng.choice(types)
        ref_id, bias = rng.choice(refs)
        f = _random_features(rng, play_type)
        called = rng.random() < _sigmoid(_true_logit(play_type, f) + bias)
        home, away = rng.sample(TEAMS, 2)
        day = start + timedelta(days=rng.randrange(0, 1000))
        rows.append((play_type, ref_id, f, called, _describe(play_type, f), f"{home} vs {away}", day.isoformat()))
    db.add_plays(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga datos de demo en VARgento")
    parser.add_argument("--db", default="vargento.db")
    parser.add_argument("--plays", type=int, default=1600)
    args = parser.parse_args()
    db = Database(args.db)
    if db.count_plays():
        print("La base ya tiene jugadas; no se cargan datos de demo.")
        return
    seed(db, args.plays)
    print(f"Cargadas {db.count_plays()} jugadas de demo en {args.db}")


if __name__ == "__main__":
    main()
