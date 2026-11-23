from datetime import datetime, timedelta

import pytest

from app.engine.pipeline import _combine, _ml_level, score_payload
from app.engine.windows import WINDOWS, Event
from app.ml.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, feature_row, split_merchant
from app.ml.model import MODEL
from app.schemas import TransactionIn

NOW = datetime(2026, 10, 6, 12, 0, 0)


def test_split_merchant_handles_banksim_and_plain():
    assert split_merchant("travel · M1823072687") == ("travel", "M1823072687")
    assert split_merchant("POS") == ("unknown", "POS")


def test_feature_row_has_every_declared_feature():
    feats = feature_row(
        amount=100.0, merchant="health · M12", ts=NOW, lat=40.4, lon=-3.7,
        prev=None, velocity_count=1, n=0, mean=0.0, std=0.0,
    )
    for name in NUMERIC_FEATURES + CATEGORICAL_FEATURES:
        assert name in feats, name
    assert feats["has_prev"] == 0
    assert feats["has_baseline"] == 0
    assert feats["category"] == "health"


def test_feature_row_uses_window_stats():
    prev = Event(NOW - timedelta(hours=2), 40.4, -3.7, 50.0)
    feats = feature_row(
        amount=500.0, merchant="travel · M1", ts=NOW, lat=40.4, lon=-3.7,
        prev=prev, velocity_count=3, n=20, mean=100.0, std=50.0,
    )
    assert feats["has_prev"] == 1
    assert feats["has_baseline"] == 1
    assert feats["amount_z"] == pytest.approx((500.0 - 100.0) / 50.0)
    assert feats["amount_over_mean"] == pytest.approx(5.0)
    assert feats["velocity_10m"] == 3
    assert feats["history_count"] == 20


def test_amount_over_mean_is_capped():
    feats = feature_row(
        amount=1e9, merchant="x · M1", ts=NOW, lat=0.0, lon=0.0,
        prev=None, velocity_count=1, n=10, mean=1.0, std=1.0,
    )
    assert feats["amount_over_mean"] == 50.0


def test_ml_level_bands():
    assert _ml_level(0.99, 0.9) == "High"
    assert _ml_level(0.5, 0.9) == "Medium"
    assert _ml_level(0.1, 0.9) == "Low"


def test_combine_takes_the_more_severe_verdict():
    assert _combine("Low", "High") == "High"
    assert _combine("High", "Low") == "High"
    assert _combine("Medium", "Low") == "Medium"
    assert _combine("Low", None) == "Low"


def test_scoring_still_works_without_a_model():
    """The rules must stand on their own if no model file is present."""
    was_loaded = MODEL.available
    MODEL.unload()
    try:
        item, _ = score_payload(TransactionIn(
            account_id="ACC-NOMODEL", amount=42.0, timestamp=NOW,
            lat=40.4, lon=-3.7, city="Madrid", merchant="food · M9",
        ))
        assert item.ml_score is None
        assert item.ml_flag is False
        assert item.level == item.rule_level
    finally:
        if was_loaded:
            MODEL.load()


@pytest.mark.skipif(not MODEL.load(), reason="no trained model; run python train_model.py")
def test_model_scores_in_range_and_explains():
    feats = feature_row(
        amount=1200.0, merchant="travel · M1823072687", ts=NOW, lat=40.4, lon=-3.7,
        prev=Event(NOW - timedelta(hours=3), 40.4, -3.7, 30.0),
        velocity_count=2, n=25, mean=35.0, std=12.0,
    )
    prob = MODEL.probability(feats)
    assert prob is not None
    assert 0.0 <= prob <= 1.0

    reasons = MODEL.explain(feats)
    assert reasons, "a high-risk travel transaction should produce reasons"
    for r in reasons:
        assert set(r) == {"feature", "contribution", "detail"}
        assert r["contribution"] > 0
        assert r["detail"]
    # Sorted strongest first.
    assert reasons == sorted(reasons, key=lambda d: -d["contribution"])


@pytest.mark.skipif(not MODEL.load(), reason="no trained model; run python train_model.py")
def test_live_scoring_attaches_model_output():
    item, _ = score_payload(TransactionIn(
        account_id="ACC-ML-1", amount=950.0, timestamp=NOW,
        lat=40.4, lon=-3.7, city="Madrid", merchant="travel · M1823072687",
    ))
    assert item.ml_score is not None
    assert 0 <= item.ml_score <= 100
    assert item.ml_level in ("Low", "Medium", "High")
    assert item.rule_level in ("Low", "Medium", "High")
    # The combined level is never softer than either side on its own.
    rank = {"Low": 0, "Medium": 1, "High": 2}
    assert rank[item.level] >= rank[item.rule_level]
    assert rank[item.level] >= rank[item.ml_level]


@pytest.mark.skipif(not MODEL.load(), reason="no trained model; run python train_model.py")
def test_model_separates_a_safe_row_from_a_risky_one():
    """Transportation is 0% fraud in BankSim; travel is ~79%."""
    common = dict(ts=NOW, lat=40.4, lon=-3.7, prev=None,
                  velocity_count=1, n=30, mean=35.0, std=12.0)
    safe = MODEL.probability(feature_row(
        amount=12.0, merchant="transportation · M348934600", **common))
    risky = MODEL.probability(feature_row(
        amount=1400.0, merchant="travel · M1823072687", **common))
    assert risky > safe


def test_anomaly_alone_only_reaches_review_queue():
    assert _combine("Low", "Low", "High") == "Medium"
    assert _combine("Low", None, "Medium") == "Low"
    assert _combine("High", "Low", "High") == "High"


def test_psi_is_zero_for_identical_and_large_for_shifted():
    from app.services.monitor import psi
    same = [0.1] * 10
    assert psi(same, same) == pytest.approx(0.0)
    shifted = [0.0] * 9 + [1.0]
    assert psi(same, shifted) > 0.25


def test_threshold_curve_is_monotone_in_alert_volume():
    import numpy as np
    from app.ml.training import threshold_curve
    rng = np.random.RandomState(0)
    y = (rng.rand(5000) < 0.05).astype(int)
    scores = np.clip(y * 0.6 + rng.rand(5000) * 0.5, 0, 1)
    curve = threshold_curve(y, scores)
    volumes = [c["alerts_per_1000"] for c in curve]
    assert volumes == sorted(volumes, reverse=True)
    assert all(0.0 <= c["precision"] <= 1.0 and 0.0 <= c["recall"] <= 1.0 for c in curve)


@pytest.mark.skipif(not MODEL.load() or not MODEL.has_anomaly, reason="model without anomaly detector")
def test_anomaly_percentile_in_range():
    feats = feature_row(
        amount=40.0, merchant="food · M1", ts=NOW, lat=40.4, lon=-3.7,
        prev=Event(NOW - timedelta(hours=5), 40.4, -3.7, 35.0),
        velocity_count=1, n=30, mean=38.0, std=10.0,
    )
    pct = MODEL.anomaly(feats)
    assert 0.0 <= pct <= 100.0


@pytest.mark.skipif(not MODEL.load(), reason="no trained model")
def test_threshold_override_changes_live_flag_and_resets():
    trained = MODEL.trained_threshold
    try:
        MODEL.set_threshold(0.01)
        item, _ = score_payload(TransactionIn(
            account_id="ACC-THR-1", amount=950.0, timestamp=NOW,
            lat=40.4, lon=-3.7, city="Madrid", merchant="travel · M1823072687",
        ))
        assert item.ml_flag is True
    finally:
        MODEL.set_threshold(None)
    assert MODEL.threshold == pytest.approx(trained)
