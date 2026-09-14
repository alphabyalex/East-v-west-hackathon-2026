"""Test-only observations exercise overlapping blockers, not SPP emissions."""

from copy import deepcopy
import json

import pandas as pd

from pipeline.carbon import (
    BOUNDARY, FACTOR_UNIT, fuel_mix_intensity, normalize_spp_generation_archive,
)


HOUR = "2024-01-01T00:00:00+00:00"
POLICY = {"source_type": "assumption", "ref": "test-only factor application, not a measured hourly rate"}
FUELS = ["Coal", "Diesel Fuel Oil", "Hydro", "Natural Gas", "Nuclear", "Other",
         "Solar", "Waste Disposal Services", "Waste Heat", "Wind"]


def factor(fuel):
    return {"value": 100., "source_type": "assumption", "ref": f"test-only authored {fuel} factor",
            "unit": FACTOR_UNIT, "boundary": BOUNDARY}


def row(fuel, generation, status="complete"):
    return {"timestamp_utc": HOUR, "fuel": fuel, "generation_mwh": generation,
            "generation_status": status, "unit": "MWh", "source_type": "assumption",
            "ref": f"test-only authored {fuel} observation: {generation!r}, {status}"}


def test_missing_generation_does_not_hide_a_separate_positive_fuel_without_a_factor():
    factors = {"Coal": factor("Coal")}
    original_factors, original_policy = deepcopy(factors), deepcopy(POLICY)
    # The absent-row and explicitly marked-unknown contracts must retain both causes.
    for unknown_rows in ([], [row("Wind", None, "incomplete_observations")]):
        frame = pd.DataFrame([row("Coal", 20.), row("Other", 10.), *unknown_rows], dtype=object)
        original = deepcopy(frame)
        result = fuel_mix_intensity(frame, factors, expected_fuels=["Coal", "Other", "Wind"],
                                    application_source=POLICY)[0]

        assert result["status"] == "missing_generation"
        assert result["missing_generation_fuels"] == ["Wind"]
        assert result["missing_fuels"] == ["Other"]
        for name in ("intensity_kg_co2_per_mwh", "generation_mwh", "factor_coverage_fraction"):
            assert result[name]["value"] is None
            assert result[name]["source_type"] == "assumption"
        assert result["reported_generation_mwh"]["value"] == 30.
        assert result["known_generation_mwh"]["value"] == 20.
        evidence = json.loads(result["intensity_kg_co2_per_mwh"]["ref"])["inputs"]
        assert all(any(item["ref"] == ref for item in evidence) for ref in frame.ref)
        assert any("missing_generation_fuels=['Wind']" in item["ref"]
                   and "missing_factor_fuels=['Other']" in item["ref"] for item in evidence)
        pd.testing.assert_frame_equal(frame, original, check_exact=True)
    assert factors == original_factors and POLICY == original_policy


def test_explicit_zero_removes_only_its_factor_blocker_without_erasing_unknown_generation():
    factors = {"Coal": factor("Coal")}
    before = pd.DataFrame([row("Coal", 20.), row("Other", 10.),
                           row("Wind", None, "incomplete_observations")], dtype=object)
    after = pd.DataFrame([row("Coal", 20.), row("Other", 0.),
                          row("Wind", None, "incomplete_observations")], dtype=object)
    originals = deepcopy((before, after, factors, POLICY))
    results = [fuel_mix_intensity(frame, factors, expected_fuels=["Coal", "Other", "Wind"],
                                 application_source=POLICY)[0] for frame in (before, after)]

    assert results[0]["missing_fuels"] == ["Other"]
    assert results[1]["missing_fuels"] == []
    for result in results:
        assert result["status"] == "missing_generation"
        assert result["missing_generation_fuels"] == ["Wind"]
        assert result["intensity_kg_co2_per_mwh"]["value"] is None
        assert result["generation_mwh"]["value"] is None
        assert result["factor_coverage_fraction"]["value"] is None
    assert results[1]["reported_generation_mwh"]["value"] == 20.
    assert results[1]["known_generation_mwh"]["value"] == 20.
    evidence = json.loads(results[1]["intensity_kg_co2_per_mwh"]["ref"])["inputs"]
    assert any(item["ref"] == after.loc[1, "ref"] and item["value"] == 0. for item in evidence)
    pd.testing.assert_frame_equal(before, originals[0], check_exact=True)
    pd.testing.assert_frame_equal(after, originals[1], check_exact=True)
    assert factors == originals[2] and POLICY == originals[3]


def test_negative_and_incomplete_samples_survive_normalization_and_intensity_provenance():
    raw = pd.DataFrame({"GMT MKT Interval": pd.date_range(HOUR, periods=12, freq="5min")})
    for fuel in FUELS:
        raw[fuel + " Market"], raw[fuel + " Self"] = 1., 0.
    raw.loc[0, "Solar Self"] = float("nan")
    raw.loc[1, "Solar Self"] = -2.
    origin = {"source_type": "assumption", "ref": "test-only authored full-fuel samples"}
    timing = {"source_type": "assumption", "ref": "test-only observation-time UTC bins, not metered energy"}
    factors = {fuel: factor(fuel) for fuel in FUELS if fuel != "Other"}
    originals = deepcopy((raw, origin, timing, factors, POLICY))

    normalized = normalize_spp_generation_archive(raw, generation_source=origin, timing_source=timing)
    original_normalized = deepcopy(normalized)
    solar = normalized.set_index("fuel").loc["Solar"]
    assert solar.generation_mwh is None
    assert solar.generation_status == "negative_net_generation"
    diagnostic = json.loads(solar.ref)
    assert diagnostic["complete_samples"] == 11
    assert diagnostic["component_samples"] == {"Solar Market": 12, "Solar Self": 11}
    assert diagnostic["negative_net_samples"] == 1
    assert diagnostic["negative_component_samples"] == {"Solar Market": 0, "Solar Self": 1}
    assert origin in diagnostic["inputs"] and timing in diagnostic["inputs"]

    result = fuel_mix_intensity(normalized, factors, expected_fuels=normalized.attrs["expected_fuels"],
                                application_source=POLICY)[0]
    assert result["status"] == "missing_generation"
    assert result["missing_generation_fuels"] == ["Solar"]
    assert result["missing_fuels"] == ["Other"]
    assert result["intensity_kg_co2_per_mwh"]["value"] is None
    assert result["generation_mwh"]["value"] is None
    assert result["factor_coverage_fraction"]["value"] is None
    evidence = json.loads(result["intensity_kg_co2_per_mwh"]["ref"])["inputs"]
    assert any(item["ref"] == solar.ref and item["value"] is None for item in evidence)
    assert result["intensity_kg_co2_per_mwh"]["source_type"] == "assumption"
    pd.testing.assert_frame_equal(raw, originals[0], check_exact=True)
    pd.testing.assert_frame_equal(normalized, original_normalized, check_exact=True)
    assert normalized.attrs == original_normalized.attrs
    assert (origin, timing, factors, POLICY) == originals[1:]
