"""Software fixtures for frozen-target comparisons; no fitted product results."""
from copy import deepcopy
import json

import joblib
import numpy as np
import pandas as pd
import pytest

from pipeline import backtest
from pipeline.common import write_json
from pipeline.features import build_features


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    times = pd.date_range("2024-12-29", "2025-03-01", freq="h", inclusive="left", tz="UTC")
    frame = pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM",
                          "load_mw": 1000 + 100 * np.sin(np.arange(len(times)) / 12)})
    hourly = tmp_path / "hourly.parquet"
    frame.to_parquet(hourly, index=False)
    card = {"model_version": "fixture", "policy": {
        "operator": "SPP", "label_method": "observed_event", "target_name": "high_demand_stress_proxy",
        "target_source_type": "assumption", "demand_proxy": {"thresholds_mw": {"SPP_SYSTEM": 1050.},
            "quantile": .95, "reference_start": "2019-01-01T00:00Z", "reference_end_exclusive": "2022-01-01T00:00Z"}},
        "settings": {"embargo_hours": 24}, "splits": {
            "train": {"end": "2022-01-02T00:00Z", "rows": 1000, "positive_hours": 100},
            "calibration": {"end": "2023-01-01T00:00Z"}}}
    _, names = build_features(frame.assign(target=frame.load_mw.ge(1050.).astype(float)), card["policy"])
    runs = [tmp_path / "old", tmp_path / "new"]
    for i, path in enumerate(runs):
        path.mkdir()
        document = deepcopy(card)
        document["model_version"] = f"fixture-{i}"
        write_json(path / "model_card.json", document)
        joblib.dump({"report": document, "feature_names": names,
                     "density_training_locations": np.array(["SPP_SYSTEM"]), "probability": .1 + .1 * i}, path / "model.joblib")
    monkeypatch.setattr(backtest, "predict_members", lambda bundle, rows: np.full((len(rows), 2), bundle["probability"]))
    return runs, hourly, tmp_path / "comparison"


def compare(inputs):
    runs, hourly, out = inputs
    return backtest.compare_runs(runs, hourly, "2025-01-01T00:00Z", "2025-03-01T00:00Z", out)


def test_same_later_hours_and_frozen_target_are_used_for_both_models(comparison):
    report = compare(comparison)
    assert report["expected_hours"] == report["scored_hours"] == 1416
    assert report["unscored_hours"] == 0
    metrics = [item["metrics"] for item in report["runs"]]
    assert metrics[0]["positive_hours"] == metrics[1]["positive_hours"]
    assert metrics[1]["brier_score"] < metrics[0]["brier_score"]
    assert report["paired_brier_changes_vs_first_run"][0]["interval_95"] is not None
    predictions = pd.read_parquet(comparison[2] / "predictions.parquet")
    assert predictions.run_0_probability.eq(.1).all()
    assert predictions.run_1_probability.eq(.2).all()
    with pytest.raises(ValueError, match="already exists"):
        compare(comparison)


@pytest.mark.parametrize("field", ["threshold", "reference_period", "label_type", "training_overlap", "calibration_overlap"])
def test_comparison_rejects_target_drift_or_fitted_hours(comparison, field):
    path = comparison[0][1] / "model_card.json"
    card = json.loads(path.read_text())
    if field == "threshold":
        card["policy"]["demand_proxy"]["thresholds_mw"]["SPP_SYSTEM"] = 1060.
    elif field == "reference_period":
        card["policy"]["demand_proxy"]["reference_end_exclusive"] = "2023-01-01T00:00Z"
    elif field == "label_type":
        card["policy"]["target_name"] = "actual_emergencies"
    else:
        part = "train" if field == "training_overlap" else "calibration"
        card["splits"][part]["end"] = "2025-01-01T00:00Z"
    write_json(path, card)
    with pytest.raises(ValueError):
        compare(comparison)
    assert not comparison[2].exists()


def test_comparison_does_not_overwrite_independent_labels(comparison):
    path = comparison[1]
    pd.read_parquet(path).assign(event_active=0).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="existing labels"):
        compare(comparison)


def test_missing_load_hours_stay_out_of_both_scores(comparison):
    path = comparison[1]
    frame = pd.read_parquet(path)
    frame.loc[frame.timestamp_utc == pd.Timestamp("2025-01-15T12:00Z"), "load_mw"] = np.nan
    frame.to_parquet(path, index=False)
    report = compare(comparison)
    assert report["unscored_hours"] == 25  # Missing label plus subsequent 24-hour history.
    assert all(item["metrics"]["n_hours"] == 1391 for item in report["runs"])


def test_mismatched_saved_bundle_is_not_evaluated(comparison):
    path = comparison[0][0] / "model.joblib"
    bundle = joblib.load(path)
    bundle["report"]["model_version"] = "another model"
    joblib.dump(bundle, path)
    with pytest.raises(ValueError, match="model/card"):
        compare(comparison)


@pytest.mark.parametrize("probability", [np.nan, np.inf, -.1, 1.1])
def test_invalid_predictions_are_rejected(comparison, monkeypatch, probability):
    monkeypatch.setattr(backtest, "predict_members", lambda bundle, frame: np.full((len(frame), 2), probability))
    with pytest.raises(ValueError, match="invalid probabilities"):
        compare(comparison)


def test_paired_interval_is_deterministic_and_zero_for_identical_models():
    times = pd.Series(pd.date_range("2025-01-01", periods=2000, freq="h", tz="UTC"))
    target = np.arange(len(times)) % 2
    probability = np.full(len(times), .2)
    result = backtest.paired_brier_interval(times, target, probability, probability)
    assert result["candidate_minus_baseline"] == 0
    assert result["interval_95"] == [0, 0]
