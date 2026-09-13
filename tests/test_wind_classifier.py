"""Offline classifier checks use assumption-sourced synthetic event fixtures only."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal as wind


SOURCES = {name: {"source_type": "assumption", "ref": f"synthetic hourly {name} fixture"}
           for name in ("system_wind_mw", "system_load_mw")}


def inputs():
    dates = pd.DatetimeIndex([stamp for start in ("2024-01-01", "2024-09-01", "2024-10-01")
                             for stamp in pd.date_range(start, periods=96, freq="h", tz="UTC")])
    index = np.arange(len(dates), dtype=float)
    actuals = pd.DataFrame({"timestamp_utc": dates, "location_id": "SPP_SYSTEM",
                           "system_wind_mw": 40. + 15. * np.sin(index * np.pi / 4.),
                           "system_load_mw": 100. + 20. * np.cos(index * np.pi / 12.)})
    target = pd.Series(((index.astype(int) - 1) % 8) < 4, dtype="boolean")
    events = pd.DataFrame({"timestamp_utc": dates, "location_id": "SPP_SYSTEM",
                          "wind_curtailment_event": target,
                          "observed_five_minute_samples": 12, "evaluable_five_minute_samples": 12})
    events.attrs = {"method": "reported_system_wind_curtailment_any_category_v1", "system_scope": "SPP_SYSTEM",
                    "source": {"source_type": "assumption", "ref": "synthetic independent VER event fixture"}}
    return actuals, events


def fit(actuals=None, events=None, **kwargs):
    if actuals is None:
        actuals, events = inputs()
    return wind.fit_wind_event_classifier(actuals, events, sources=SOURCES, **kwargs)


@pytest.fixture(scope="module")
def experiment():
    return fit()


def probabilities(rows, name="raw_probability"):
    return [row[name]["value"] for row in rows]


def test_exact_time_lags_do_not_jump_missing_hours_or_use_current_actuals():
    actuals, _ = inputs()
    target = actuals.timestamp_utc.iloc[30]
    without_previous = actuals.drop(index=29)
    features = wind.prepare_wind_classifier_features(without_previous, sources=SOURCES, target_timestamps=[target])
    assert pd.isna(features.system_wind_mw_lag_1h.iloc[0])
    assert features.system_wind_mw_lag_24h.iloc[0] == actuals.system_wind_mw.iloc[6]
    changed_current = without_previous.copy()
    changed_current.loc[30, "system_wind_mw"] = 900.
    other = wind.prepare_wind_classifier_features(changed_current, sources=SOURCES, target_timestamps=[target])
    pd.testing.assert_frame_equal(features, other)


def test_requested_hour_needs_only_its_lags_not_a_current_hour_observation():
    actuals, _ = inputs()
    target = actuals.timestamp_utc.iloc[30]
    expected = wind.prepare_wind_classifier_features(actuals, sources=SOURCES, target_timestamps=[target])
    result = wind.prepare_wind_classifier_features(actuals.drop(index=30), sources=SOURCES, target_timestamps=[target])
    pd.testing.assert_frame_equal(expected, result)
    assert result[list(wind.WIND_CLASSIFIER_FEATURES)].notna().all().all()


def test_only_four_declared_features_enter_even_when_prices_and_rule_columns_exist():
    actuals, events = inputs()
    baseline = fit(actuals, events)
    augmented = actuals.assign(lmp_usd_mwh=-999., wind_oversupply_proxy=True,
                              wind_curtailment_event=events.wind_curtailment_event)
    result = fit(augmented, events)
    assert result[0] == baseline[0]
    assert probabilities(result[2]) == probabilities(baseline[2])
    assert result[0]["features"] == ["system_wind_mw_lag_1h", "system_load_mw_lag_1h",
                                     "system_wind_mw_lag_24h", "system_load_mw_lag_24h"]


def test_test_actuals_do_not_fit_scaler_base_or_calibrator(experiment):
    actuals, events = inputs()
    actuals.loc[actuals.timestamp_utc.dt.month == 10, "system_wind_mw"] = 10000.
    changed, report, _ = fit(actuals, events)
    assert changed == experiment[0]
    assert report["data_manifest"]["hourly_observations_sha256"] != experiment[1]["data_manifest"]["hourly_observations_sha256"]


def test_test_label_changes_only_change_evaluation_not_any_fitted_artifact(experiment):
    actuals, events = inputs()
    october = events.timestamp_utc.dt.month == 10
    events.loc[october, "wind_curtailment_event"] = ~events.loc[october, "wind_curtailment_event"]
    changed, report, rows = fit(actuals, events)
    assert changed == experiment[0]
    assert probabilities(rows) == probabilities(experiment[2])
    assert probabilities(rows, "calibrated_probability") == probabilities(experiment[2], "calibrated_probability")
    assert report["test_metrics"]["raw"]["brier_score"]["value"] != experiment[1]["test_metrics"]["raw"]["brier_score"]["value"]


def test_calibration_labels_change_only_calibration_and_never_choose_model_on_test(experiment):
    actuals, events = inputs()
    september = events.timestamp_utc.dt.month == 9
    events.loc[september, "wind_curtailment_event"] = ~events.loc[september, "wind_curtailment_event"]
    changed, report, rows = fit(actuals, events)
    assert changed["base_model"] == experiment[0]["base_model"]
    assert changed["standardizer"] == experiment[0]["standardizer"]
    assert changed["calibrator"] != experiment[0]["calibrator"]
    assert probabilities(rows) == probabilities(experiment[2])
    assert changed["calibrator"] is not None
    assert report["test_metrics"]["calibrated"]["brier_score"]["value"] > report["test_metrics"]["raw"]["brier_score"]["value"]
    assert report["calibration_status"] == "regularized_sigmoid_fitted_on_september_only"


def test_training_labels_actually_affect_fitted_model(experiment):
    actuals, events = inputs()
    january = events.timestamp_utc.dt.month == 1
    events.loc[january, "wind_curtailment_event"] = ~events.loc[january, "wind_curtailment_event"]
    changed, _, rows = fit(actuals, events)
    assert changed["base_model"] != experiment[0]["base_model"]
    assert probabilities(rows) != probabilities(experiment[2])


def test_later_observations_cannot_change_earlier_prediction_values(experiment):
    actuals, _ = inputs()
    boundary = pd.Timestamp("2024-10-03T00:00:00Z")
    targets = [row["timestamp_utc"] for row in experiment[2] if pd.Timestamp(row["timestamp_utc"]) < boundary]
    before = wind.predict_wind_event_classifier(experiment[0], actuals, sources=SOURCES, target_timestamps=targets)
    actuals.loc[actuals.timestamp_utc >= boundary, "system_wind_mw"] = 9999.
    after = wind.predict_wind_event_classifier(experiment[0], actuals, sources=SOURCES, target_timestamps=targets)
    assert probabilities(before) == probabilities(after)
    assert probabilities(before, "calibrated_probability") == probabilities(after, "calibrated_probability")


@pytest.mark.parametrize("negative_count,expected", [(0, False), (19, False), (20, True)])
def test_september_calibration_requires_predeclared_twenty_per_class(negative_count, expected):
    actuals, events = inputs()
    features = wind.prepare_wind_classifier_features(actuals, sources=SOURCES)
    eligible = features[list(wind.WIND_CLASSIFIER_FEATURES)].notna().all(axis=1)
    september = events.timestamp_utc.dt.month == 9
    events.loc[september, "wind_curtailment_event"] = True
    indices = events.index[september & eligible][:negative_count]
    events.loc[indices, "wind_curtailment_event"] = False
    bundle, report, rows = fit(actuals, events)
    assert (bundle["calibrator"] is not None) is expected
    if not expected:
        assert report["calibration_status"] == "insufficient_september_class_support"
        assert all(value is None for value in probabilities(rows, "calibrated_probability"))
        assert report["test_metrics"]["calibrated"]["brier_score"]["value"] is None


def test_declared_disabled_calibration_does_not_use_september_labels():
    actuals, events = inputs()
    bundle, report, rows = fit(actuals, events, calibrate=False)
    events.loc[events.timestamp_utc.dt.month == 9, "wind_curtailment_event"] = True
    changed, _, _ = fit(actuals, events, calibrate=False)
    assert changed == bundle
    assert report["calibration_status"] == "disabled_by_declared_policy"
    assert all(row["calibrated_probability"]["value"] is None and "unavailable" in row["calibrated_probability"]["ref"] for row in rows)


@pytest.mark.parametrize("kind", ["all_missing_inputs", "all_unknown_targets", "single_class"])
def test_training_without_independent_evaluable_class_support_is_rejected(kind):
    actuals, events = inputs()
    january = events.timestamp_utc.dt.month == 1
    if kind == "all_missing_inputs":
        actuals.loc[january, "system_wind_mw"] = np.nan
    elif kind == "all_unknown_targets":
        events.loc[january, "wind_curtailment_event"] = pd.NA
        events.loc[january, "evaluable_five_minute_samples"] = 0
    else:
        events.loc[january, "wind_curtailment_event"] = True
    with pytest.raises(ValueError, match="both independent event classes"):
        fit(actuals, events)


def test_unknown_targets_are_excluded_and_not_negative_examples(experiment):
    actuals, events = inputs()
    events.loc[events.index[-1], "wind_curtailment_event"] = pd.NA
    events.loc[events.index[-1], "evaluable_five_minute_samples"] = 11
    _, report, rows = fit(actuals, events)
    assert report["cohorts"]["test"]["unknown_target_hours"]["value"] == 1
    assert report["test_metrics"]["raw"]["sample_count"]["value"] == experiment[1]["test_metrics"]["raw"]["sample_count"]["value"] - 1
    assert rows[-1]["observed_event"]["value"] is None
    assert rows[-1]["evaluated"] is False


def test_single_class_test_has_finite_probability_scores_but_no_auc():
    actuals, events = inputs()
    events.loc[events.timestamp_utc.dt.month == 10, "wind_curtailment_event"] = True
    _, report, _ = fit(actuals, events)
    for name in ("raw", "calibrated", "training_prevalence_baseline"):
        scores = report["test_metrics"][name]
        assert np.isfinite(scores["brier_score"]["value"])
        assert np.isfinite(scores["log_loss"]["value"])
        assert scores["roc_auc"]["value"] is None
        assert "both observed classes" in scores["roc_auc"]["ref"]


def test_empty_test_has_explicit_unavailable_metrics_and_no_predictions():
    actuals, events = inputs()
    events = events[events.timestamp_utc.dt.month != 10].copy()
    _, report, rows = fit(actuals, events)
    assert rows == []
    for scores in report["test_metrics"].values():
        assert scores["sample_count"]["value"] == 0
        assert scores["brier_score"]["value"] is None


def test_baseline_and_reliability_use_exact_same_evaluated_test_rows(experiment):
    _, report, rows = experiment
    evaluated = [row for row in rows if row["evaluated"]]
    target = np.array([row["observed_event"]["value"] for row in evaluated], dtype=float)
    prevalence = report["training_event_rate"]["value"]
    baseline = report["test_metrics"]["training_prevalence_baseline"]
    assert baseline["brier_score"]["value"] == pytest.approx(np.mean((prevalence - target) ** 2))
    for scores in report["test_metrics"].values():
        assert scores["sample_count"]["value"] == len(evaluated)
        assert sum(bin_["sample_count"]["value"] for bin_ in scores["reliability"]) == len(evaluated)
        assert len(scores["reliability"]) == 10
        for bin_ in scores["reliability"]:
            if not bin_["sample_count"]["value"]:
                assert bin_["mean_probability"]["value"] is None
                assert bin_["observed_event_frequency"]["value"] is None


def assert_sourced_numbers(value):
    if isinstance(value, dict):
        if "value" in value:
            assert {"value", "source_type", "ref"}.issubset(value)
            assert value["source_type"] in {"data", "model", "assumption"}
            assert value["ref"].strip()
            if value["value"] is not None:
                assert isinstance(value["value"], (int, float, bool))
                assert np.isfinite(value["value"])
        else:
            for child in value.values():
                assert_sourced_numbers(child)
    elif isinstance(value, list):
        for child in value:
            assert_sourced_numbers(child)
    else:
        assert isinstance(value, (str, bool)) or value is None


def test_reports_and_probabilities_are_sourced_json_without_false_claims(experiment):
    bundle, report, rows = experiment
    json.dumps(experiment, allow_nan=False)
    assert_sourced_numbers(report)
    assert_sourced_numbers(rows)
    assert report["status"] == "research_only_no_production_promotion"
    assert report["forecast_asof_verified"] is False
    assert "not sixty minutes" in report["limitation"]
    assert "not the product's model-confidence" in report["limitation"]
    for origin in bundle["manifest"]["input_sources"].values():
        assert origin["source_type"] == "assumption"
    assert "assumption-sourced" in next(row["raw_probability"]["ref"] for row in rows if row["raw_probability"]["value"] is not None)
    for row in rows:
        for name in ("raw_probability", "calibrated_probability"):
            value = row[name]["value"]
            if value is not None:
                assert 0 <= value <= 1
                assert row[name]["source_type"] == "assumption"
    for metric_group in report["test_metrics"].values():
        assert metric_group["brier_score"]["source_type"] == "assumption"


def test_fit_is_reproducible_canonical_and_leaves_inputs_untouched(experiment):
    actuals, events = inputs()
    saved_actuals, saved_events = actuals.copy(deep=True), events.copy(deep=True)
    result = fit(actuals.iloc[::-1], events.iloc[::-1])
    assert json.dumps(result, sort_keys=True, allow_nan=False) == json.dumps(experiment, sort_keys=True, allow_nan=False)
    pd.testing.assert_frame_equal(actuals, saved_actuals)
    pd.testing.assert_frame_equal(events, saved_events)


def test_json_roundtrip_prediction_never_fits_reads_cache_or_follows_refs(experiment):
    actuals, _ = inputs()
    restored = json.loads(json.dumps(experiment[0], allow_nan=False))
    targets = [row["timestamp_utc"] for row in experiment[2]]
    with patch("sklearn.linear_model.LogisticRegression.fit", side_effect=AssertionError("No fitting")), \
         patch.object(pd, "read_parquet", side_effect=AssertionError("No cache I/O")), \
         patch.object(wind.ingest, "fetch_public_evidence", side_effect=AssertionError("No network")):
        rows = wind.predict_wind_event_classifier(restored, actuals, sources=SOURCES, target_timestamps=targets)
    assert probabilities(rows) == probabilities(experiment[2])
    assert probabilities(rows, "calibrated_probability") == probabilities(experiment[2], "calibrated_probability")


@pytest.mark.parametrize("kind", ["site_scope", "rule_labels", "model_generated_labels", "incomplete_positive", "numeric_target"])
def test_invalid_target_contract_cannot_become_an_independent_label(kind):
    actuals, events = inputs()
    if kind == "site_scope":
        events.loc[0, "location_id"] = "OKGE"
    elif kind == "rule_labels":
        events.attrs["method"] = "high_wind_low_local_price_v1"
    elif kind == "model_generated_labels":
        events.attrs["source"] = {"source_type": "model", "ref": "another model's targets"}
    elif kind == "incomplete_positive":
        events.loc[0, "evaluable_five_minute_samples"] = 11
    else:
        events["wind_curtailment_event"] = events.wind_curtailment_event.astype(int)
    with pytest.raises(ValueError):
        fit(actuals, events)


@pytest.mark.parametrize("kind", ["zone", "duplicate", "naive_time", "infinity", "boolean", "negative"])
def test_invalid_actuals_are_rejected(kind):
    actuals, _ = inputs()
    if kind == "zone":
        actuals.loc[0, "location_id"] = "OKGE"
    elif kind == "duplicate":
        actuals = pd.concat([actuals, actuals.iloc[[0]]])
    elif kind == "naive_time":
        actuals["timestamp_utc"] = actuals.timestamp_utc.dt.tz_localize(None)
    else:
        actuals["system_wind_mw"] = actuals.system_wind_mw.astype(object)
        actuals.loc[0, "system_wind_mw"] = {"infinity": np.inf, "boolean": True, "negative": -1.}[kind]
    with pytest.raises(ValueError):
        wind.prepare_wind_classifier_features(actuals, sources=SOURCES)


def reidentify(bundle):
    payload = {key: value for key, value in bundle.items() if key != "model_id"}
    bundle["model_id"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@pytest.mark.parametrize("rehash", [False, True])
def test_invalid_or_tampered_json_parameters_are_rejected(experiment, rehash):
    bundle = deepcopy(experiment[0])
    bundle["standardizer"]["scale"][0] = 0.
    if rehash:
        reidentify(bundle)
    actuals, _ = inputs()
    with pytest.raises(ValueError):
        wind.predict_wind_event_classifier(bundle, actuals, sources=SOURCES)


def test_missing_requested_lags_are_explicitly_unavailable(experiment):
    actuals, _ = inputs()
    rows = wind.predict_wind_event_classifier(experiment[0], actuals, sources=SOURCES,
                                              target_timestamps=["2025-01-01T00:00:00Z"])
    assert rows[0]["raw_probability"]["value"] is None
    assert rows[0]["calibrated_probability"]["value"] is None
    assert "missing exact-time lag" in rows[0]["raw_probability"]["ref"]


@pytest.mark.parametrize("field,value", [
    ("status", "production"), ("target_method", "self_generated_rule"),
    ("split_bounds", {}), ("limitation", "accurate site forecast"),
    ("calibration_minimum_per_class", 1), ("calibration_minimum_per_class", 20.0),
    ("calibration_requested", False), ("calibration_requested", 1),
    ("calibration_status", "insufficient_september_class_support"),
    ("policy_source", {"source_type": "data", "ref": "experiment policy"}),
    ("training_cohort_sha256", "missing"), ("calibration_cohort_sha256", None),
    ("fit_parameters", {}),
])
def test_rehashed_manifest_cannot_change_the_declared_experiment(experiment, field, value):
    bundle = deepcopy(experiment[0])
    bundle["manifest"][field] = value
    reidentify(bundle)
    actuals, _ = inputs()
    with pytest.raises(ValueError):
        wind.predict_wind_event_classifier(bundle, actuals, sources=SOURCES)


@pytest.mark.parametrize("name", ["system_wind_mw", "system_load_mw", "labels"])
def test_rehashed_bundle_cannot_present_model_outputs_as_independent_observations(experiment, name):
    bundle = deepcopy(experiment[0])
    bundle["manifest"]["input_sources"][name] = {"source_type": "model", "ref": "generated observations"}
    reidentify(bundle)
    actuals, _ = inputs()
    with pytest.raises(ValueError):
        wind.predict_wind_event_classifier(bundle, actuals, sources=SOURCES)


def test_rehashed_missing_calibrator_cannot_claim_successful_calibration(experiment):
    bundle = deepcopy(experiment[0])
    bundle["calibrator"] = None
    reidentify(bundle)
    actuals, _ = inputs()
    with pytest.raises(ValueError):
        wind.predict_wind_event_classifier(bundle, actuals, sources=SOURCES)


def test_all_data_empirical_inputs_use_model_kind_but_queries_cannot_upgrade_assumptions():
    # Exercise the provenance branch only; these test-only inputs are never published.
    actuals, events = inputs()
    data_sources = {key: {"source_type": "data", "ref": "test-only declared observation source"} for key in SOURCES}
    events.attrs["source"] = {"source_type": "data", "ref": "test-only declared VER source"}
    bundle, report, rows = wind.fit_wind_event_classifier(actuals, events, sources=data_sources)
    assert bundle["manifest"]["policy_source"]["source_type"] == "assumption"
    assert report["test_metrics"]["raw"]["brier_score"]["source_type"] == "model"
    assert report["test_metrics"]["training_prevalence_baseline"]["brier_score"]["source_type"] == "model"
    assert all(row["raw_probability"]["source_type"] == "model" for row in rows if row["evaluated"])
    targets = [row["timestamp_utc"] for row in rows if row["evaluated"]]
    assumed_queries = wind.predict_wind_event_classifier(bundle, actuals, sources=SOURCES, target_timestamps=targets)
    assert all(row["raw_probability"]["source_type"] == "assumption" for row in assumed_queries)
    assert all("assumption-sourced" in row["raw_probability"]["ref"] for row in assumed_queries)


def test_exact_split_boundaries_carry_only_preceding_actuals_and_exclude_next_year():
    actuals, events = inputs()
    extra = pd.DatetimeIndex([stamp for start in ("2024-08-31", "2024-09-30", "2024-12-31")
                              for stamp in pd.date_range(start, periods=24, freq="h", tz="UTC")]
                             + [pd.Timestamp("2025-01-01T00:00:00Z")])
    added_actuals = pd.DataFrame({"timestamp_utc": extra, "location_id": "SPP_SYSTEM",
                                  "system_wind_mw": np.arange(len(extra)) + 30., "system_load_mw": 100.})
    added_events = pd.DataFrame({"timestamp_utc": extra, "location_id": "SPP_SYSTEM",
                                 "wind_curtailment_event": pd.array(np.arange(len(extra)) % 2 == 0, dtype="boolean"),
                                 "observed_five_minute_samples": 12, "evaluable_five_minute_samples": 12})
    origins = deepcopy(events.attrs)
    actuals = pd.concat([actuals, added_actuals], ignore_index=True)
    events = pd.concat([events, added_events], ignore_index=True)
    events.attrs = origins
    targets = ["2024-09-01T00:00:00Z", "2024-10-01T00:00:00Z", "2025-01-01T00:00:00Z"]
    features = wind.prepare_wind_classifier_features(actuals, sources=SOURCES, target_timestamps=targets)
    assert features.system_wind_mw_lag_1h.tolist() == [53., 77., 101.]
    assert features.system_wind_mw_lag_24h.tolist() == [30., 54., 78.]
    _, report, rows = fit(actuals, events)
    assert report["cohorts"]["train"]["supplied_label_hours"]["value"] == 120
    assert report["cohorts"]["calibration"]["supplied_label_hours"]["value"] == 120
    assert report["cohorts"]["test"]["supplied_label_hours"]["value"] == 120
    assert report["data_manifest"]["outside_fixed_windows"]["value"] == 1
    assert rows[0]["timestamp_utc"] == "2024-10-01T00:00:00+00:00"
    assert rows[0]["evaluated"] is True
    assert all(pd.Timestamp(row["timestamp_utc"]) < pd.Timestamp("2025-01-01T00:00:00Z") for row in rows)


def test_prediction_rows_explain_cohort_role_and_outside_vintage_without_inventing_validation(experiment):
    actuals, _ = inputs()
    targets = ["2023-12-31T23:00:00Z", "2024-01-02T00:00:00Z", "2024-09-02T00:00:00Z",
               "2024-10-02T00:00:00Z", "2025-01-01T00:00:00Z"]
    rows = wind.predict_wind_event_classifier(experiment[0], actuals, sources=SOURCES, target_timestamps=targets)
    assert [row["target_period_role"] for row in rows] == ["outside_evaluated_period", "training_period",
        "calibration_period", "heldout_2024_autumn", "outside_evaluated_period"]
    for row in rows:
        assert row["limitation"] == experiment[0]["manifest"]["limitation"]
        assert row["forecast_asof_verified"] is False
        assert "does not assert this row" in row["period_role_basis"]
        assert row["model_id"] == experiment[0]["model_id"]
        assert row["model_id"] in row["raw_probability"]["ref"]


def test_equivalent_source_mapping_order_keeps_report_and_prediction_json_identical(experiment):
    actuals, events = inputs()
    sources = {key: dict(reversed(list(value.items()))) for key, value in reversed(list(SOURCES.items()))}
    events.attrs["source"] = dict(reversed(list(events.attrs["source"].items())))
    result = wind.fit_wind_event_classifier(actuals, events, sources=sources)
    assert json.dumps(result, allow_nan=False) == json.dumps(experiment, allow_nan=False)
