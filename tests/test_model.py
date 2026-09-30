import pytest

from vargento.db import Database
from vargento.domain import get_play_type, validate_features
from vargento.evaluate import calibrate, evaluate
from vargento.model import HistoricalPlay, ModelParams, predict, similarity
from vargento.seed import seed

OFFSIDE = get_play_type("offside")


def offside(cm, interfiere=True, campo=False, deliberada=False):
    return {
        "decision_campo": float(campo),
        "distancia_cm": cm,
        "interfiere_juego": float(interfiere),
        "jugada_deliberada_rival": float(deliberada),
    }


def hp(i, ref, feats, called):
    return HistoricalPlay(id=i, play_type="offside", referee_id=ref, features=feats, called=called)


def test_similarity_is_one_for_identical_and_decreases():
    a = offside(10)
    assert similarity(OFFSIDE, a, a) == pytest.approx(1.0)
    assert similarity(OFFSIDE, a, offside(12)) > similarity(OFFSIDE, a, offside(-30))


def test_prediction_follows_similar_plays():
    history = [hp(i, 1, offside(15 + i % 5), True) for i in range(20)]
    history += [hp(100 + i, 1, offside(-20 - i % 5), False) for i in range(20)]
    assert predict(OFFSIDE, offside(16), None, history).probability > 0.85
    assert predict(OFFSIDE, offside(-21), None, history).probability < 0.15


def test_referee_history_shifts_probability():
    feats = offside(2)
    # Todos los demás cobran la mitad; el árbitro 7 cobra siempre esta jugada.
    history = [hp(i, 1, feats, i % 2 == 0) for i in range(40)]
    history += [hp(100 + i, 7, feats, True) for i in range(15)]
    others = predict(OFFSIDE, feats, 1, history)
    strict = predict(OFFSIDE, feats, 7, history)
    assert strict.probability > others.probability + 0.15
    assert strict.referee_calls == 15


def test_no_history_falls_back_to_half():
    r = predict(OFFSIDE, offside(0), None, [])
    assert r.probability == pytest.approx(0.5)
    assert r.recommendation == "DUDOSA"
    assert r.confidence == "baja"


def test_validate_features_rejects_missing_and_out_of_range():
    with pytest.raises(ValueError):
        validate_features(OFFSIDE, {"distancia_cm": 1})
    with pytest.raises(ValueError):
        validate_features(OFFSIDE, offside(500))
    assert validate_features(OFFSIDE, {**offside(3), "interfiere_juego": True})["interfiere_juego"] == 1.0


def test_params_change_predictions_and_roundtrip():
    history = [hp(i, 1, offside(10), True) for i in range(10)] + [hp(50 + i, 1, offside(-10), False) for i in range(10)]
    narrow = predict(OFFSIDE, offside(0), None, history, ModelParams(bandwidth=0.05))
    wide = predict(OFFSIDE, offside(0), None, history, ModelParams(bandwidth=0.5))
    assert narrow.effective_similar < wide.effective_similar
    assert ModelParams.from_dict(ModelParams(top_k=5).to_dict()) == ModelParams(top_k=5)
    assert ModelParams.from_dict({"bandwidth": 0.1, "desconocido": 1}).bandwidth == 0.1


def test_evaluate_and_calibrate_on_demo_data(tmp_path):
    db = Database(tmp_path / "e.db")
    seed(db, n_plays=500)
    history = db.history()
    m = evaluate(history, max_samples=120)
    assert m["n"] == 120
    assert m["accuracy"] > 0.7
    assert m["log_loss"] < m["log_loss_base_rate"]
    assert sum(b["n"] for b in m["calibration"]) == 120
    result = calibrate(history, grid={"bandwidth": (0.08, 0.5)}, max_samples=80)
    assert result["best"]["bandwidth"] == 0.08
    db.set_model_params(ModelParams.from_dict(result["best"]))
    assert db.get_model_params().bandwidth == 0.08
    db.close()
