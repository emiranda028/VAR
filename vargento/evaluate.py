"""Evaluación y calibración del modelo con los datos cargados.

Usa validación *leave-one-out*: cada jugada evaluada se predice con todo el
histórico menos ella misma, como si fuera una revisión nueva. Así se mide qué
tan bien anda el modelo con los datos reales antes de confiar en él.

Uso por consola:
    python -m vargento.evaluate [--db vargento.db] [--calibrar]
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
from dataclasses import replace

from .domain import get_play_type
from .model import DEFAULT_PARAMS, HistoricalPlay, ModelParams, predict

MAX_SAMPLES = 400  # jugadas evaluadas (muestra) para acotar el tiempo de cálculo
CALIBRATION_BINS = 10

GRID = {
    "bandwidth": (0.06, 0.08, 0.12, 0.18),
    "referee_bandwidth": (0.15, 0.25, 0.4),
}


def _clip(p: float) -> float:
    return min(max(p, 1e-4), 1 - 1e-4)


def _log_loss(p: float, y: bool) -> float:
    p = _clip(p)
    return -math.log(p if y else 1 - p)


def _sample(history: list[HistoricalPlay], max_samples: int, seed: int) -> list[HistoricalPlay]:
    if len(history) <= max_samples:
        return list(history)
    return random.Random(seed).sample(history, max_samples)


def evaluate(
    history: list[HistoricalPlay],
    params: ModelParams = DEFAULT_PARAMS,
    max_samples: int = MAX_SAMPLES,
    seed: int = 0,
) -> dict:
    """Métricas leave-one-out sobre una muestra del histórico."""
    sample = _sample(history, max_samples, seed)
    by_type: dict[str, list[HistoricalPlay]] = {}
    for p in history:
        by_type.setdefault(p.play_type, []).append(p)

    n = len(sample)
    if n == 0:
        return {"n": 0, "total_plays": 0}

    ll = ll_noref = ll_base = brier = 0.0
    correct = decisive = decisive_correct = 0
    bins = [[0, 0.0, 0] for _ in range(CALIBRATION_BINS)]  # [n, suma p, cobradas]
    per_type: dict[str, list[int]] = {}

    for play in sample:
        pt = get_play_type(play.play_type)
        rest = [p for p in by_type[play.play_type] if p.id != play.id]
        r = predict(pt, play.features, play.referee_id, rest, params)
        p, y = r.probability, play.called

        ll += _log_loss(p, y)
        ll_noref += _log_loss(r.general_probability, y)
        ll_base += _log_loss(r.base_rate, y)
        brier += (p - y) ** 2
        hit = (p >= 0.5) == y
        correct += hit
        if r.recommendation != "DUDOSA":
            decisive += 1
            decisive_correct += hit
        b = bins[min(int(p * CALIBRATION_BINS), CALIBRATION_BINS - 1)]
        b[0] += 1
        b[1] += p
        b[2] += y
        t = per_type.setdefault(play.play_type, [0, 0])
        t[0] += 1
        t[1] += hit

    return {
        "n": n,
        "total_plays": len(history),
        "params": params.to_dict(),
        "accuracy": correct / n,
        "decisive_share": decisive / n,
        "decisive_accuracy": (decisive_correct / decisive) if decisive else None,
        "log_loss": ll / n,
        "log_loss_without_referee": ll_noref / n,
        "log_loss_base_rate": ll_base / n,
        "brier": brier / n,
        "calibration": [
            {
                "from": i / CALIBRATION_BINS,
                "to": (i + 1) / CALIBRATION_BINS,
                "n": b[0],
                "predicted": b[1] / b[0],
                "observed": b[2] / b[0],
            }
            for i, b in enumerate(bins)
            if b[0]
        ],
        "by_type": {k: {"n": v[0], "accuracy": v[1] / v[0]} for k, v in sorted(per_type.items())},
    }


def calibrate(
    history: list[HistoricalPlay],
    base: ModelParams = DEFAULT_PARAMS,
    grid: dict[str, tuple] = GRID,
    max_samples: int = 250,
    seed: int = 0,
) -> dict:
    """Busca en la grilla los parámetros con menor log-loss leave-one-out."""
    keys = list(grid)
    trials = []
    for values in itertools.product(*(grid[k] for k in keys)):
        params = replace(base, **dict(zip(keys, values)))
        m = evaluate(history, params, max_samples=max_samples, seed=seed)
        trials.append({"params": params.to_dict(), "log_loss": m["log_loss"], "accuracy": m["accuracy"]})
    trials.sort(key=lambda t: t["log_loss"])
    best = ModelParams.from_dict(trials[0]["params"]) if trials else base
    return {"best": best.to_dict(), "trials": trials}


def main() -> None:
    from .db import Database

    parser = argparse.ArgumentParser(description="Evalúa (y opcionalmente recalibra) el modelo de VARgento")
    parser.add_argument("--db", default="vargento.db")
    parser.add_argument("--calibrar", action="store_true", help="buscar y guardar los mejores parámetros")
    args = parser.parse_args()
    db = Database(args.db)
    history = db.history()
    params = db.get_model_params()
    if args.calibrar:
        result = calibrate(history, params)
        params = ModelParams.from_dict(result["best"])
        db.set_model_params(params)
        print("Parámetros guardados:", json.dumps(result["best"]))
    m = evaluate(history, params)
    print(json.dumps(m, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
