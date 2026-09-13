"""Nullable identifiers must not invent an SPP footprint for synthetic rows."""
from copy import deepcopy
import hashlib
import json

import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from pipeline import wind_signal as wind


SOURCE = {"source_type": "assumption", "ref": "synthetic scope regression; no measured observations or fitted model"}
SOURCES = {name: dict(SOURCE) for name in ("system_wind_mw", "system_load_mw")}
BASELINE = {"value": 0.5, **SOURCE}
START, END = "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z"


@pytest.fixture(autouse=True)
def no_training_or_external_reads(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Scope validation must not fit, fetch, or load data")
    monkeypatch.setattr(wind, "fit_wind_event_classifier", reject)
    monkeypatch.setattr(LogisticRegression, "fit", reject)
    monkeypatch.setattr(StandardScaler, "fit", reject)
    monkeypatch.setattr(wind.ingest, "fetch_public_evidence", reject)
    monkeypatch.setattr(pd, "read_parquet", reject)


@pytest.fixture
def bundle():
    # A manually specified constant-probability model exercises deserialization;
    # no training or published artifact is needed to test footprint validation.
    value = {
        "schema_version": wind.WIND_CLASSIFIER_SCHEMA,
        "features": list(wind.WIND_CLASSIFIER_FEATURES),
        "standardizer": {"mean": [0.0] * 4, "scale": [1.0] * 4},
        "base_model": {"coefficients": [0.0] * 4, "intercept": 0.0},
        "calibrator": None,
        "manifest": {
            "system_scope": "SPP_SYSTEM", "forecast_asof_verified": False,
            "status": "research_only_no_production_promotion",
            "target_method": "reported_system_wind_curtailment_any_category_v1",
            "split_bounds": {name: list(bounds) for name, bounds in wind.WIND_CLASSIFIER_SPLITS.items()},
            "limitation": wind.WIND_CLASSIFIER_LIMITATION,
            "calibration_minimum_per_class": 20, "calibration_requested": False,
            "calibration_status": "disabled_by_declared_policy",
            "training_cohort_sha256": hashlib.sha256(b"synthetic schema fixture; no training cohort").hexdigest(),
            "calibration_cohort_sha256": None,
            "fit_parameters": {"C": 1.0, "solver": "lbfgs", "class_weight": None,
                               "max_iter": 2000, "tol": 1e-8, "random_state": 2026},
            "policy_source": dict(SOURCE),
            "input_sources": {**deepcopy(SOURCES), "labels": dict(SOURCE)},
        },
    }
    value["model_id"] = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return value


def inputs():
    hours = pd.date_range("2024-12-31", periods=48, freq="h", tz="UTC")
    hourly = pd.DataFrame({"timestamp_utc": hours, "location_id": "SPP_SYSTEM",
                           "system_wind_mw": 50.0, "system_load_mw": 100.0})
    hourly.attrs["sources"] = deepcopy(SOURCES)
    labels = pd.DataFrame({"timestamp_utc": hours[24:], "location_id": "SPP_SYSTEM",
                           "wind_curtailment_event": pd.array([False, True] * 12, dtype="boolean"),
                           "observed_five_minute_samples": 12, "evaluable_five_minute_samples": 12})
    labels.attrs = {"method": "reported_system_wind_curtailment_any_category_v1",
                    "system_scope": "SPP_SYSTEM", "source": dict(SOURCE)}
    return hourly, labels


def call(entrypoint, bundle, hourly, labels):
    if entrypoint == "prepare":
        return wind.prepare_wind_classifier_features(hourly, sources=SOURCES,
                                                      target_timestamps=labels.timestamp_utc)
    if entrypoint == "predict":
        return wind.predict_wind_event_classifier(bundle, hourly, sources=SOURCES,
                                                  target_timestamps=labels.timestamp_utc)
    return wind.evaluate_wind_event_classifier(bundle, hourly, labels, sources=SOURCES,
        baseline_probability=BASELINE, start_utc=START, end_exclusive_utc=END)


def assert_unchanged(hourly, labels, bundle, saved):
    for actual, before in zip((hourly, labels), saved[:2]):
        pd.testing.assert_frame_equal(actual, before)
        assert actual.attrs == before.attrs
    assert bundle == saved[2]


@pytest.mark.parametrize("storage", ["python", "pyarrow"])
@pytest.mark.parametrize("missing", ["all", "mixed"])
@pytest.mark.parametrize("entrypoint", ["prepare", "predict", "replay"])
def test_missing_hourly_scope_cannot_be_relabelled_as_spp_system(bundle, storage, missing, entrypoint):
    hourly, labels = inputs()
    hourly["location_id"] = hourly.location_id.astype(pd.StringDtype(storage=storage))
    hourly.loc[hourly.index if missing == "all" else [0], "location_id"] = pd.NA
    saved = deepcopy((hourly, labels, bundle))
    try:
        with pytest.raises(ValueError, match="SPP_SYSTEM"):
            call(entrypoint, bundle, hourly, labels)
    finally:
        assert_unchanged(hourly, labels, bundle, saved)


@pytest.mark.parametrize("storage", ["python", "pyarrow"])
@pytest.mark.parametrize("missing", ["all", "mixed"])
def test_missing_label_scope_cannot_supply_independent_system_targets(bundle, storage, missing):
    hourly, labels = inputs()
    labels["location_id"] = labels.location_id.astype(pd.StringDtype(storage=storage))
    labels.loc[labels.index if missing == "all" else [0], "location_id"] = pd.NA
    saved = deepcopy((hourly, labels, bundle))
    try:
        with pytest.raises(ValueError, match="sites or zones"):
            call("replay", bundle, hourly, labels)
    finally:
        assert_unchanged(hourly, labels, bundle, saved)


@pytest.mark.parametrize("storage", ["python", "pyarrow"])
def test_complete_string_scope_preserves_features_predictions_replay_and_sources(bundle, storage):
    hourly, labels = inputs()
    expected = {name: call(name, bundle, hourly, labels) for name in ("prepare", "predict", "replay")}
    for frame in (hourly, labels):
        frame["location_id"] = frame.location_id.astype(pd.StringDtype(storage=storage))
    saved = deepcopy((hourly, labels, bundle))
    actual = {name: call(name, bundle, hourly, labels) for name in expected}
    pd.testing.assert_frame_equal(actual["prepare"], expected["prepare"])
    assert actual["prepare"].attrs == expected["prepare"].attrs
    for name in ("predict", "replay"):
        assert json.dumps(actual[name], sort_keys=True, allow_nan=False) == json.dumps(
            expected[name], sort_keys=True, allow_nan=False)
    report, rows = actual["replay"]
    assert report["coverage"]["eligible_hours"]["value"] == 24
    assert all(row["raw_probability"]["value"] == 0.5 for row in rows)
    assert all(row["raw_probability"]["source_type"] == "assumption" for row in rows)
    assert all(row["calibrated_probability"]["value"] is None for row in rows)
    assert_unchanged(hourly, labels, bundle, saved)
