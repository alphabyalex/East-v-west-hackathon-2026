"""The public cache adapter requires a named load footprint before normalization."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal


def adapter_inputs(generation_format):
    samples = pd.date_range("2024-01-01", periods=24, freq="5min", tz="UTC")
    hours = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    if generation_format == "historical":
        generation = pd.DataFrame({
            "GMT MKT Interval": samples,
            "Wind Market": 40., "Wind Self": 20.,
            "Solar Market": 1., "Solar Self": 1.,
        })
    else:
        generation = pd.DataFrame({
            "Interval Start": samples,
            "Interval End": samples + pd.Timedelta(5, unit="min"),
            "Wind": 60.,
        })
    load = pd.DataFrame({
        "timestamp_utc": hours, "location_id": "SPP_SYSTEM", "load_mw": 100.,
    })
    prices = pd.DataFrame({
        "Interval Start": hours,
        "Interval End": hours + pd.Timedelta(1, unit="h"),
        "Market": "DA", "Location": "EXACT_NODE", "LMP": [-1., 10.],
    })
    return generation, load, prices


def prepare(inputs, generation_format):
    return wind_signal.prepare_wind_inputs(
        *inputs, price_locations={"EXPLICIT_POINT": "EXACT_NODE"},
        market="DA", generation_format=generation_format,
    )


@pytest.fixture
def forbid_normalization(monkeypatch):
    def unexpected_normalization(*args, **kwargs):
        raise AssertionError("An invalid footprint reached generation or price normalization.")

    monkeypatch.setattr(wind_signal, "_complete_hourly_power", unexpected_normalization)
    monkeypatch.setattr(wind_signal, "normalize_generation", unexpected_normalization)


@pytest.mark.parametrize("generation_format", ["gridstatus", "historical"])
@pytest.mark.parametrize("identifier,dtype", [
    pytest.param(None, object, id="none-object"),
    pytest.param(pd.NA, object, id="na-object"),
    pytest.param(pd.NA, "string[python]", id="na-python-string"),
    pytest.param(pd.NA, "string[pyarrow]", id="na-arrow-string"),
    pytest.param(np.nan, "float64", id="nan-float"),
    pytest.param("", object, id="empty-string"),
    pytest.param(" \t ", object, id="whitespace-string"),
    pytest.param(False, bool, id="false"),
    pytest.param(True, bool, id="true"),
    pytest.param(7, "int64", id="integer"),
    pytest.param(3.5, "float64", id="float"),
])
def test_invalid_load_footprint_rejects_before_normalization(
    generation_format, identifier, dtype, forbid_normalization,
):
    inputs = adapter_inputs(generation_format)
    inputs[1]["location_id"] = pd.Series([identifier, identifier], dtype=dtype)
    with pytest.raises(ValueError, match="footprint"):
        prepare(inputs, generation_format)


@pytest.mark.parametrize("generation_format", ["gridstatus", "historical"])
@pytest.mark.parametrize("dtype", [object, "string[python]", "string[pyarrow]"])
def test_custom_named_footprint_preserves_observations_and_inputs(generation_format, dtype):
    inputs = adapter_inputs(generation_format)
    # The adapter requires a name; it does not impose an alias or trim caller IDs.
    scope = " Verified custom system / exact name "
    inputs[1]["location_id"] = pd.Series([scope, scope], dtype=dtype)
    for name, frame in zip(("generation", "load", "prices"), inputs):
        frame.attrs = {"source": {"source_type": "assumption", "ref": f"synthetic {name} fixture"}}
    original = deepcopy(inputs)

    actual = prepare(inputs, generation_format)

    assert actual.timestamp_utc.tolist() == inputs[1].timestamp_utc.tolist()
    assert actual.system_load_mw.tolist() == [100., 100.]
    assert actual.system_wind_mw.tolist() == [60., 60.]
    assert actual.lmp_usd_mwh.tolist() == [-1., 10.]
    # The output location is the explicit price-point ID, not a guessed system alias.
    assert actual.location_id.tolist() == ["EXPLICIT_POINT", "EXPLICIT_POINT"]
    assert inputs[1].location_id.tolist() == [scope, scope]
    for before, after in zip(original, inputs):
        pd.testing.assert_frame_equal(before, after, check_exact=True)
        assert before.attrs == after.attrs


@pytest.mark.parametrize("generation_format", ["gridstatus", "historical"])
def test_mixed_named_footprints_still_reject_before_normalization(
    generation_format, forbid_normalization,
):
    inputs = adapter_inputs(generation_format)
    inputs[1]["location_id"] = ["VERIFIED_SYSTEM_A", "VERIFIED_SYSTEM_B"]
    with pytest.raises(ValueError, match="one system load footprint"):
        prepare(inputs, generation_format)
