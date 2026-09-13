"""Synthetic public-boundary inputs; magnitudes here are not grid assumptions."""
from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from api.grid_impact import read_wind_scenario
from pipeline.carbon import wind_carbon
from pipeline.wind_signal import WindPolicy, wind_oversupply_hours, wind_scenario_mwh


ORIGIN = {"source_type": "assumption", "ref": "Synthetic numeric-boundary fixture, not physical grid data"}


def datum(value):
    return {"value": value, **ORIGIN}


@pytest.mark.parametrize("literal", ["18446744073709551616", "1000000000000000000000000000000"])
def test_finite_json_integer_and_float_have_identical_wind_accounting(literal):
    integer = json.loads(literal)
    floating = json.loads(literal + ".0")
    expected = float(Fraction(float(integer)) * Fraction(3) * Fraction(.5))
    assert wind_scenario_mwh(3, integer, .5) == expected
    assert wind_scenario_mwh(3, integer, .5) == wind_scenario_mwh(3, floating, .5)
    integer_carbon = wind_carbon(datum(integer), selection_source=ORIGIN)
    float_carbon = wind_carbon(datum(floating), selection_source=ORIGIN)
    assert json.dumps(integer_carbon, sort_keys=True, allow_nan=False) == json.dumps(float_carbon, sort_keys=True, allow_nan=False)
    assert integer_carbon["wind_operational_co2_kg"]["value"] == 0.
    assert integer_carbon["wind_operational_co2_kg"]["source_type"] == "assumption"


def test_real_fraction_controls_match_equivalent_binary64_controls():
    assert wind_scenario_mwh(3, Fraction(25, 2), Fraction(1, 2)) == 18.75
    assert WindPolicy(Fraction(1, 2), Fraction(-3, 2)).source == WindPolicy(.5, -1.5).source


@pytest.mark.parametrize("field,threshold,expected", [
    ("minimum_wind_share", Fraction(1, 3), [False, True, True]),
    ("maximum_lmp_usd_mwh", Fraction(1, 10), [True, True, False]),
])
def test_threshold_comparison_matches_its_published_binary64_provenance(field, threshold, expected):
    boundary = float(threshold)
    neighbors = [np.nextafter(boundary, -np.inf), boundary, np.nextafter(boundary, np.inf)]
    frame = pd.DataFrame({
        "timestamp_utc": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
        "location_id": "SYNTHETIC", "system_load_mw": 1.,
        "system_wind_mw": neighbors if field == "minimum_wind_share" else 1.,
        "lmp_usd_mwh": neighbors if field == "maximum_lmp_usd_mwh" else 0.,
    })
    kwargs = {"sources": {name: dict(ORIGIN) for name in ("system_load_mw", "system_wind_mw", "lmp_usd_mwh")},
              "wind_scope": "SPP_SYSTEM", "load_scope": "SPP_SYSTEM"}
    exact = wind_oversupply_hours(frame, policy=WindPolicy(**{field: threshold}), **kwargs)
    rounded = wind_oversupply_hours(frame, policy=WindPolicy(**{field: boundary}), **kwargs)
    assert exact.attrs["policy_source"] == rounded.attrs["policy_source"]
    assert exact.wind_oversupply_proxy.tolist() == rounded.wind_oversupply_proxy.tolist() == expected


@pytest.mark.parametrize("capacity,availability", [
    (np.int64(25), np.float32(.5)),
    (np.uint64(25), np.float64(.5)),
    (25, .5),
])
def test_normal_scalar_types_retain_the_same_public_result(capacity, availability):
    assert wind_scenario_mwh(4, capacity, availability) == 50.
    result = wind_carbon(datum(capacity), selection_source=ORIGIN)
    assert result == wind_carbon(datum(25.), selection_source=ORIGIN)


@pytest.mark.parametrize("bad", [10**400, -10**400, float("inf"), float("nan"), True, "100", 1 + 0j],
                         ids=["positive-overflow", "negative-overflow", "infinity", "nan", "boolean", "string", "complex"])
def test_invalid_scenario_capacity_fails_as_value_error_before_snapshot_io(tmp_path, monkeypatch, bad):
    def forbidden_read(*args, **kwargs):
        raise AssertionError("Invalid controls must be rejected before snapshot I/O")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ValueError, match="finite number"):
        read_wind_scenario("SYNTHETIC", tmp_path / "unused.snapshot.json",
                           flexible_load_mw=datum(bad), available_fraction=datum(.5))
    with pytest.raises(ValueError, match="finite number"):
        wind_carbon(datum(bad), selection_source=ORIGIN)


def test_finite_conversion_keeps_domain_limits_and_final_energy_overflow_checks():
    with pytest.raises(ValueError, match="outside its allowed range"):
        wind_scenario_mwh(1, Fraction(-1, 2), .5)
    with pytest.raises(ValueError, match="outside its allowed range"):
        wind_scenario_mwh(1, 100, Fraction(3, 2))
    with pytest.raises(ValueError, match="unrepresentable"):
        wind_scenario_mwh(4, 10**308, 1.)


def test_negative_carbon_energy_cannot_become_valid_by_rounding_to_negative_zero():
    negative_energy = Fraction(-1, 10**400)
    assert float(negative_energy) == 0.
    with pytest.raises(ValueError, match="nonnegative"):
        wind_carbon(datum(negative_energy), selection_source=ORIGIN)
