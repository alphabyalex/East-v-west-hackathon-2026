"""Synthetic unit fixtures are assumptions, never published SPP measurements."""
import json
import hashlib

import numpy as np
import pandas as pd
import pytest

from pipeline.wind_signal import WindPolicy, prepare_wind_inputs, read_cached_generation_archive, read_cached_wind_inputs, source, summarize_wind, wind_oversupply_hours


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


@pytest.mark.parametrize("bad", [60 + 5j, np.complex64(60 + 5j), pd.Timestamp(60, unit="ns"),
                                  np.datetime64(60, "ns"), pd.Timedelta(60, unit="ns"), np.timedelta64(60, "ns")])
@pytest.mark.parametrize("mixed", [False, True])
def test_scientific_quantity_types_cannot_become_real_mw_or_price(bad, mixed):
    values = pd.Series([bad] * 4) if not mixed else pd.Series(["60", pd.NA, bad, None], dtype=object)
    for column in SOURCES:
        frame = observations()
        frame[column] = values
        original = frame.copy(deep=True)
        with pytest.raises(ValueError, match="complex, datetime or timedelta"):
            wind_oversupply_hours(frame, **ARGS)
        with pytest.raises(ValueError, match="complex, datetime or timedelta"):
            summarize(frame)
        pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize("dtype,values", [
    (object, ["60", None, np.nan, pd.NA]),
    ("Float64", [60., None, np.nan, pd.NA]),
    ("Int64", [60, None, pd.NA, pd.NA]),
])
def test_real_numeric_strings_and_nullable_quantities_preserve_unknown_hours(dtype, values):
    frame = observations()
    frame["system_wind_mw"] = pd.Series(values, dtype=dtype)
    result = wind_oversupply_hours(frame, **ARGS)
    assert result.wind_oversupply_proxy.iloc[0]
    assert result.wind_oversupply_proxy.iloc[1:].isna().all()
    report = summarize(frame)[0]
    assert report["proxy_hours"]["value"] == 1
    assert report["unknown_hours"]["value"] == 3
    assert report["wind_absorption_mwh_in_observed_hours"]["value"] == 50.


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


@pytest.mark.parametrize("fraction,expected", [(0.0, 0.0), (1e-308, 4.0)])
def test_finite_available_capacity_does_not_overflow_before_applying_fraction(fraction, expected):
    report = summarize(observations(), flexible_load_mw={**LOAD, "value": 1e308},
                       available_fraction={**FRACTION, "value": fraction})[0]
    assert report["wind_absorption_mwh_in_observed_hours"]["value"] == pytest.approx(expected)
    assert report["scenario_inputs"]["flexible_load_mw"]["value"] == 1e308
    assert report["scenario_inputs"]["available_fraction"]["value"] == fraction


def test_unrepresentable_scenario_energy_remains_rejected():
    with pytest.raises(ValueError, match="finite"):
        summarize(observations(), flexible_load_mw={**LOAD, "value": 1e308},
                  available_fraction={**FRACTION, "value": 1.0})


def test_nonzero_subnormal_final_energy_does_not_disappear_in_partial_product():
    smallest_mw = float.fromhex('0x0.0000000000001p-1022')
    report = summarize(observations(), flexible_load_mw={**LOAD, "value": smallest_mw},
                       available_fraction={**FRACTION, "value": 0.5})[0]
    assert report["wind_absorption_mwh_in_observed_hours"]["value"] == 2 * smallest_mw
    assert report["scenario_inputs"]["flexible_load_mw"]["value"] == smallest_mw


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


def derived_source(kind, inputs):
    return {"source_type": kind, "ref": json.dumps({"method": "test calculation", "inputs": inputs})}


@pytest.mark.parametrize("outer,inner", [("data", "model"), ("data", "assumption"), ("model", "assumption")])
def test_nested_calculation_provenance_cannot_upgrade_an_input(outer, inner):
    nested = derived_source(outer, [{"value": 0.5, "source_type": inner, "ref": "declared test input"}])
    with pytest.raises(ValueError, match="upgrade a nested"):
        source(nested)
    with pytest.raises(ValueError, match="upgrade a nested"):
        summarize(observations(), available_fraction={"value": 0.5, **nested})


@pytest.mark.parametrize("outer,inner", [("data", "data"), ("model", "data"), ("model", "model"),
                                        ("assumption", "data"), ("assumption", "model"), ("assumption", "assumption")])
def test_honest_nested_provenance_retains_exact_source_and_reference(outer, inner):
    nested = derived_source(outer, [{"value": 0.5, "unit": "fraction", "source_type": inner, "ref": "declared test input"}])
    saved = dict(nested)
    assert source(nested) == saved
    assert nested == saved


def test_nested_provenance_checks_descendants_even_when_outermost_source_is_assumption():
    hidden = derived_source("data", [{"source_type": "assumption", "ref": "declared test coefficient"}])
    outer = derived_source("assumption", [{"value": 0.5, **hidden}])
    with pytest.raises(ValueError, match="upgrade a nested"):
        source(outer)


@pytest.mark.parametrize("ref", [
    '{"method":"count","inputs":[{"source_type":"assumption","source_type":"data","ref":"reviewed input"}]}',
    '{"method":"count","inputs":[{"value":1,"value":2,"source_type":"data","ref":"reviewed input"}]}',
    '{"method":"count","inputs":[{"source_type":"data","ref":"first input","ref":"second input"}]}',
    '{"method":"first method","method":"second method","inputs":[]}',
    '{"method":"count","inputs":[{"source_type":"assumption","ref":"declared input"}],"inputs":[]}',
])
def test_interpreted_provenance_rejects_duplicate_keys_without_hiding_earlier_sources(ref):
    origin = {"source_type": "data", "ref": ref}
    with pytest.raises(ValueError, match="Duplicate.*JSON key"):
        source(origin)
    with pytest.raises(ValueError, match="Duplicate.*JSON key"):
        summarize(observations(), available_fraction={"value": 0.5, **origin})
    # A nested string must be checked too, even beneath an honest outer source.
    with pytest.raises(ValueError, match="Duplicate.*JSON key"):
        source(derived_source("assumption", [{"value": 0.5, **origin}]))


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_interpreted_provenance_rejects_nonfinite_numeric_literals(literal):
    ref = '{"method":"count","inputs":[{"value":' + literal + ',"source_type":"data","ref":"reviewed input"}]}'
    with pytest.raises(ValueError, match="Nonfinite.*JSON number"):
        source({"source_type": "data", "ref": ref})


@pytest.mark.parametrize("ref", [
    '{"catalog": {"source_type": "assumption", "ref": "unrelated metadata"}}',
    '{"catalog": "first citation", "catalog": "second citation", "uninterpreted": NaN}',
    '{"method": "unknown schema", "inputs": ["opaque"]}',
    '{"method": "unknown schema", "inputs": [], "version": 2}',
    '{"method": "unknown schema", "inputs": [{"value": 1}]}',
    '{"unfinished JSON citation', '[{"source_type": "assumption"}]',
])
def test_unrecognized_json_references_remain_opaque(ref):
    origin = {"source_type": "data", "ref": ref}
    assert source(origin) == origin


@pytest.mark.parametrize("bad", [[], {}, None, "clause"])
def test_recognized_nested_input_rejects_invalid_source_kind(bad):
    nested = derived_source("assumption", [{"source_type": bad, "ref": "test input"}])
    with pytest.raises(ValueError, match="Unsupported source_type"):
        source(nested)


def test_wind_source_still_requires_exact_outer_source_keys():
    with pytest.raises(ValueError, match="exactly"):
        source({"value": 0.5, "source_type": "assumption", "ref": "test datum"})


def test_reproducible_order_and_no_avoided_claims_or_unsourced_numbers():
    frame = observations()
    first, second = summarize(frame), summarize(frame.sample(frac=1, random_state=2026))
    assert first == second
    assert "avoided" not in json.dumps(first).lower()
    def check_provenance(value):
        if isinstance(value, dict) and "value" in value:
            assert set(value) == {"value", "source_type", "ref"}
        elif isinstance(value, dict):
            for child in value.values():
                check_provenance(child)
        else:
            assert not isinstance(value, (int, float))
    check_provenance(first[0])
    assert "unobserved" in first[0]["basis"]


def test_provenance_keeps_exact_scenario_and_threshold_values():
    load, fraction, share, price = 100.000001, 0.500000001, 0.500000001, -0.123456789
    report = summarize(
        observations(), flexible_load_mw={**LOAD, "value": load},
        available_fraction={**FRACTION, "value": fraction},
        policy=WindPolicy(minimum_wind_share=share, maximum_lmp_usd_mwh=price),
    )[0]
    ref = report["wind_absorption_mwh_in_observed_hours"]["ref"]
    # Display rounding must never make two distinct assumptions share a source ref.
    for value in (load, fraction, share, price):
        assert repr(value) in ref
    assert report["wind_absorption_mwh_in_observed_hours"]["value"] == 4 * load * fraction
    assert report["energy_model"] == "declared_available_capacity_times_proxy_hours_v1"
    assert report["scenario_inputs"] == {
        "flexible_load_mw": {**LOAD, "value": load},
        "available_fraction": {**FRACTION, "value": fraction},
    }


def test_source_mapping_order_does_not_change_serialized_output():
    first = summarize(observations())
    second = summarize(observations().iloc[::-1], sources=dict(reversed(list(SOURCES.items()))))
    assert json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(second, sort_keys=True, allow_nan=False)


def cached_inputs():
    starts = pd.date_range("2024-01-01", periods=24, freq="5min", tz="UTC")
    generation = pd.DataFrame({"Interval Start": starts, "Interval End": starts + pd.Timedelta(5, unit="min"), "Wind": 60.})
    hours = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    load = pd.DataFrame({"timestamp_utc": hours, "location_id": "SPP_SYSTEM", "load_mw": 100.})
    prices = pd.DataFrame({"Interval Start": hours, "Interval End": hours + pd.Timedelta(1, unit="h"),
                           "Market": "DA", "Location": "EXPLICIT_NODE", "LMP": [-1., 10.]})
    return generation, load, prices


def prepare(generation, load, prices, **changes):
    return prepare_wind_inputs(generation, load, prices, **{"price_locations": {"CSWS": "EXPLICIT_NODE"}, "market": "DA", **changes})


def test_cache_adapter_means_power_and_uses_exact_selected_price():
    frame = prepare(*cached_inputs())
    assert frame.system_wind_mw.tolist() == [60., 60.]
    assert frame.lmp_usd_mwh.tolist() == [-1., 10.]
    assert wind_oversupply_hours(frame, **ARGS).wind_oversupply_proxy.tolist() == [True, False]


def test_cache_adapter_incomplete_subhourly_coverage_stays_unknown():
    generation, load, prices = cached_inputs()
    result = prepare(generation.drop(index=0), load, prices)
    assert pd.isna(result.system_wind_mw.iloc[0])
    assert pd.isna(wind_oversupply_hours(result, **ARGS).wind_oversupply_proxy.iloc[0])


def test_negative_net_wind_cannot_be_hidden_in_a_positive_hourly_mean():
    generation, load, prices = cached_inputs()
    generation.loc[0, "Wind"] = -1.
    with pytest.raises(ValueError, match="Negative net wind"):
        prepare(generation, load, prices)


@pytest.mark.parametrize("bad", [True, float("inf"), -21.])
def test_historical_wind_validates_raw_components_before_averaging(bad):
    generation, load, prices = cached_inputs()
    historic = pd.DataFrame({"GMT MKT Interval": generation["Interval Start"], "Wind Market": 40.,
                             "Wind Self": 20., "Solar Market": 1., "Solar Self": 1.})
    historic["Wind Market"] = historic["Wind Market"].astype(object)
    historic.loc[0, "Wind Market"] = bad
    with pytest.raises(ValueError):
        prepare(historic, load, prices, generation_format="historical")


@pytest.mark.parametrize("bad", [60 + 5j, pd.Timestamp(60, unit="ns"), pd.Timedelta(60, unit="ns")])
@pytest.mark.parametrize("mixed", [False, True])
def test_adapters_reject_lossy_quantities_before_hourly_averaging(bad, mixed):
    def invalid(length):
        return (pd.Series([bad] * length) if not mixed else
                pd.Series([bad, *(["60"] * (length - 1))], dtype=object))

    generation, load, prices = cached_inputs()
    generation["Wind"] = invalid(len(generation))
    with pytest.raises(ValueError, match="complex, datetime or timedelta"):
        prepare(generation, load, prices)
    generation, load, prices = cached_inputs()
    prices["LMP"] = invalid(len(prices))
    with pytest.raises(ValueError, match="complex, datetime or timedelta"):
        prepare(generation, load, prices)
    generation, load, prices = cached_inputs()
    historic = pd.DataFrame({"GMT MKT Interval": generation["Interval Start"], "Wind Market": invalid(len(generation)),
                             "Wind Self": 20., "Solar Market": 1., "Solar Self": 1.})
    with pytest.raises(ValueError, match="complex, datetime or timedelta"):
        prepare(historic, load, prices, generation_format="historical")


def test_adapter_keeps_numeric_strings_and_incomplete_nullable_power_unknown():
    generation, load, prices = cached_inputs()
    generation["Wind"] = pd.Series(["60"] * len(generation), dtype=object)
    generation.loc[0, "Wind"] = pd.NA
    prices["LMP"] = pd.Series(["-1", "10"], dtype=object)
    result = prepare(generation, load, prices)
    assert pd.isna(result.system_wind_mw.iloc[0])
    assert result.system_wind_mw.iloc[1] == 60.
    assert result.lmp_usd_mwh.tolist() == [-1., 10.]


def test_cache_adapter_overlapping_identical_chunks_are_deduplicated():
    generation, load, prices = cached_inputs()
    assert prepare(pd.concat([generation, generation]), load, prices).equals(prepare(generation, load, prices))


def test_cache_adapter_conflicting_revisions_are_rejected():
    generation, load, prices = cached_inputs()
    conflict = generation.iloc[[0]].assign(Wind=80.)
    with pytest.raises(ValueError, match="Conflicting"):
        prepare(pd.concat([generation, conflict]), load, prices)


@pytest.mark.parametrize("changes", [{"market": "OTHER"}, {"price_locations": {"CSWS": "UNMAPPED"}}])
def test_cache_adapter_does_not_guess_market_or_node(changes):
    with pytest.raises(ValueError, match="cached"):
        prepare(*cached_inputs(), **changes)


def test_cache_adapter_does_not_average_other_nodes_or_markets():
    generation, load, prices = cached_inputs()
    other = prices.assign(Location="UNRELATED", LMP=-1000.)
    other_market = prices.assign(Market="RT", LMP=-999.)
    result = prepare(generation, load, pd.concat([prices, other, other_market]))
    assert result.lmp_usd_mwh.tolist() == [-1., 10.]


def test_cache_adapter_requires_hourly_system_load_not_zone_pooling():
    generation, load, prices = cached_inputs()
    with pytest.raises(ValueError, match="one system"):
        prepare(generation, pd.concat([load, load.assign(location_id="CSWS")]), prices)


def test_cache_adapter_reuses_historical_generation_normalizer():
    generation, load, prices = cached_inputs()
    historic = pd.DataFrame({"GMT MKT Interval": generation["Interval Start"], "Wind Market": 40.,
                             "Wind Self": 20., "Solar Market": 1., "Solar Self": 1.})
    result = prepare(historic, load, prices, generation_format="historical")
    assert result.system_wind_mw.tolist() == [60., 60.]


def historical_annual_boundary_inputs():
    generation, load, prices = cached_inputs()
    hours = pd.date_range("2025-01-01T05:00:00Z", periods=2, freq="h")
    times = pd.date_range(hours[0], periods=len(generation), freq="5min")
    historic = pd.DataFrame({"GMT MKT Interval": times, "Wind Market": 40., "Wind Self": 20.,
                             "Solar Market": 1., "Solar Self": 1.})
    # The boundary hour has one 120-MW sample followed by eleven 12-MW samples.
    historic.loc[12:, ["Wind Market", "Wind Self"]] = [10., 2.]
    historic.loc[12, "Wind Market"] = 118.
    load["timestamp_utc"] = hours
    prices["Interval Start"] = hours
    prices["Interval End"] = hours + pd.Timedelta(1, unit="h")
    return historic.iloc[:13].copy(), historic.iloc[13:].copy(), load, prices


def test_raw_annual_boundary_recovery_requires_all_twelve_samples_and_preserves_prefix():
    previous_year, next_year, load, prices = historical_annual_boundary_inputs()
    boundary = pd.Timestamp("2025-01-01T06:00:00Z")
    first_partition = previous_year.loc[previous_year["GMT MKT Interval"].dt.floor("h") == boundary]
    assert first_partition["GMT MKT Interval"].tolist() == [boundary]
    assert next_year["GMT MKT Interval"].tolist() == list(pd.date_range(boundary + pd.Timedelta(5, unit="min"), periods=11, freq="5min"))
    previous = prepare(previous_year, load, prices, generation_format="historical")
    following = prepare(next_year, load, prices, generation_format="historical")
    assert pd.isna(previous.system_wind_mw.iloc[1])
    assert pd.isna(following.system_wind_mw.iloc[1])
    joined = prepare(pd.concat([previous_year, next_year], ignore_index=True), load, prices,
                     generation_format="historical")
    assert joined.system_wind_mw.iloc[1] == 21.
    # Averaging the unequal partitions' means would wrongly produce 66 MW.
    partition_means = [part[["Wind Market", "Wind Self"]].sum(axis=1).mean()
                       for part in (first_partition, next_year)]
    assert sum(partition_means) / 2 == 66.
    assert joined.system_wind_mw.iloc[1] != sum(partition_means) / 2
    pd.testing.assert_frame_equal(previous.iloc[:1], joined.iloc[:1], check_exact=True)
    assert joined.system_wind_mw.iloc[0] == 60.


@pytest.mark.parametrize("case", ["missing_market", "missing_self", "conflicting_revision",
                                  "missing_interval", "duplicate_for_missing_interval"])
def test_joined_annual_boundary_with_incomplete_or_conflicting_evidence_stays_unknown(case):
    previous_year, next_year, load, prices = historical_annual_boundary_inputs()
    prefix = prepare(previous_year, load, prices, generation_format="historical").iloc[:1]
    if case == "missing_market":
        previous_year.loc[previous_year.index[-1], "Wind Market"] = np.nan
    elif case == "missing_self":
        next_year.loc[next_year.index[0], "Wind Self"] = np.nan
    elif case == "conflicting_revision":
        changed = previous_year.iloc[[-1]].assign(**{"Wind Market": 119.})
        next_year = pd.concat([changed, next_year], ignore_index=True)
    else:
        next_year = next_year.iloc[1:].copy()
        if case == "duplicate_for_missing_interval":
            next_year = pd.concat([next_year, next_year.iloc[[0]]], ignore_index=True)
    joined = prepare(pd.concat([previous_year, next_year], ignore_index=True), load, prices,
                     generation_format="historical")
    assert pd.isna(joined.system_wind_mw.iloc[1])
    assert pd.isna(wind_oversupply_hours(joined, **ARGS).wind_oversupply_proxy.iloc[1])
    pd.testing.assert_frame_equal(prefix, joined.iloc[:1], check_exact=True)


def test_read_only_cache_adapter_reuses_existing_reader_without_fetch(monkeypatch, tmp_path):
    generation, load, prices = cached_inputs()
    path = tmp_path / "system.parquet"
    load.to_parquet(path, index=False)
    seen = []

    def reader(dataset):
        seen.append(dataset)
        return {"fuel_mix": generation, "lmp": prices}[dataset]

    def unexpected(*args, **kwargs):
        pytest.fail("Read-only wind adapter must not fetch")

    monkeypatch.setattr("pipeline.wind_signal.ingest.load_dataset", reader)
    monkeypatch.setattr("pipeline.wind_signal.ingest.fetch_chunk", unexpected)
    result = read_cached_wind_inputs(path, price_locations={"CSWS": "EXPLICIT_NODE"}, market="DA")
    assert seen == ["fuel_mix", "lmp"]
    assert len(result) == 2
    assert result.attrs["price_locations"] == {"CSWS": "EXPLICIT_NODE"}


def archive_fixture(tmp_path, monkeypatch):
    # Synthetic cache bytes; the declared assumption survives the reader.
    content = b'GMT MKT Interval,Coal Market,Wind Market\n2024-01-01T00:00:00Z,10,20\n'
    url = "https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/GenMix_2024.csv"
    manifest = {"source_type": "assumption", "ref": url, "requested_url": url, "sha256": hashlib.sha256(content).hexdigest()}
    path = tmp_path / "data/raw/spp/evidence/genmix_2024.parquet"
    path.parent.mkdir(parents=True)
    row = {"request_url": url, "content": content, "source_json": json.dumps(manifest)}
    pd.DataFrame([row]).to_parquet(path, index=False)
    monkeypatch.setattr("pipeline.wind_signal.ROOT", tmp_path)
    return path, row


def test_historical_cached_document_keeps_all_fuels_and_provenance(tmp_path, monkeypatch):
    archive_fixture(tmp_path, monkeypatch)
    frame, origin = read_cached_generation_archive(2024)
    assert "Coal Market" in frame.columns
    assert origin["source_type"] == "assumption"
    assert "cached_sha256=" in origin["ref"]


def test_historical_cached_document_rejects_changed_bytes(tmp_path, monkeypatch):
    path, row = archive_fixture(tmp_path, monkeypatch)
    row["content"] += b"tampered"
    pd.DataFrame([row]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="fingerprint"):
        read_cached_generation_archive(2024)


def test_historical_cached_document_rejects_wrong_vintage(tmp_path, monkeypatch):
    path, row = archive_fixture(tmp_path, monkeypatch)
    row["request_url"] = row["request_url"].replace("2024", "2023")
    pd.DataFrame([row]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="archive URL"):
        read_cached_generation_archive(2024)


def test_historical_missing_cache_is_not_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.wind_signal.ROOT", tmp_path)
    monkeypatch.setattr("pipeline.wind_signal.ingest.fetch_public_evidence", lambda *args, **kwargs: pytest.fail("Must not fetch"))
    with pytest.raises(FileNotFoundError):
        read_cached_generation_archive(2024)


def test_historical_adapter_does_not_inherit_raw_data_tag_for_assumed_hourly_binning(tmp_path, monkeypatch):
    from pathlib import Path
    from pipeline import wind_signal as module
    generation, load, prices = cached_inputs()
    raw = pd.DataFrame({"GMT MKT Interval": generation["Interval Start"],
                        "Wind Market": generation.Wind, "Wind Self": 0.,
                        "Solar Market": 0., "Solar Self": 0.})
    origin = {"source_type": "data", "ref": "authored archive-source fixture for provenance-boundary testing"}
    path = tmp_path / "load.parquet"
    load.to_parquet(path, index=False)
    monkeypatch.setattr(module, "ROOT", Path.cwd())
    monkeypatch.setattr(module, "read_cached_generation_archive", lambda year: (raw, origin))
    monkeypatch.setattr(module.ingest, "RAW_DIR", tmp_path)
    monkeypatch.setattr(module.ingest, "load_dataset", lambda dataset: prices)
    monkeypatch.setattr(module.ingest, "fetch_public_evidence", lambda *args, **kwargs: pytest.fail("Must not fetch"))
    result = module.read_cached_historical_wind_inputs(path, year=2024,
        price_locations={"CSWS": "EXPLICIT_NODE"}, market="DA")
    assert result.attrs["raw_generation_source"] == origin
    assert result.attrs["generation_source"]["source_type"] == "assumption"
    assert origin["ref"] in result.attrs["generation_source"]["ref"]
    assert "not verified metered" in result.attrs["generation_source"]["ref"]
    assert result.attrs["source_qualification"] == module.GENMIX_SOURCE_QUALIFICATION
    assert result.system_wind_mw.tolist() == [60., 60.]
