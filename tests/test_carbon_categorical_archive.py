"""Categorical observations preserve numeric values, unknowns and source evidence."""
from copy import deepcopy
from datetime import time
import json
import socket

import numpy as np
import pandas as pd
import pytest

import pipeline.carbon as carbon

ORIGIN = {"source_type": "data", "ref": ' {"dataset":"authored software fixture", "year":2025} '}
TIMING = {"source_type": "assumption", "ref": "Authored observation-time fixture; no measured-energy claim"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network in categorical regressions")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def raw_archive(values, *, categorical=True):
    raw = pd.DataFrame({"GMT MKT Interval": pd.date_range("2025-01-01", periods=len(values), freq="5min", tz="UTC")})
    for fuel in carbon.SPP_ARCHIVE_FUELS:
        raw[fuel + " Market"] = 0.0
        raw[fuel + " Self"] = 0.0
    raw["Wind Market"] = pd.Categorical(values) if categorical else pd.Series(values, dtype=object)
    raw.attrs = {"scope": "authored categorical fixture", "original_source": deepcopy(ORIGIN)}
    return raw


def equivalent_result(raw, origin=None):
    origin = deepcopy(ORIGIN if origin is None else origin)
    saved_raw, saved_origin, saved_timing = deepcopy((raw, origin, TIMING))
    plain = raw.copy(deep=True)
    for column in plain:
        if isinstance(plain[column].dtype, pd.CategoricalDtype):
            plain[column] = plain[column].astype(object)
    expected = carbon.normalize_spp_generation_archive(plain, generation_source=origin, timing_source=TIMING)
    actual = carbon.normalize_spp_generation_archive(raw, generation_source=origin, timing_source=TIMING)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert actual.attrs == expected.attrs
    pd.testing.assert_frame_equal(raw, saved_raw, check_exact=True)
    assert raw.attrs == saved_raw.attrs
    assert origin == saved_origin and TIMING == saved_timing
    assert set(actual.fuel) == set(carbon.SPP_ARCHIVE_FUELS)
    for row in actual.itertuples(index=False):
        assert row.source_type == "assumption" and row.unit == "MWh"
        evidence = json.loads(row.ref)
        assert origin in evidence["inputs"] and TIMING in evidence["inputs"]
        assert evidence["fuel"] == row.fuel
        assert evidence["hour_utc"] == row.timestamp_utc
    return actual.loc[actual.fuel.eq("Wind")].reset_index(drop=True)


@pytest.mark.parametrize("values,expected,complete_samples", [
    ([60.0] * 12, 60.0, 12),
    ([60.0, 61.0] * 6, 60.5, 12),
    (["60"] * 12, 60.0, 12),
    ([60.0] * 11 + [None], None, 11),
    (["60"] * 11 + [None], None, 11),
    ([None] * 12, None, 0),
], ids=["constant", "two_categories", "numeric_string", "nullable_numeric", "nullable_string", "all_unknown"])
def test_categorical_observations_match_plain_frame_sources_and_unknownness(values, expected, complete_samples):
    result = equivalent_result(raw_archive(values)).iloc[0]
    assert result.generation_mwh == expected
    assert result.generation_status == ("complete" if expected is not None else "incomplete_observations")
    evidence = json.loads(result.ref)
    assert evidence["complete_samples"] == complete_samples
    assert evidence["component_samples"]["Wind Market"] == complete_samples
    assert evidence["component_samples"]["Wind Self"] == 12


def test_missing_component_remains_unknown_for_a_valid_categorical_observation():
    raw = raw_archive([60.0] * 12).drop(columns="Wind Self")
    result = equivalent_result(raw).iloc[0]
    assert result.generation_mwh is None and result.generation_status == "missing_component"
    evidence = json.loads(result.ref)
    assert evidence["missing_components"] == ["Wind Self"]
    assert evidence["complete_samples"] == 0


def test_signed_categorical_sample_keeps_negative_net_diagnostic_and_unknown_energy():
    result = equivalent_result(raw_archive([-1.0] + [60.0] * 11)).iloc[0]
    assert result.generation_mwh is None and result.generation_status == "negative_net_generation"
    evidence = json.loads(result.ref)
    assert evidence["complete_samples"] == 12 and evidence["negative_net_samples"] == 1
    assert evidence["negative_component_samples"]["Wind Market"] == 1


@pytest.mark.parametrize("value,match", [
    (True, "boolean"), (np.bool_(False), "boolean"),
    (complex(1.0, 2.0), "complex or temporal"),
    (pd.Timestamp("2025-01-01T00:00:00Z"), "complex or temporal"),
    (pd.Timedelta(1, unit="h"), "complex or temporal"),
    (time(12, 0), "complex or temporal"),
], ids=["boolean_true", "numpy_boolean_false", "complex", "datetime", "timedelta", "time"])
def test_invalid_categorical_quantities_raise_domain_value_error_without_mutation(value, match):
    raw = raw_archive([value] * 12)
    saved = raw.copy(deep=True)
    with pytest.raises(ValueError, match=match):
        carbon.normalize_spp_generation_archive(raw, generation_source=ORIGIN, timing_source=TIMING)
    pd.testing.assert_frame_equal(raw, saved, check_exact=True)
    assert raw.attrs == saved.attrs


@pytest.mark.parametrize("kind", ["data", "model", "assumption"])
def test_categorical_representation_preserves_original_source_kind_and_exact_ref(kind):
    origin = {**ORIGIN, "source_type": kind}
    result = equivalent_result(raw_archive([60.0] * 12), origin=origin).iloc[0]
    assert result.generation_mwh == 60.0
    evidence = json.loads(result.ref)
    assert origin in evidence["inputs"]
    assert result.source_type == "assumption"
