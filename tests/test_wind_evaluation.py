"""Frozen later-year replay must expose absent calendar hours and never refit."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from pipeline import wind_signal as wind


SOURCES = {name: {"source_type": "assumption", "ref": f"synthetic {name} replay fixture"}
           for name in ("system_wind_mw", "system_load_mw")}
BASELINE = {"value": 0.5, "source_type": "assumption", "ref": "predeclared synthetic constant comparator"}


def frame_at(starts):
    times = pd.DatetimeIndex([stamp for start in starts
                             for stamp in pd.date_range(start, periods=120, freq="h", tz="UTC")])
    index = np.arange(len(times))
    hourly = pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM",
                           "system_wind_mw": 50. + 20. * np.sin(index * np.pi / 6),
                           "system_load_mw": 100. + 15. * np.cos(index * np.pi / 12)})
    labels = pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM",
                           "wind_curtailment_event": pd.array(index % 12 < 6, dtype="boolean"),
                           "observed_five_minute_samples": 12, "evaluable_five_minute_samples": 12})
    labels.attrs = {"method": "reported_system_wind_curtailment_any_category_v1", "system_scope": "SPP_SYSTEM",
                    "source": {"source_type": "assumption", "ref": "synthetic independent VER replay targets"}}
    return hourly, labels


@pytest.fixture(scope="module")
def bundle():
    hourly, labels = frame_at(["2024-01-01", "2024-09-01", "2024-10-01"])
    return wind.fit_wind_event_classifier(hourly, labels, sources=SOURCES)[0]


def evaluate(bundle, hourly=None, labels=None, **overrides):
    if hourly is None:
        hourly, labels = frame_at(["2024-12-31"])
    return wind.evaluate_wind_event_classifier(bundle, hourly, labels, **{
        "sources": SOURCES, "baseline_probability": BASELINE,
        "start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2025-01-02T00:00:00Z", **overrides})


def test_later_replay_never_fits_or_mutates_frozen_parameters(bundle, monkeypatch):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    def reject(*args, **kwargs):
        raise AssertionError("Independent replay must not fit an estimator")
    monkeypatch.setattr(LogisticRegression, "fit", reject)
    monkeypatch.setattr(StandardScaler, "fit", reject)
    before = deepcopy(bundle)
    report, rows = evaluate(bundle)
    assert bundle == before
    assert report["model_id"] == bundle["model_id"]
    assert report["coverage"]["eligible_hours"]["value"] == 24
    assert all(row["target_period_role"] == "outside_evaluated_period" for row in rows)
    assert all(row["forecast_asof_verified"] is False for row in rows)


def test_declared_calendar_grid_does_not_hide_absent_archive_hours(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    labels = labels.loc[labels.timestamp_utc < pd.Timestamp("2025-01-01T12:00Z")].copy()
    report, rows = evaluate(bundle, hourly, labels)
    coverage = report["coverage"]
    assert coverage["requested_hours"]["value"] == 24
    assert coverage["supplied_label_hours"]["value"] == 12
    assert coverage["missing_label_hours"]["value"] == 12
    assert coverage["eligible_hours"]["value"] == 12
    assert coverage["excluded_hours"]["value"] == 12
    assert len(rows) == 24
    assert all(row["observed_event"]["value"] is None and not row["evaluated"] for row in rows[12:])
    assert all(row["raw_probability"]["value"] is not None for row in rows[12:])
    assert all(row["observed_event"]["source_type"] == "assumption" for row in rows[12:])


def test_overlapping_missing_labels_and_lags_count_exclusion_once(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    stamp = pd.Timestamp("2025-01-01T01:00Z")
    labels = labels.loc[labels.timestamp_utc != stamp].copy()
    hourly = hourly.loc[hourly.timestamp_utc != stamp - pd.Timedelta(1, unit="h")].copy()
    report, rows = evaluate(bundle, hourly, labels)
    assert report["coverage"]["missing_label_hours"]["value"] == 1
    assert report["coverage"]["missing_lag_hours"]["value"] == 1
    assert report["coverage"]["excluded_hours"]["value"] == 1
    assert rows[1]["raw_probability"]["value"] is None


def test_incomplete_supplied_hour_remains_distinct_from_missing_row(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    chosen = labels.timestamp_utc == pd.Timestamp("2025-01-01T00:00Z")
    labels.loc[chosen, "wind_curtailment_event"] = pd.NA
    labels.loc[chosen, "evaluable_five_minute_samples"] = 11
    report, rows = evaluate(bundle, hourly, labels)
    assert report["coverage"]["unknown_supplied_target_hours"]["value"] == 1
    assert report["coverage"]["missing_label_hours"]["value"] == 0
    assert "incomplete supplied" in rows[0]["observed_event"]["ref"]


def test_all_probability_comparators_use_identical_observed_rows(bundle):
    report, rows = evaluate(bundle)
    chosen = [row for row in rows if row["evaluated"]]
    target = [row["observed_event"]["value"] for row in chosen]
    for metric_name, field in [("raw", "raw_probability"), ("calibrated", "calibrated_probability")]:
        probabilities = [row[field]["value"] for row in chosen]
        metrics = report["metrics"][metric_name]
        assert metrics["sample_count"]["value"] == len(chosen)
        assert metrics["brier_score"]["value"] == pytest.approx(brier_score_loss(target, probabilities))
        assert metrics["log_loss"]["value"] == pytest.approx(log_loss(target, probabilities))
        assert metrics["roc_auc"]["value"] == pytest.approx(roc_auc_score(target, probabilities))
        assert sum(item["sample_count"]["value"] for item in metrics["reliability"]) == len(chosen)
    baseline = report["metrics"]["supplied_constant_baseline"]
    assert baseline["sample_count"]["value"] == len(chosen)
    assert baseline["brier_score"]["value"] == 0.25
    assert report["baseline_probability"] == BASELINE


def test_outcome_changes_cannot_change_predictions_or_frozen_model(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    original_report, original_rows = evaluate(bundle, hourly, labels)
    labels.wind_curtailment_event = ~labels.wind_curtailment_event
    report, rows = evaluate(bundle, hourly, labels)
    assert [row["raw_probability"] for row in rows] == [row["raw_probability"] for row in original_rows]
    assert report["data_manifest"]["labels_sha256"] != original_report["data_manifest"]["labels_sha256"]
    assert report["metrics"]["raw"]["brier_score"] != original_report["metrics"]["raw"]["brier_score"]
    assert report["metrics"]["raw"]["brier_score"]["ref"] != original_report["metrics"]["raw"]["brier_score"]["ref"]
    assert report["data_manifest"]["evaluation_cohort_sha256"] in report["metrics"]["raw"]["brier_score"]["ref"]


def test_probability_refs_identify_the_hour_and_query_data(bundle):
    report, rows = evaluate(bundle)
    assert rows[0]["raw_probability"]["ref"] != rows[1]["raw_probability"]["ref"]
    assert rows[0]["timestamp_utc"] in rows[0]["raw_probability"]["ref"]
    assert report["data_manifest"]["hourly_observations_sha256"] in rows[0]["raw_probability"]["ref"]


def test_unavailable_metric_refs_identify_the_distinct_requested_windows(bundle):
    first, _ = evaluate(bundle, start_utc="2025-02-01T00:00Z", end_exclusive_utc="2025-02-02T00:00Z")
    second, _ = evaluate(bundle, start_utc="2025-03-01T00:00Z", end_exclusive_utc="2025-03-02T00:00Z")
    left, right = first["metrics"]["raw"]["brier_score"], second["metrics"]["raw"]["brier_score"]
    assert left["value"] is right["value"] is None
    assert left["ref"] != right["ref"]


def test_reordered_evaluation_is_byte_reproducible_and_keeps_assumptions(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    first = evaluate(bundle, hourly, labels)
    second = evaluate(bundle, hourly.iloc[::-1], labels.iloc[::-1], sources=dict(reversed(list(SOURCES.items()))))
    assert json.dumps(first) == json.dumps(second)
    for metrics in first[0]["metrics"].values():
        assert metrics["brier_score"]["source_type"] == "assumption"
    assert "research_only" in first[0]["status"]


@pytest.mark.parametrize("overrides", [
    {"start_utc": "2024-12-31T00:00Z"},
    {"start_utc": "2025-01-02T00:00Z"},
    {"start_utc": "2025-01-03T00:00Z"},
    {"start_utc": "2025-01-01"},
    {"start_utc": "2025-01-01T00:30Z"},
    {"end_exclusive_utc": "2026-01-03T00:00Z"},
])
def test_invalid_or_reused_experiment_windows_are_rejected(bundle, overrides):
    with pytest.raises(ValueError):
        evaluate(bundle, **overrides)


@pytest.mark.parametrize("value", [None, True, -0.1, 1.1, np.nan, np.inf])
def test_invalid_constant_baselines_do_not_become_plausible_comparisons(bundle, value):
    with pytest.raises(ValueError):
        evaluate(bundle, baseline_probability={**BASELINE, "value": value})


def test_baseline_cannot_hide_an_assumed_coefficient_under_data_provenance(bundle):
    ref = json.dumps({"method": "declared comparator", "inputs": [BASELINE]})
    with pytest.raises(ValueError, match="upgrade a nested"):
        evaluate(bundle, baseline_probability={**BASELINE, "source_type": "data", "ref": ref})


def test_frozen_legacy_manifest_replays_with_new_source_qualification(bundle):
    legacy = deepcopy(bundle)
    legacy["manifest"]["limitation"] = wind.WIND_CLASSIFIER_LEGACY_LIMITATION
    legacy["model_id"] = wind._wind_json_digest({key: value for key, value in legacy.items() if key != "model_id"})
    before = deepcopy(legacy)
    report, rows = evaluate(legacy)
    assert legacy == before
    assert report["model_id"] == legacy["model_id"]
    assert report["limitation"] == wind.WIND_CLASSIFIER_LIMITATION
    assert report["source_qualification"] == wind.GENMIX_SOURCE_QUALIFICATION
    assert "dispatch targets" in report["limitation"]
    assert all(row["source_qualification"] == wind.GENMIX_SOURCE_QUALIFICATION for row in rows)


def test_legacy_compatibility_does_not_accept_arbitrary_softened_limitations(bundle):
    edited = deepcopy(bundle)
    edited["manifest"]["limitation"] = "Verified actual metered generation with no timing uncertainty."
    edited["model_id"] = wind._wind_json_digest({key: value for key, value in edited.items() if key != "model_id"})
    with pytest.raises(ValueError, match="honesty framing"):
        evaluate(edited)


def test_zero_evaluable_window_reports_unavailable_not_zero_error(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    report, rows = evaluate(bundle, hourly, labels,
                            start_utc="2025-02-01T00:00Z", end_exclusive_utc="2025-02-02T00:00Z")
    assert report["coverage"]["excluded_hours"]["value"] == 24
    assert report["coverage"]["missing_label_hours"]["value"] == 24
    assert all(not row["evaluated"] for row in rows)
    for metrics in report["metrics"].values():
        assert metrics["brier_score"]["value"] is None
        assert metrics["log_loss"]["value"] is None


def test_disabled_calibration_is_not_substituted_with_raw_probabilities():
    hourly, labels = frame_at(["2024-01-01", "2024-09-01", "2024-10-01"])
    bundle = wind.fit_wind_event_classifier(hourly, labels, sources=SOURCES, calibrate=False)[0]
    report, rows = evaluate(bundle)
    assert report["metrics"]["raw"]["sample_count"]["value"] == 24
    assert report["metrics"]["calibrated"]["sample_count"]["value"] == 0
    assert report["metrics"]["calibrated"]["brier_score"]["value"] is None
    assert all(row["calibrated_probability"]["value"] is None for row in rows)
