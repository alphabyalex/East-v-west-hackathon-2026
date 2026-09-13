"""Shared citations retain operand identity; authored software fixtures only."""
from copy import deepcopy
import json
import socket

import pandas as pd
import pytest

import pipeline.carbon as carbon

HOURS = [f"2025-01-01T0{hour}:00:00+00:00" for hour in range(4)]
SHARED = ' {"dataset": "test-only shared table", "scope": "authored software fixture"} '
POLICY = {"source_type": "assumption", "ref": "test-only scenario selection; no physical claim"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("No network in provenance regressions")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def datum(value, kind="assumption", ref=SHARED):
    return {"value": value, "source_type": kind, "ref": ref}


def frame(values=(30.0, 70.0), *, hour=HOURS[0], kind="assumption"):
    return pd.DataFrame([
        {"timestamp_utc": hour, "fuel": fuel, "generation_mwh": value,
         "source_type": kind, "ref": SHARED,
         "generation_status": "complete" if value is not None else "incomplete_observations"}
        for fuel, value in zip(("Coal", "Wind"), values)
    ], dtype=object)


def factors(values=(1000.0, 0.0), *, kind="assumption"):
    return {fuel: {**datum(value, kind), "unit": carbon.FACTOR_UNIT, "boundary": carbon.BOUNDARY}
            for fuel, value in zip(("Coal", "Wind"), values)}


def mix(values=(30.0, 70.0), rates=(1000.0, 0.0), *, hour=HOURS[0]):
    return carbon.fuel_mix_intensity(frame(values, hour=hour), factors(rates),
        expected_fuels=["Coal", "Wind"], application_source=POLICY)[0]


def inputs(output):
    assert set(output) == {"value", "source_type", "ref"}
    return json.loads(output["ref"])["inputs"]


def identity_fields(item):
    return {key: item[key] for key in ("value", "source_type", "ref")}


def shift_fixture(values=(800.0, 200.0), amount=10.0, limit_values=(10.0, 10.0)):
    records = [{"timestamp_utc": hour, "boundary": carbon.BOUNDARY,
                "intensity_kg_co2_per_mwh": datum(value)}
               for hour, value in zip(HOURS, values)]
    moves = [{"risk_hour": HOURS[0], "makeup_hour": HOURS[1], "mwh": datum(amount)}]
    limits = {HOURS[0]: {"removable_mwh": datum(limit_values[0])},
              HOURS[1]: {"makeup_capacity_mwh": datum(limit_values[1])}}
    return records, moves, limits


def shifted(values=(800.0, 200.0), amount=10.0, limit_values=(10.0, 10.0)):
    records, moves, limits = shift_fixture(values, amount, limit_values)
    return carbon.shift_carbon(records, moves, hourly_limits=limits, selection_source=POLICY)


def test_generation_swap_with_shared_citations_cannot_have_identical_reference():
    first = mix()["intensity_kg_co2_per_mwh"]
    second = mix((70.0, 30.0))["intensity_kg_co2_per_mwh"]
    assert (first["value"], second["value"]) == (300.0, 700.0)
    assert first["ref"] != second["ref"]


def test_factor_swap_retains_exact_fuel_to_generation_mapping_and_original_datums():
    first = mix()["intensity_kg_co2_per_mwh"]
    second = mix(rates=(0.0, 1000.0))["intensity_kg_co2_per_mwh"]
    assert (first["value"], second["value"]) == (300.0, 700.0)
    assert first["ref"] != second["ref"]
    evidence = inputs(first)
    generation = {item["fuel"]: item for item in evidence if item.get("quantity") == "generation_mwh"}
    rates = {item["fuel"]: item for item in evidence if item.get("quantity") == "factor_kg_co2_per_mwh"}
    assert set(generation) == set(rates) == {"Coal", "Wind"}
    for fuel, energy in (("Coal", 30.0), ("Wind", 70.0)):
        assert identity_fields(generation[fuel]) == datum(energy)
        assert generation[fuel]["timestamp_utc"] == HOURS[0]
        assert "timestamp_utc" not in rates[fuel], "An annual factor is not an hourly observation"
        assert generation[fuel]["unit"] == "MWh"
        assert {key: rates[fuel][key] for key in factors()[fuel]} == factors()[fuel]
    assert sum(generation[fuel]["value"] * rates[fuel]["value"] for fuel in generation) / sum(
        item["value"] for item in generation.values()) == first["value"]


def test_equal_fuel_contributions_do_not_collapse_in_reported_generation_sum():
    result = mix((20.0, 20.0))["reported_generation_mwh"]
    assert result["value"] == 40.0
    contributions = [item for item in inputs(result) if item.get("value") == 20.0]
    assert len(contributions) == 2, "Two different fuel rows cannot be deduplicated into one contribution"
    assert {item["fuel"] for item in contributions} == {"Coal", "Wind"}
    assert sum(item["value"] for item in contributions) == result["value"]
    assert all(identity_fields(item) == datum(20.0) for item in contributions)


def test_equal_hourly_fuel_values_retain_the_observation_hour():
    first = mix()["intensity_kg_co2_per_mwh"]
    later = mix(hour=HOURS[1])["intensity_kg_co2_per_mwh"]
    assert first["value"] == later["value"] == 300.0
    assert first["ref"] != later["ref"]
    assert {item["timestamp_utc"] for item in inputs(later) if item.get("quantity") == "generation_mwh"} == {HOURS[1]}
    annual_factors = lambda result: [item for item in inputs(result) if item.get("quantity") == "factor_kg_co2_per_mwh"]
    assert annual_factors(first) == annual_factors(later), "Annual factor evidence remains shared across hours"
    assert all("timestamp_utc" not in item for item in annual_factors(later))


def test_opposite_shift_signs_from_shared_citations_keep_distinct_hour_roles():
    positive = shifted()["pairs"][0]["carbon_shifted_kg_co2"]
    negative = shifted((200.0, 800.0))["pairs"][0]["carbon_shifted_kg_co2"]
    assert (positive["value"], negative["value"]) == (6000.0, -6000.0)
    assert positive["ref"] != negative["ref"]
    by_role = {item["role"]: item for item in inputs(positive)
               if item.get("quantity") == "intensity_kg_co2_per_mwh"}
    assert set(by_role) == {"risk", "makeup"}
    assert by_role["risk"]["timestamp_utc"] == HOURS[0]
    assert by_role["makeup"]["timestamp_utc"] == HOURS[1]
    for role, value in (("risk", 800.0), ("makeup", 200.0)):
        assert identity_fields(by_role[role]) == datum(value)
        assert by_role[role]["unit"] == carbon.FACTOR_UNIT
        assert by_role[role]["boundary"] == carbon.BOUNDARY


def test_equal_shift_operands_remain_five_distinct_quantities_and_roles():
    result = shifted((10.0, 10.0))
    assert result["mwh_removed"]["value"] == result["mwh_made_up"]["value"] == 10.0
    assert result["carbon_shifted_kg_co2"]["value"] == 0.0
    evidence = inputs(result["pairs"][0]["carbon_shifted_kg_co2"])
    operands = [item for item in evidence if "value" in item]
    assert len(operands) == 5, "Energy, two intensities and two limits are distinct despite equal values/refs"
    assert all(identity_fields(item) == datum(10.0) for item in operands)
    by_quantity = {item["quantity"]: item for item in operands if item["quantity"] != "intensity_kg_co2_per_mwh"}
    assert set(by_quantity) == {"moved_mwh", "removable_mwh", "makeup_capacity_mwh"}
    assert by_quantity["moved_mwh"]["risk_hour"] == HOURS[0]
    assert by_quantity["moved_mwh"]["makeup_hour"] == HOURS[1]
    assert by_quantity["removable_mwh"]["timestamp_utc"] == HOURS[0]
    assert by_quantity["makeup_capacity_mwh"]["timestamp_utc"] == HOURS[1]


def test_swapping_valid_capacity_limits_changes_evidence_without_changing_arithmetic():
    first = shifted(limit_values=(10.0, 20.0))["pairs"][0]["carbon_shifted_kg_co2"]
    swapped = shifted(limit_values=(20.0, 10.0))["pairs"][0]["carbon_shifted_kg_co2"]
    assert first["value"] == swapped["value"] == 6000.0
    assert first["ref"] != swapped["ref"]


def test_unknown_generation_and_shift_intensity_preserve_identity_and_null():
    output = mix((None, 70.0))
    assert output["intensity_kg_co2_per_mwh"]["value"] is None
    assert output["generation_mwh"]["value"] is None
    assert output["reported_generation_mwh"]["value"] == 70.0
    unknown = [item for item in inputs(output["intensity_kg_co2_per_mwh"])
               if item.get("quantity") == "generation_mwh" and item.get("fuel") == "Coal"]
    assert len(unknown) == 1
    assert identity_fields(unknown[0]) == datum(None)
    result = shifted((None, 200.0))
    assert result["carbon_shifted_kg_co2"]["value"] is None
    unknown = [item for item in inputs(result["pairs"][0]["carbon_shifted_kg_co2"])
               if item.get("role") == "risk"]
    assert len(unknown) == 1
    assert identity_fields(unknown[0]) == datum(None)
    assert unknown[0]["timestamp_utc"] == HOURS[0]


@pytest.mark.parametrize("kind,expected_intensity", [("data", "model"), ("model", "model"), ("assumption", "assumption")])
def test_context_addition_preserves_exact_leaf_and_output_source_ranks(kind, expected_intensity):
    original = frame(kind=kind)
    selected_factors = factors(kind=kind)
    policy = {"source_type": "data", "ref": "test-only documented factor applicability"}
    output = carbon.fuel_mix_intensity(original, selected_factors,
        expected_fuels=["Coal", "Wind"], application_source=policy)[0]
    assert output["reported_generation_mwh"]["source_type"] == kind
    assert output["generation_mwh"]["source_type"] == kind
    assert output["intensity_kg_co2_per_mwh"]["source_type"] == expected_intensity
    leaves = [item for item in inputs(output["intensity_kg_co2_per_mwh"]) if "value" in item]
    assert len(leaves) == 4
    assert all(item["source_type"] == kind and item["ref"] == SHARED for item in leaves)


def test_determinism_no_input_mutation_and_existing_pair_multiplicity():
    original = frame((20.0, 20.0))
    saved = original.copy(deep=True)
    selected_factors = factors((0.0, 0.0))
    saved_factors = deepcopy(selected_factors)
    first = carbon.fuel_mix_intensity(original, selected_factors, expected_fuels=["Coal", "Wind"], application_source=POLICY)
    permuted = carbon.fuel_mix_intensity(original.iloc[::-1], dict(reversed(list(selected_factors.items()))),
        expected_fuels=["Wind", "Coal"], application_source=POLICY)
    assert json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(permuted, sort_keys=True, allow_nan=False)
    pd.testing.assert_frame_equal(original, saved)
    assert selected_factors == saved_factors

    records, moves, limits = shift_fixture((800.0, 200.0, 800.0, 200.0))
    moves.append({"risk_hour": HOURS[2], "makeup_hour": HOURS[3], "mwh": datum(10.0)})
    limits.update({HOURS[2]: {"removable_mwh": datum(10.0)}, HOURS[3]: {"makeup_capacity_mwh": datum(10.0)}})
    saved_records, saved_moves, saved_limits = deepcopy((records, moves, limits))
    first = carbon.shift_carbon(records, moves, hourly_limits=limits, selection_source=POLICY)
    permuted = carbon.shift_carbon(records[::-1], moves[::-1],
        hourly_limits=dict(reversed(list(limits.items()))), selection_source=POLICY)
    assert json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(permuted, sort_keys=True, allow_nan=False)
    assert (records, moves, limits) == (saved_records, saved_moves, saved_limits)
    assert first["carbon_shifted_kg_co2"]["value"] == 12000.0
    contributions = [item for item in inputs(first["carbon_shifted_kg_co2"]) if "value" in item]
    assert len(contributions) == 2
    assert all(item["value"] == 6000.0 for item in contributions)
    assert contributions[0]["ref"] != contributions[1]["ref"]
    assert first["mwh_removed"]["value"] == first["mwh_made_up"]["value"] == 20.0
