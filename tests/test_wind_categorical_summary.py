"""Synthetic categorical representations retain values, sources and coverage."""
import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal as wind

SOURCE = {"source_type": "assumption", "ref": "Synthetic categorical summary test fixture, not grid observations"}
COLUMNS = ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")
ARGS = {"sources": {name: dict(SOURCE) for name in COLUMNS}, "wind_scope": "SPP_SYSTEM", "load_scope": "SPP_SYSTEM"}
CONTROLS = {"flexible_load_mw": {"value": 100., **SOURCE}, "available_fraction": {"value": .5, **SOURCE}}


def base(hours=2):
    return pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=hours, freq="h", tz="UTC"),
        "location_id": "A", "system_wind_mw": 60., "system_load_mw": 100., "lmp_usd_mwh": -1.})


def equivalent_results(frame):
    saved = frame.copy(deep=True)
    plain = frame.copy()
    for name in ("location_id", *COLUMNS):
        if isinstance(plain[name].dtype, pd.CategoricalDtype):
            plain[name] = plain[name].astype(object)
    expected_screen = wind.wind_oversupply_hours(plain, **ARGS)
    actual_screen = wind.wind_oversupply_hours(frame, **ARGS)
    assert actual_screen.attrs == expected_screen.attrs
    actual_screen["location_id"] = actual_screen.location_id.astype(object)
    expected_screen["location_id"] = expected_screen.location_id.astype(object)
    pd.testing.assert_frame_equal(actual_screen.sort_values(["location_id", "timestamp_utc"]).reset_index(drop=True),
                                  expected_screen.sort_values(["location_id", "timestamp_utc"]).reset_index(drop=True), check_exact=True)
    expected = wind.summarize_wind(plain, **ARGS, **CONTROLS)
    actual = wind.summarize_wind(frame, **ARGS, **CONTROLS)
    assert sorted(actual, key=lambda x: x["location_id"]) == sorted(expected, key=lambda x: x["location_id"])
    pd.testing.assert_frame_equal(frame, saved, check_exact=True)
    assert frame.attrs == saved.attrs
    return actual


@pytest.mark.parametrize("unused", [False, True])
def test_location_categories_do_not_create_empty_groups_or_break_validation(unused):
    frame = base()
    frame["location_id"] = pd.Categorical(frame.location_id, categories=["UNUSED", "A"] if unused else ["A"])
    result = equivalent_results(frame)
    assert [row["location_id"] for row in result] == ["A"]


@pytest.mark.parametrize("column", COLUMNS)
@pytest.mark.parametrize("kind", ["constant", "numeric_string_nullable", "all_unknown"])
def test_real_categorical_quantities_preserve_values_and_unknowns(column, kind):
    frame = base()
    value = frame[column].iloc[0]
    values = [value, value] if kind == "constant" else [str(value), None] if kind == "numeric_string_nullable" else [None, None]
    frame[column] = pd.Categorical(values)
    result = equivalent_results(frame)[0]
    assert result["unknown_hours"]["value"] == {"constant": 0, "numeric_string_nullable": 1, "all_unknown": 2}[kind]
    assert result["proxy_hours"]["value"] == {"constant": 2, "numeric_string_nullable": 1, "all_unknown": None}[kind]


@pytest.mark.parametrize("values", [["A", None], ["", ""], [" ", " "], [1, 1]],
                         ids=["missing", "empty", "whitespace", "numeric"])
def test_invalid_categorical_location_values_still_raise_domain_error(values):
    frame = base()
    frame["location_id"] = pd.Categorical(values)
    saved = frame.copy(deep=True)
    for call in (lambda: wind.wind_oversupply_hours(frame, **ARGS), lambda: wind.summarize_wind(frame, **ARGS, **CONTROLS)):
        with pytest.raises(ValueError, match="location_id"):
            call()
    pd.testing.assert_frame_equal(frame, saved, check_exact=True)


@pytest.mark.parametrize("column", COLUMNS)
def test_categorical_booleans_are_not_numeric_observations(column):
    frame = base()
    frame[column] = pd.Categorical([True, True])
    saved = frame.copy(deep=True)
    for call in (lambda: wind.wind_oversupply_hours(frame, **ARGS), lambda: wind.summarize_wind(frame, **ARGS, **CONTROLS)):
        with pytest.raises(ValueError, match="boolean"):
            call()
    pd.testing.assert_frame_equal(frame, saved, check_exact=True)


@pytest.mark.parametrize("coverage", ["complete", "missing_row", "unknown_flag"])
def test_unused_location_categories_cannot_change_annual_coverage(coverage):
    frame = base(8784)
    frame["location_id"] = pd.Categorical(frame.location_id, categories=["UNUSED", "A"])
    if coverage == "missing_row":
        frame = frame.drop(index=12)
    elif coverage == "unknown_flag":
        frame.loc[12, "lmp_usd_mwh"] = np.nan
    result = equivalent_results(frame)[0]
    assert result["wind_absorption_mwh_per_year"]["value"] == (8784 * 50. if coverage == "complete" else None)
