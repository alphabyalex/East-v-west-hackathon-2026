"""Synthetic unit fixtures are assumptions, never published SPP measurements."""
import json

import numpy as np
import pandas as pd
import pytest

from pipeline.wind_signal import WindPolicy, summarize_wind, wind_oversupply_hours


SOURCES = {name: {"source_type": "assumption", "ref": "synthetic unit fixture"}
           for name in ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")}
ARGS = dict(sources=SOURCES, wind_scope="SPP_SYSTEM", load_scope="SPP_SYSTEM")
LOAD = {"value": 100.0, "source_type": "assumption", "ref": "user flexible capacity"}
FRACTION = {"value": .5, "source_type": "assumption", "ref": "user available fraction"}


def observations(hours=4, start="2024-01-01"):
    return pd.DataFrame({"timestamp_utc": pd.date_range(start, periods=hours, freq="h", tz="UTC"),
                         "location_id": "CSWS", "system_wind_mw": 60.,
                         "system_load_mw": 100., "lmp_usd_mwh": -1.})


def summarize(frame, **overrides):
    return summarize_wind(frame, **{**ARGS, "flexible_load_mw": LOAD, "available_fraction": FRACTION, **overrides})


def test_screen_requires_both_high_wind_and_low_local_price():
    frame = observations()
    frame["system_wind_mw"] = [60, 60, 10, 50]
    frame["lmp_usd_mwh"] = [-5, 50, -5, 0]
    result = wind_oversupply_hours(frame, **ARGS)
    assert result.wind_oversupply_proxy.tolist() == [True, False, False, True]
    assert result.attrs["policy_source"]["source_type"] == "assumption"


@pytest.mark.parametrize("column", list(SOURCES))
def test_missing_evidence_is_unknown_not_false(column):
    frame = observations()
    frame.loc[0, column] = np.nan
    result = wind_oversupply_hours(frame, **ARGS)
    assert pd.isna(result.wind_oversupply_proxy.iloc[0])
    report = summarize(frame)[0]
    assert report["unknown_hours"]["value"] == 1
    assert report["proxy_hours"]["value"] == 3


def test_generic_binding_constraint_does_not_establish_wind_oversupply():
    frame = observations()
    frame["lmp_usd_mwh"] = 50.
    frame["binding_constraint_count"] = 10
    assert not wind_oversupply_hours(frame, **ARGS).wind_oversupply_proxy.any()


def test_system_context_cannot_be_divided_by_zone_load():
    with pytest.raises(ValueError, match="footprint"):
        wind_oversupply_hours(observations(), **{**ARGS, "load_scope": "CSWS"})


def test_same_system_hour_cannot_have_conflicting_wind_across_price_nodes():
    frame = observations()
    other = frame.assign(location_id="OTHER", system_wind_mw=70.)
    with pytest.raises(ValueError, match="Conflicting"):
        wind_oversupply_hours(pd.concat([frame, other]), **ARGS)


def test_each_price_node_is_evaluated_independently():
    frame = observations()
    other = frame.assign(location_id="OTHER", lmp_usd_mwh=20.)
    result = summarize(pd.concat([frame, other]))
    assert [row["proxy_hours"]["value"] for row in result] == [4, 0]


def test_zero_load_is_unknown_even_when_wind_exists():
    frame = observations()
    frame["system_load_mw"] = 0
    assert wind_oversupply_hours(frame, **ARGS).wind_oversupply_proxy.isna().all()
    assert summarize(frame)[0]["wind_absorption_mwh_in_observed_hours"]["value"] is None
    assert summarize(frame)[0]["proxy_hours"]["value"] is None


def test_missing_timestamps_are_counted_separately_from_unknown_observations():
    report = summarize(observations().iloc[[0, 3]])[0]
    assert report["observed_hours"]["value"] == 2
    assert report["unknown_hours"]["value"] == 0
    assert report["missing_interval_hours"]["value"] == 2


def test_ratio_overflow_is_rejected_instead_of_becoming_a_positive_signal():
    frame = observations().assign(system_load_mw=1e-308, system_wind_mw=1e308)
    with pytest.raises(ValueError, match="overflow"):
        wind_oversupply_hours(frame, **ARGS)


def test_excess_wind_over_load_is_not_used_as_measured_recoverable_power():
    frame = observations().assign(system_wind_mw=200)
    report = summarize(frame)[0]
    assert report["wind_absorption_mwh_in_observed_hours"]["value"] == 200.
    assert report["wind_absorption_mwh_in_observed_hours"]["source_type"] == "assumption"
    assert report["wind_absorption_mwh_per_year"]["value"] is None


def test_partial_year_is_not_extrapolated_to_a_year():
    report = summarize(observations())[0]
    assert report["wind_absorption_mwh_in_observed_hours"]["value"] == 200
    assert report["wind_absorption_mwh_per_year"]["value"] is None
    assert "unavailable" in report["wind_absorption_mwh_per_year"]["ref"]


@pytest.mark.parametrize("year,hours", [(2023, 8760), (2024, 8784)])
def test_annual_scenario_requires_complete_calendar_year(year, hours):
    report = summarize(observations(hours, f"{year}-01-01"))[0]
    assert report["wind_absorption_mwh_per_year"]["value"] == hours * 100 * .5
    assert report["wind_absorption_mwh_per_year"]["source_type"] == "assumption"


def test_missing_hour_prevents_annual_output():
    frame = observations(8784).drop(index=100)
    assert summarize(frame)[0]["wind_absorption_mwh_per_year"]["value"] is None


def test_unknown_hour_prevents_annual_output():
    frame = observations(8784)
    frame.loc[100, "lmp_usd_mwh"] = np.nan
    assert summarize(frame)[0]["wind_absorption_mwh_per_year"]["value"] is None


@pytest.mark.parametrize("bad", [True, np.inf, np.nan, -1, 1.1])
def test_policy_share_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        WindPolicy(minimum_wind_share=bad)


@pytest.mark.parametrize("bad", [True, np.inf, np.nan, -1])
def test_capacity_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        summarize(observations(), flexible_load_mw={**LOAD, "value": bad})


def test_duplicate_and_naive_timestamps_are_rejected():
    frame = observations()
    with pytest.raises(ValueError, match="Duplicate"):
        wind_oversupply_hours(pd.concat([frame, frame.iloc[[0]]]), **ARGS)
    frame["timestamp_utc"] = frame.timestamp_utc.dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone"):
        wind_oversupply_hours(frame, **ARGS)


def test_placeholder_reference_cannot_be_upgraded_to_data():
    sources = {**SOURCES, "lmp_usd_mwh": {"source_type": "data", "ref": "placeholder price"}}
    with pytest.raises(ValueError, match="Placeholder"):
        wind_oversupply_hours(observations(), **{**ARGS, "sources": sources})


def test_reproducible_order_and_no_avoided_claims_or_unsourced_numbers():
    frame = observations()
    first, second = summarize(frame), summarize(frame.sample(frac=1, random_state=2026))
    assert first == second
    assert "avoided" not in json.dumps(first).lower()
    for value in first[0].values():
        if isinstance(value, dict):
            assert set(value) == {"value", "source_type", "ref"}
    assert "unobserved" in first[0]["basis"]
