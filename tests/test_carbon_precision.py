"""Synthetic binary64 accounting boundaries, not physical grid observations."""
from copy import deepcopy
from fractions import Fraction
from itertools import permutations
import json
import math

import pandas as pd
import pytest

from pipeline.carbon import BOUNDARY, FACTOR_UNIT, fuel_mix_intensity, shift_carbon


MIN_SUBNORMAL = math.ulp(0.)
HOURS = [f"2024-01-01T0{hour}:00:00+00:00" for hour in range(4)]
POLICY = {"source_type": "assumption", "ref": "Synthetic precision-test policy; no physical data or dispatch claim"}


def datum(value, label):
    return {"value": value, "source_type": "assumption", "ref": f"Synthetic precision-test {label}"}


def fuel_inputs(generation, rates):
    names = [f"synthetic_fuel_{index}" for index in range(len(generation))]
    frame = pd.DataFrame([
        {"timestamp_utc": HOURS[0], "fuel": name, "generation_mwh": amount,
         **{key: value for key, value in datum(amount, f"generation {name}").items() if key != "value"}}
        for name, amount in zip(names, generation)
    ])
    factors = {name: {**datum(rate, f"factor {name}"), "unit": FACTOR_UNIT, "boundary": BOUNDARY}
               for name, rate in zip(names, rates)}
    return frame, factors, names


def calculate_mix(generation, rates):
    frame, factors, names = fuel_inputs(generation, rates)
    return fuel_mix_intensity(frame, factors, expected_fuels=names, application_source=POLICY)[0]


def exact_mix(generation, rates):
    amounts = [Fraction.from_float(value) for value in generation]
    factors = [Fraction.from_float(value) for value in rates]
    return float(sum((amount * factor for amount, factor in zip(amounts, factors)), Fraction()) / sum(amounts))


def shift_inputs(energy, before, after):
    records = [{"timestamp_utc": hour, "boundary": BOUNDARY,
                "intensity_kg_co2_per_mwh": datum(value, f"intensity {hour}")}
               for hour, value in zip(HOURS, (before, after))]
    moves = [{"risk_hour": HOURS[0], "makeup_hour": HOURS[1], "mwh": datum(energy, "moved energy")}]
    limits = {HOURS[0]: {"removable_mwh": datum(energy, "removable limit")},
              HOURS[1]: {"makeup_capacity_mwh": datum(energy, "makeup limit")}}
    return records, moves, limits


def calculate_shift(energy, before, after):
    records, moves, limits = shift_inputs(energy, before, after)
    return shift_carbon(records, moves, hourly_limits=limits, selection_source=POLICY)


def exact_shift(energy, before, after):
    return float(Fraction.from_float(energy) * (Fraction.from_float(before) - Fraction.from_float(after)))


def assert_assumed(value):
    if isinstance(value, dict):
        if "value" in value:
            assert value["source_type"] == "assumption" and value["ref"]
        else:
            for child in value.values():
                assert_assumed(child)
    elif isinstance(value, list):
        for child in value:
            assert_assumed(child)


def test_nonzero_weighted_intensity_survives_underflow_of_intermediate_share():
    generation = [2. ** -700, 2. ** 700]
    rates = [2. ** 700, 0.]
    expected = exact_mix(generation, rates)
    assert expected == 2. ** -700
    result = calculate_mix(generation, rates)
    assert result["status"] == "available"
    assert result["intensity_kg_co2_per_mwh"]["value"] == expected
    assert result["generation_mwh"]["value"] == math.fsum(generation)
    assert result["factor_coverage_fraction"]["value"] == 1.
    assert_assumed(result)
    json.dumps(result, allow_nan=False)


def test_tiny_weighted_terms_are_summed_before_the_single_final_rounding():
    generation, rates = [3., 3., 2.], [MIN_SUBNORMAL, MIN_SUBNORMAL, 0.]
    # Each nonzero term is only 3/8 of the least subnormal; together they round up.
    expected = exact_mix(generation, rates)
    assert expected == MIN_SUBNORMAL
    frame, factors, names = fuel_inputs(generation, rates)
    saved_frame, saved_factors = frame.copy(deep=True), deepcopy(factors)
    result = fuel_mix_intensity(frame, factors, expected_fuels=names, application_source=POLICY)
    assert result[0]["intensity_kg_co2_per_mwh"]["value"] == expected
    encoded = json.dumps(result, allow_nan=False)
    for order in permutations(range(len(frame))):
        actual = fuel_mix_intensity(frame.iloc[list(order)], dict(reversed(list(factors.items()))),
                                    expected_fuels=list(reversed(names)), application_source=POLICY)
        assert json.dumps(actual, allow_nan=False) == encoded
    pd.testing.assert_frame_equal(frame, saved_frame, check_exact=True)
    assert factors == saved_factors
    assert_assumed(result)


@pytest.mark.parametrize("rates", [(MIN_SUBNORMAL, 0.), (0., 0.)], ids=["unrepresentable-final", "genuine-zero-factors"])
def test_weighted_underflow_guard_keeps_correctly_rounded_zero(rates):
    generation = [1., 3.]
    result = calculate_mix(generation, rates)
    assert result["status"] == "available"
    assert result["intensity_kg_co2_per_mwh"]["value"] == exact_mix(generation, rates) == 0.
    assert_assumed(result)


def test_tiny_positive_generation_with_unknown_factor_stays_unknown():
    frame, factors, names = fuel_inputs([2. ** -700, 2. ** 700], [2. ** 700, 0.])
    del factors[names[0]]
    result = fuel_mix_intensity(frame, factors, expected_fuels=names, application_source=POLICY)[0]
    assert result["status"] == "missing_factors"
    assert result["missing_fuels"] == [names[0]]
    assert result["intensity_kg_co2_per_mwh"]["value"] is None
    assert_assumed(result)


def test_unknown_generation_does_not_enter_the_exact_fallback_as_zero():
    frame, factors, names = fuel_inputs([2. ** -700, 2. ** 700], [2. ** 700, 0.])
    frame["generation_status"] = ["incomplete_observations", "complete"]
    frame["generation_mwh"] = frame["generation_mwh"].astype(object)
    frame.loc[0, "generation_mwh"] = None
    result = fuel_mix_intensity(frame, factors, expected_fuels=names, application_source=POLICY)[0]
    assert result["status"] == "missing_generation"
    assert result["generation_mwh"]["value"] is None
    assert result["intensity_kg_co2_per_mwh"]["value"] is None
    assert result["factor_coverage_fraction"]["value"] is None
    assert_assumed(result)


@pytest.mark.parametrize("reverse", [False, True], ids=["positive", "negative"])
def test_subnormal_energy_uses_once_rounded_exact_intensity_difference(reverse):
    before, after = 1., math.nextafter(.5, 0.)
    if reverse:
        before, after = after, before
    expected = exact_shift(MIN_SUBNORMAL, before, after)
    assert expected == (-MIN_SUBNORMAL if reverse else MIN_SUBNORMAL)
    result = calculate_shift(MIN_SUBNORMAL, before, after)
    assert result["carbon_shifted_kg_co2"]["value"] == expected
    assert result["pairs"][0]["carbon_shifted_kg_co2"]["value"] == expected
    assert result["mwh_removed"]["value"] == result["mwh_made_up"]["value"] == MIN_SUBNORMAL
    ref = json.loads(result["pairs"][0]["carbon_shifted_kg_co2"]["ref"])
    assert ref["method"] == "MWh * (risk-hour intensity - makeup-hour intensity); positive means lower makeup intensity"
    assert datum(before, f"intensity {HOURS[0]}") in ref["inputs"]
    assert datum(after, f"intensity {HOURS[1]}") in ref["inputs"]
    assert_assumed(result)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("energy,before,after", [
    (MIN_SUBNORMAL, .25, 0.),
    (MIN_SUBNORMAL, 0., .25),
    (MIN_SUBNORMAL, 1., .5),
    (MIN_SUBNORMAL, .5, 1.),
], ids=["positive-unrepresentable", "negative-unrepresentable", "positive-tie-to-even", "negative-tie-to-even"])
def test_unrepresentable_final_shift_and_half_subnormal_ties_still_round_to_zero(energy, before, after):
    expected = exact_shift(energy, before, after)
    assert expected == 0.
    result = calculate_shift(energy, before, after)
    assert result["pairs"][0]["carbon_shifted_kg_co2"]["value"] == expected
    assert result["carbon_shifted_kg_co2"]["value"] == expected
    assert_assumed(result)


@pytest.mark.parametrize("energy,before,after", [(0., 1., 0.), (MIN_SUBNORMAL, 1., 1.)], ids=["zero-energy", "equal-intensity"])
def test_true_zero_shift_remains_zero_near_underflow_boundary(energy, before, after):
    result = calculate_shift(energy, before, after)
    assert result["carbon_shifted_kg_co2"]["value"] == exact_shift(energy, before, after) == 0.
    assert result["mwh_removed"]["value"] == result["mwh_made_up"]["value"] == energy
    assert_assumed(result)


@pytest.mark.parametrize("energy", [0., MIN_SUBNORMAL], ids=["zero-energy", "subnormal-energy"])
def test_missing_intensity_remains_null_even_when_numeric_result_could_be_zero(energy):
    result = calculate_shift(energy, None, .5)
    assert result["carbon_shifted_kg_co2"]["value"] is None
    assert result["pairs"][0]["carbon_shifted_kg_co2"]["value"] is None
    assert result["mwh_removed"]["value"] == result["mwh_made_up"]["value"] == energy
    assert_assumed(result)


def test_total_sums_published_pair_scalars_without_recovering_hidden_residuals():
    records = [{"timestamp_utc": hour, "boundary": BOUNDARY,
                "intensity_kg_co2_per_mwh": datum(value, f"intensity {hour}")}
               for hour, value in zip(HOURS, (.5, 0., .5, 0.))]
    moves = [{"risk_hour": HOURS[index], "makeup_hour": HOURS[index + 1],
              "mwh": datum(MIN_SUBNORMAL, f"moved energy {index}")} for index in (0, 2)]
    limits = {hour: {"removable_mwh": datum(MIN_SUBNORMAL, "removable limit"),
                     "makeup_capacity_mwh": datum(MIN_SUBNORMAL, "makeup limit")} for hour in HOURS}
    result = shift_carbon(records, moves, hourly_limits=limits, selection_source=POLICY)
    # Each half-subnormal pair correctly rounds to zero; its residual is not public.
    pair_values = [pair["carbon_shifted_kg_co2"]["value"] for pair in result["pairs"]]
    assert pair_values == [0., 0.]
    assert result["carbon_shifted_kg_co2"]["value"] == math.fsum(pair_values) == 0.
    assert result["mwh_removed"]["value"] == result["mwh_made_up"]["value"] == 2 * MIN_SUBNORMAL
    reordered = shift_carbon(list(reversed(records)), list(reversed(moves)),
                             hourly_limits=dict(reversed(list(limits.items()))), selection_source=POLICY)
    assert json.dumps(reordered, allow_nan=False) == json.dumps(result, allow_nan=False)
    assert_assumed(result)


def test_precision_fallback_does_not_relax_zero_energy_capacity():
    records, moves, limits = shift_inputs(MIN_SUBNORMAL, 1., math.nextafter(.5, 0.))
    limits[HOURS[1]]["makeup_capacity_mwh"] = datum(0., "explicit zero makeup capacity")
    with pytest.raises(ValueError, match="energy limit"):
        shift_carbon(records, moves, hourly_limits=limits, selection_source=POLICY)


def test_ordinary_documented_accounting_and_provenance_remain_stable():
    result = calculate_mix([30., 70.], [1000., 0.])
    assert result["intensity_kg_co2_per_mwh"]["value"] == 300.
    assert result["generation_mwh"]["value"] == result["known_generation_mwh"]["value"] == 100.
    assert result["factor_coverage_fraction"]["value"] == 1.
    assert json.loads(result["intensity_kg_co2_per_mwh"]["ref"])["method"] == "generation-weighted average; not marginal dispatch intensity"
    shifted = calculate_shift(10., 800., 200.)
    assert shifted["carbon_shifted_kg_co2"]["value"] == 6000.
    assert shifted["mwh_removed"]["value"] == shifted["mwh_made_up"]["value"] == 10.
    assert json.loads(shifted["carbon_shifted_kg_co2"]["ref"])["method"] == "signed sum across all explicit shift pairs; not a causal emissions reduction estimate"
    assert_assumed([result, shifted])
