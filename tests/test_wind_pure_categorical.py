"""Pure public-entrypoint regressions; every fixture is synthetic and unfitted."""
from copy import deepcopy
import hashlib
import json
import socket

import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from pipeline import wind_signal as wind

SOURCE = {"source_type": "assumption", "ref": "synthetic categorical representation fixture; no measurements or fitted model"}
SOURCES = {name: dict(SOURCE) for name in ("system_wind_mw", "system_load_mw")}


@pytest.fixture(autouse=True)
def no_training_data_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure categorical regression must not fit, read data, fetch, or publish")
    for obj, name in [(wind, "fit_wind_event_classifier"), (wind, "publish_wind_replay"),
                      (LogisticRegression, "fit"), (StandardScaler, "fit"),
                      (wind.ingest, "fetch_public_evidence"), (pd, "read_parquet"),
                      (pd, "read_csv"), (socket, "create_connection"), (socket, "getaddrinfo"), (socket.socket, "connect")]:
        monkeypatch.setattr(obj, name, forbidden)


def same(actual, expected):
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert actual.attrs == expected.attrs


def unchanged(actuals, originals):
    for actual, original in zip(actuals, originals):
        same(actual, original)


def cached_inputs():
    starts = pd.date_range("2024-01-01", periods=24, freq="5min", tz="UTC")
    hours = starts[::12]
    generation = pd.DataFrame({"Interval Start": starts, "Interval End": starts + pd.Timedelta(5, unit="min"), "Wind": 60.})
    load = pd.DataFrame({"timestamp_utc": hours, "location_id": "SPP_SYSTEM", "load_mw": 100.})
    prices = pd.DataFrame({"Interval Start": hours, "Interval End": hours + pd.Timedelta(1, unit="h"),
                           "Market": "DA", "Location": "EXACT_NODE", "LMP": -1.})
    for frame in (generation, load, prices):
        frame.attrs["source"] = dict(SOURCE)
    return generation, load, prices


def prepare(generation, load, prices, *, historical=False):
    return wind.prepare_wind_inputs(generation, load, prices, price_locations={"A": "EXACT_NODE"},
                                    market="DA", generation_format="historical" if historical else "gridstatus")


def values(length, value, shape):
    if shape == "constant":
        return [value] * length
    if shape == "nullable":
        return [str(value)] * (length - 1) + [None]
    return [None] * length


@pytest.mark.parametrize("column", ["Wind", "LMP", "Wind Market", "Wind Self"])
@pytest.mark.parametrize("shape", ["constant", "nullable"])
def test_adapter_categorical_power_and_prices_match_ordinary_values(column, shape):
    generation, load, prices = cached_inputs()
    historical = column.startswith("Wind ")
    if historical:
        generation = pd.DataFrame({"GMT MKT Interval": generation["Interval Start"], "Wind Market": 40.,
                                   "Wind Self": 20., "Solar Market": 1., "Solar Self": 1.})
        generation.attrs["source"] = dict(SOURCE)
    target = prices if column == "LMP" else generation
    target[column] = pd.Series(values(len(target), float(target[column].iloc[0]), shape), dtype=object)
    expected = prepare(generation, load, prices, historical=historical)
    target[column] = target[column].astype("category")
    originals = deepcopy((generation, load, prices))
    try:
        actual = prepare(generation, load, prices, historical=historical)
        same(actual, expected)
        if shape == "nullable":
            assert pd.isna(actual["lmp_usd_mwh" if column == "LMP" else "system_wind_mw"].iloc[-1])
    finally:
        unchanged((generation, load, prices), originals)


@pytest.mark.parametrize("categories", [["SPP_SYSTEM"], ["SPP_SYSTEM", "UNUSED"]])
def test_categorical_system_load_identifier_preserves_exact_footprint(categories):
    generation, load, prices = cached_inputs()
    expected = prepare(generation, load, prices)
    load["location_id"] = pd.Categorical(load.location_id, categories=categories)
    originals = deepcopy((generation, load, prices))
    try:
        same(prepare(generation, load, prices), expected)
    finally:
        unchanged((generation, load, prices), originals)


@pytest.mark.parametrize("ids", [[None, None], ["SPP_SYSTEM", None], ["", ""], [7, 7]])
def test_invalid_categorical_footprints_remain_invalid(ids):
    generation, load, prices = cached_inputs()
    load["location_id"] = pd.Categorical(ids)
    originals = deepcopy((generation, load, prices))
    try:
        with pytest.raises(ValueError, match="nonempty name"):
            prepare(generation, load, prices)
    finally:
        unchanged((generation, load, prices), originals)


def ver_input():
    return pd.DataFrame({"GMTIntervalEnding": pd.date_range("2025-01-01T00:05Z", periods=24, freq="5min"),
                         **{name: 0. for name in wind.VER_WIND_COLUMNS}})


def make_labels(raw):
    return wind.prepare_wind_curtailment_labels(raw, origin=SOURCE, system_scope="SPP_SYSTEM", scope_source=SOURCE)


@pytest.mark.parametrize("timestamp", ["2025-01-01T00:05:00Z", "01/01/2025 00:05:00", pd.Timestamp("2025-01-01T00:05:00Z")],
                         ids=["explicit-UTC-string", "declared-GMT-string", "aware-Timestamp"])
def test_single_categorical_gmt_interval_retains_partial_hour_unknown(timestamp):
    raw = ver_input().iloc[:1].copy()
    raw["GMTIntervalEnding"] = pd.Series([timestamp], dtype=object)
    expected = make_labels(raw)
    raw["GMTIntervalEnding"] = raw.GMTIntervalEnding.astype("category")
    original = deepcopy(raw)
    try:
        result = make_labels(raw)
        same(result, expected)
        assert result.timestamp_utc.tolist() == [pd.Timestamp("2025-01-01T00:00:00Z")]
        assert result.observed_five_minute_samples.tolist() == [1]
        assert result.evaluable_five_minute_samples.tolist() == [1]
        assert result.wind_curtailment_event.isna().all()
        assert result.attrs["source"]["source_type"] == "assumption"
    finally:
        same(raw, original)


@pytest.mark.parametrize("timestamp", [0, 0., True], ids=["integer", "float", "boolean"])
def test_categorical_numeric_interval_end_remains_invalid(timestamp):
    raw = ver_input().iloc[:1].copy()
    raw["GMTIntervalEnding"] = pd.Categorical([timestamp])
    original = deepcopy(raw)
    try:
        with pytest.raises(ValueError, match="cannot be numeric or boolean"):
            make_labels(raw)
    finally:
        same(raw, original)


@pytest.mark.parametrize("column", wind.VER_WIND_COLUMNS)
@pytest.mark.parametrize("shape", ["constant", "nullable", "missing"])
def test_independent_ver_numeric_categories_keep_complete_zero_and_unknown(column, shape):
    raw = ver_input()
    raw[column] = pd.Series(values(len(raw), 0., shape), dtype=object)
    expected = make_labels(raw)
    raw[column] = raw[column].astype("category")
    original = deepcopy(raw)
    try:
        result = make_labels(raw)
        same(result, expected)
        assert result.attrs["source"]["source_type"] == "assumption"
        assert result.wind_curtailment_event.isna().sum() == {"constant": 0, "nullable": 1, "missing": 2}[shape]
    finally:
        same(raw, original)


def hourly_inputs():
    return pd.DataFrame({"timestamp_utc": pd.date_range("2024-12-31", periods=48, freq="h", tz="UTC"),
                         "location_id": "SPP_SYSTEM", "system_wind_mw": 50., "system_load_mw": 100.})


@pytest.mark.parametrize("column", ["system_wind_mw", "system_load_mw"])
@pytest.mark.parametrize("shape", ["constant", "nullable", "missing"])
def test_exact_lag_preparation_accepts_categorical_reported_inputs(column, shape):
    hourly = hourly_inputs()
    hourly[column] = pd.Series(values(len(hourly), float(hourly[column].iloc[0]), shape), dtype=object)
    if shape == "nullable":
        hourly.loc[24, column] = None
    targets = hourly.timestamp_utc
    expected = wind.prepare_wind_classifier_features(hourly, sources=SOURCES, target_timestamps=targets)
    hourly[column] = hourly[column].astype("category")
    original = deepcopy(hourly)
    try:
        result = wind.prepare_wind_classifier_features(hourly, sources=SOURCES, target_timestamps=targets)
        same(result, expected)
        assert result.attrs["sources"] == SOURCES
        assert result[f"{column}_lag_24h"].iloc[:24].isna().all()
        if shape == "nullable":
            assert pd.isna(result[f"{column}_lag_1h"].iloc[25])
    finally:
        same(hourly, original)


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


def replay(bundle, hourly, labels):
    return wind.evaluate_wind_event_classifier(bundle, hourly, labels, sources=SOURCES,
        baseline_probability={"value": .5, **SOURCE}, start_utc="2025-01-01T00:00Z", end_exclusive_utc="2025-01-02T00:00Z")


def independent_labels(pattern):
    patterns = {"true": [True] * 24, "false": [False] * 24, "mixed": [True, False] * 12,
                "nullable": [True, False] * 11 + [None, None], "missing": [None] * 24}
    events = patterns[pattern]
    frame = pd.DataFrame({"timestamp_utc": pd.date_range("2025-01-01", periods=24, freq="h", tz="UTC"),
                          "location_id": "SPP_SYSTEM", "wind_curtailment_event": pd.Series(events, dtype=object),
                          "observed_five_minute_samples": 12,
                          "evaluable_five_minute_samples": [12 if value is not None else 0 for value in events]})
    frame.attrs = {"source": dict(SOURCE), "system_scope": "SPP_SYSTEM",
                   "method": "reported_system_wind_curtailment_any_category_v1"}
    return frame


@pytest.mark.parametrize("pattern", ["true", "false", "mixed", "nullable", "missing"])
def test_unfitted_public_replay_accepts_equivalent_categorical_boolean_labels(bundle, pattern):
    hourly, labels = hourly_inputs(), independent_labels(pattern)
    expected = replay(bundle, hourly, labels)
    labels["wind_curtailment_event"] = labels.wind_curtailment_event.astype("category")
    originals, original_bundle = deepcopy((hourly, labels)), deepcopy(bundle)
    try:
        actual = replay(bundle, hourly, labels)
        assert json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
        assert actual[0]["coverage"]["eligible_hours"]["value"] == {"missing": 0, "nullable": 22}.get(pattern, 24)
    finally:
        unchanged((hourly, labels), originals)
        assert bundle == original_bundle


@pytest.mark.parametrize("target,bad", [
    ("grid_wind", True), ("historical_wind", 1j), ("ver", pd.Timestamp("2025-01-01")),
    ("features", pd.Timedelta(5, unit="ns")), ("features", True), ("events", 1), ("events", "true"),
], ids=["boolean-grid-wind", "complex-historical-wind", "timestamp-ver", "duration-feature", "boolean-feature", "numeric-event", "string-event"])
def test_invalid_categorical_observations_remain_rejected(bundle, target, bad):
    if target in {"grid_wind", "historical_wind"}:
        generation, load, prices = cached_inputs()
        historical = target == "historical_wind"
        column = "Wind Market" if historical else "Wind"
        if historical:
            generation = pd.DataFrame({"GMT MKT Interval": generation["Interval Start"], "Wind Market": 40.,
                                       "Wind Self": 20., "Solar Market": 1., "Solar Self": 1.})
        generation[column] = pd.Categorical([bad] * len(generation))
        frames = (generation, load, prices)
        call = lambda: prepare(*frames, historical=historical)
    elif target == "ver":
        raw = ver_input()
        raw[wind.VER_WIND_COLUMNS[0]] = pd.Categorical([bad] * len(raw))
        frames, call = (raw,), lambda: make_labels(raw)
    elif target == "features":
        hourly = hourly_inputs()
        hourly["system_wind_mw"] = pd.Categorical([bad] * len(hourly))
        frames, call = (hourly,), lambda: wind.prepare_wind_classifier_features(hourly, sources=SOURCES)
    else:
        hourly, labels = hourly_inputs(), independent_labels("true")
        labels["wind_curtailment_event"] = pd.Categorical([bad] * len(labels))
        frames, call = (hourly, labels), lambda: replay(bundle, hourly, labels)
    originals = deepcopy(frames)
    try:
        with pytest.raises(ValueError):
            call()
    finally:
        unchanged(frames, originals)
