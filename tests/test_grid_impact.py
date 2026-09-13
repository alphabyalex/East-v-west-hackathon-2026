"""Synthetic scenarios exercise the standalone boundary; no fixture is real SPP data."""
from copy import deepcopy
import hashlib
import json
import math
from unittest.mock import patch

import pandas as pd
import pytest

from api.grid_impact import EVIDENCE_PREFIX, UNITS, compose_grid_impact, get_location_grid_impact
from pipeline.carbon import BOUNDARY, FACTOR_UNIT, fuel_mix_intensity, shift_carbon
from pipeline.wind_signal import summarize_wind


def datum(value, ref="synthetic unit fixture", kind="assumption"):
    return {"value": value, "source_type": kind, "ref": ref}


def evidence_text(result):
    return json.dumps(result["evidence"], sort_keys=True)


def coverage(hours=4, start="2024-01-01T00:00:00Z", **changes):
    first = pd.Timestamp(start)
    return {"period_start_utc": first.isoformat(),
            "period_end_exclusive_utc": (first + pd.Timedelta(hours, unit="h")).isoformat(),
            "observed_hours": datum(hours), "evaluable_hours": datum(hours),
            "unknown_hours": datum(0), "missing_interval_hours": datum(0),
            "schedule_scope": "complete_period", **changes}


def wind(hours=4, start="2024-01-01T00:00:00Z", price=-1.):
    frame = pd.DataFrame({"timestamp_utc": pd.date_range(start, periods=hours, freq="h"),
                          "location_id": "NODE", "system_wind_mw": 60.,
                          "system_load_mw": 100., "lmp_usd_mwh": price})
    sources = {name: {"source_type": "assumption", "ref": "synthetic unit fixture"}
               for name in ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")}
    return summarize_wind(frame, sources=sources, wind_scope="SPP_SYSTEM", load_scope="SPP_SYSTEM",
                          flexible_load_mw=datum(100.), available_fraction=datum(.5))[0]


def shift(*, risk_intensity=100., makeup_intensity=300., energy=10., makeup="2024-01-01T01:00:00Z"):
    risk = "2024-01-01T00:00:00Z"
    intensities = [{"timestamp_utc": timestamp, "boundary": BOUNDARY,
                    "intensity_kg_co2_per_mwh": datum(value)}
                   for timestamp, value in ((risk, risk_intensity), (makeup, makeup_intensity))]
    return shift_carbon(intensities, [{"risk_hour": risk, "makeup_hour": makeup, "mwh": datum(energy)}],
                        selection_source={"source_type": "assumption", "ref": "synthetic selected schedule"},
                        hourly_limits={risk: {"removable_mwh": datum(energy)}, makeup: {"makeup_capacity_mwh": datum(energy)}})


def cache(path, *, wind_summary=None, carbon_shift=None, shift_coverage=None, location_id="NODE"):
    payload = {"schema_version": "grid-impact-inputs-v1", "locations": [
        {"location_id": location_id, "wind_summary": wind_summary,
         "carbon_shift": carbon_shift, "shift_coverage": shift_coverage}]}
    path.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    return path


def test_missing_file_and_no_inputs_are_unavailable_not_zero(tmp_path):
    for result in (compose_grid_impact("NODE"), get_location_grid_impact("NODE", tmp_path / "absent.json")):
        for field in UNITS:
            assert result[field]["value"] is None
            assert result[field]["source_type"] == "assumption"
            assert result[field]["ref"].startswith(EVIDENCE_PREFIX)
        assert "unavailable" in evidence_text(result)


def test_missing_location_is_unavailable(tmp_path):
    result = get_location_grid_impact("OTHER", cache(tmp_path / "cache.json", wind_summary=wind()))
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] is None
    assert "absent" in evidence_text(result)


def test_partial_wind_preserves_observed_energy_and_operational_zero():
    result = compose_grid_impact("NODE", wind_summary=wind())
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == 200.
    assert result["carbon_absorbed_tonnes_in_observed_hours"]["value"] == 0.
    assert "egrid2023_technical_guide" in evidence_text(result)
    assert result["wind_absorption_mwh_per_year"]["value"] is None
    assert result["carbon_absorbed_tonnes_per_year"]["value"] is None
    assert "associated wind direct operational CO2" in result["basis"]["carbon_absorbed"]


@pytest.mark.parametrize("year,hours", [(2023, 8760), (2024, 8784)])
def test_complete_calendar_year_wind_is_not_scaled(year, hours):
    result = compose_grid_impact("NODE", wind_summary=wind(hours, f"{year}-01-01T00:00:00Z"))
    assert result["wind_absorption_mwh_per_year"]["value"] == hours * 50.
    assert result["carbon_absorbed_tonnes_per_year"]["value"] == 0.
    assert result["coverage"]["wind"]["status"] == "complete_calendar_year"


def test_known_no_opportunity_is_zero_but_unknown_wind_remains_null():
    known = compose_grid_impact("NODE", wind_summary=wind(price=10.))
    assert known["wind_absorption_mwh_in_observed_hours"]["value"] == 0.
    unknown = compose_grid_impact("NODE", wind_summary=wind(price=float("nan")))
    assert unknown["wind_absorption_mwh_in_observed_hours"]["value"] is None
    assert unknown["carbon_absorbed_tonnes_in_observed_hours"]["value"] is None


def test_negative_shift_is_preserved_and_converted_from_kg_to_tonnes():
    result = compose_grid_impact("NODE", carbon_shift=shift(), shift_coverage=coverage())
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] == -2.
    assert result["carbon_shifted_tonnes_per_year"]["value"] is None
    assert "negative means higher" in result["basis"]["carbon_shifted"]


def test_complete_year_can_report_signed_annual_shift():
    result = compose_grid_impact("NODE", carbon_shift=shift(), shift_coverage=coverage(8784))
    assert result["carbon_shifted_tonnes_per_year"]["value"] == -2.
    assert result["carbon_shifted_tonnes_per_year"]["source_type"] == "assumption"


@pytest.mark.parametrize("changes", [
    {"schedule_scope": "selected_pairs_only"},
    {"observed_hours": datum(8783), "evaluable_hours": datum(8783), "missing_interval_hours": datum(1)},
    {"evaluable_hours": datum(8783), "unknown_hours": datum(1)},
])
def test_incomplete_selection_or_coverage_cannot_publish_annual_shift(changes):
    result = compose_grid_impact("NODE", carbon_shift=shift(), shift_coverage=coverage(8784, **changes))
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] == -2.
    assert result["carbon_shifted_tonnes_per_year"]["value"] is None


def test_unknown_intensity_nulls_entire_shift_even_for_complete_year():
    result = compose_grid_impact("NODE", carbon_shift=shift(makeup_intensity=None), shift_coverage=coverage(8784))
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] is None
    assert result["carbon_shifted_tonnes_per_year"]["value"] is None


def test_pair_outside_period_is_rejected():
    with pytest.raises(ValueError, match="within"):
        compose_grid_impact("NODE", carbon_shift=shift(makeup="2024-01-02T00:00:00Z"), shift_coverage=coverage())


@pytest.mark.parametrize("field,value", [("mwh_removed", 11.), ("mwh_made_up", 9.), ("carbon_shifted_kg_co2", 2000.)])
def test_forged_shift_totals_cannot_override_explicit_pairs(field, value):
    raw = shift()
    raw[field]["value"] = value
    with pytest.raises(ValueError, match="total"):
        compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage())


def test_duplicate_shift_pairs_are_rejected():
    raw = shift()
    raw["pairs"].append(deepcopy(raw["pairs"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage())


def test_wind_location_and_premature_annual_scalar_are_rejected():
    with pytest.raises(ValueError, match="location"):
        compose_grid_impact("OTHER", wind_summary=wind())
    raw = wind()
    raw["wind_absorption_mwh_per_year"]["value"] = 200.
    with pytest.raises(ValueError, match="Annual"):
        compose_grid_impact("NODE", wind_summary=raw)


@pytest.mark.parametrize("count,value", [("unknown_hours", 1), ("observed_hours", 5), ("observed_hours", 3.5), ("missing_interval_hours", -1)])
def test_inconsistent_period_counts_are_rejected(count, value):
    with pytest.raises(ValueError):
        compose_grid_impact("NODE", carbon_shift=shift(), shift_coverage=coverage(**{count: datum(value)}))


@pytest.mark.parametrize("bad", [None, "", " "])
def test_missing_or_blank_refs_are_rejected(bad):
    raw = wind()
    raw["observed_hours"]["ref"] = bad
    with pytest.raises(ValueError, match="reference"):
        compose_grid_impact("NODE", wind_summary=raw)


def test_mock_reference_cannot_arrive_as_data():
    raw = wind()
    raw["wind_absorption_mwh_in_observed_hours"] = datum(200., "mock://wind", "data")
    with pytest.raises(ValueError, match="Placeholder"):
        compose_grid_impact("NODE", wind_summary=raw)


def test_all_numbers_are_sourced_and_scenarios_never_upgrade_assumptions():
    result = compose_grid_impact("NODE", wind_summary=wind(), carbon_shift=shift(), shift_coverage=coverage())
    def walk(value):
        if isinstance(value, dict):
            if "value" in value:
                assert {"value", "source_type", "ref"}.issubset(value)
                assert value["source_type"] in {"data", "model", "assumption"}
                assert value["ref"].strip()
            else:
                for child in value.values():
                    walk(child)
        else:
            assert not isinstance(value, (int, float)) and value is not None
    walk(result)
    for field in UNITS:
        assert result[field]["source_type"] == "assumption"
    assert "avoided" not in json.dumps(result).lower()
    assert result["boundary"] == "direct_operational_co2"


def test_reproducible_roundtrip_does_not_mutate_inputs(tmp_path):
    raw_wind, raw_shift, raw_coverage = wind(), shift(), coverage()
    original = deepcopy((raw_wind, raw_shift, raw_coverage))
    expected = compose_grid_impact("NODE", wind_summary=raw_wind, carbon_shift=raw_shift, shift_coverage=raw_coverage)
    path = cache(tmp_path / "cache.json", wind_summary=raw_wind, carbon_shift=raw_shift, shift_coverage=raw_coverage)
    assert get_location_grid_impact("NODE", path) == expected
    assert get_location_grid_impact("NODE", path) == expected
    assert (raw_wind, raw_shift, raw_coverage) == original


@pytest.mark.parametrize("content", ["not json", '{"schema_version":"grid-impact-inputs-v1","locations":NaN}',
                                         '{"schema_version":"grid-impact-inputs-v1","locations":[],"locations":[]}',
                                         '{"schema_version":"old","locations":[]}'])
def test_malformed_existing_cache_is_never_hidden_by_fallback(tmp_path, content):
    path = tmp_path / "broken.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        get_location_grid_impact("NODE", path)


def test_payload_validation_is_limited_to_selected_location(tmp_path):
    raw = wind()
    raw["proxy_hours"]["ref"] = ""
    path = cache(tmp_path / "cache.json", wind_summary=raw)
    assert get_location_grid_impact("OTHER", path)["wind_absorption_mwh_per_year"]["value"] is None
    with pytest.raises(ValueError):
        get_location_grid_impact("NODE", path)


@pytest.mark.parametrize("missing", ["observed_hours", "period_start_utc"])
def test_incomplete_coverage_shape_has_a_clear_validation_error(missing):
    raw = coverage()
    del raw[missing]
    with pytest.raises(ValueError, match="coverage"):
        compose_grid_impact("NODE", carbon_shift=shift(), shift_coverage=raw)


def test_claimed_data_total_cannot_upgrade_assumed_shift_pairs():
    raw = shift()
    raw["carbon_shifted_kg_co2"] = datum(-2000., "reviewed aggregate record", "data")
    result = compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage(8784))
    assert result["carbon_shifted_tonnes_per_year"]["source_type"] == "assumption"
    assert "synthetic unit fixture" in evidence_text(result)


def test_empty_pair_list_cannot_claim_zero_even_with_complete_period_counts():
    raw = shift(energy=0.)
    raw["pairs"] = []
    with pytest.raises(ValueError, match="empty shift schedule"):
        compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage(8784))


def test_submitted_zero_energy_pair_is_preserved_only_with_evaluable_selection():
    raw = shift(energy=0.)
    unknown = compose_grid_impact("NODE", carbon_shift=raw,
                                  shift_coverage=coverage(evaluable_hours=datum(0), unknown_hours=datum(4)))
    assert unknown["carbon_shifted_tonnes_in_observed_hours"]["value"] is None
    known = compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage(8784))
    assert known["carbon_shifted_tonnes_per_year"]["value"] == 0.


def test_duplicate_cached_location_is_not_resolved_arbitrarily(tmp_path):
    path = cache(tmp_path / "cache.json", wind_summary=wind())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["locations"].append(deepcopy(payload["locations"][0]))
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        get_location_grid_impact("NODE", path)


def assert_complete_evidence_graph(result):
    evidence = result["evidence"]
    seen, active = set(), set()
    def walk(value):
        if isinstance(value, str) and value.startswith(EVIDENCE_PREFIX):
            key = value[len(EVIDENCE_PREFIX):]
            assert key in evidence
            assert key not in active, "Evidence must not contain a cycle"
            if key not in seen:
                active.add(key)
                encoded = json.dumps(evidence[key], sort_keys=True, separators=(",", ":"), allow_nan=False)
                assert hashlib.sha256(encoded.encode()).hexdigest() == key
                walk(evidence[key])
                active.remove(key)
                seen.add(key)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk({key: value for key, value in result.items() if key != "evidence"})
    assert seen == set(evidence), "No unreachable evidence nodes should leak into the result"


def scheduled_pairs(count):
    start = pd.Timestamp("2024-01-01T00:00:00Z")
    moves, observations, limits = [], [], {}
    for day in range(count):
        risk = (start + pd.Timedelta(day * 24, unit="h")).isoformat()
        makeup = (start + pd.Timedelta(day * 24 + 1, unit="h")).isoformat()
        observations.extend({"timestamp_utc": stamp, "boundary": BOUNDARY,
                             "intensity_kg_co2_per_mwh": datum(value, "synthetic hourly average intensity")}
                            for stamp, value in ((risk, 100.), (makeup, 300.)))
        moves.append({"risk_hour": risk, "makeup_hour": makeup, "mwh": datum(10., "synthetic scheduled MWh")})
        limits[risk] = {"removable_mwh": datum(10., "synthetic removable capacity")}
        limits[makeup] = {"makeup_capacity_mwh": datum(10., "synthetic makeup capacity")}
    return shift_carbon(observations, moves,
                        selection_source={"source_type": "assumption", "ref": "synthetic complete schedule"}, hourly_limits=limits)


def test_compact_provenance_is_complete_resolvable_and_keeps_wind_context():
    raw_wind = wind()
    result = compose_grid_impact("NODE", wind_summary=raw_wind, carbon_shift=shift(), shift_coverage=coverage())
    assert_complete_evidence_graph(result)
    assert all(len(result[field]["ref"]) == len(EVIDENCE_PREFIX) + 64 for field in UNITS)
    context = result["evidence"][result["evidence_context"]["wind"][len(EVIDENCE_PREFIX):]]
    assert context["screen_method"] == raw_wind["method"]
    assert context["system_scope"] == "SPP_SYSTEM"
    assert context["proxy_hours"]["value"] == 4
    assert context["coverage"]["evaluable_hours"]["value"] == 4


def test_provenance_retains_factor_value_unit_boundary_and_original_refs():
    risk, makeup = "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"
    raw_mix = pd.DataFrame([
        {"timestamp_utc": stamp, "fuel": fuel, "generation_mwh": energy,
         "source_type": "assumption", "ref": f"synthetic fuel observation {fuel} {stamp}"}
        for stamp, wind_mwh in ((risk, 20.), (makeup, 50.))
        for fuel, energy in (("Wind", wind_mwh), ("Gas", 100. - wind_mwh))
    ])
    factors = {fuel: {**datum(value, f"synthetic {fuel} factor"), "unit": FACTOR_UNIT, "boundary": BOUNDARY}
               for fuel, value in (("Wind", 0.), ("Gas", 400.))}
    intensity = fuel_mix_intensity(raw_mix, factors, expected_fuels=["Wind", "Gas"],
                                   application_source={"source_type": "assumption", "ref": "synthetic geography-year mapping"})
    shifted = shift_carbon(intensity, [{"risk_hour": risk, "makeup_hour": makeup, "mwh": datum(10.)}],
                            selection_source={"source_type": "assumption", "ref": "synthetic schedule"},
                            hourly_limits={risk: {"removable_mwh": datum(10.)}, makeup: {"makeup_capacity_mwh": datum(10.)}})
    result = compose_grid_impact("NODE", carbon_shift=shifted, shift_coverage=coverage())
    assert_complete_evidence_graph(result)
    inputs = [item for node in result["evidence"].values() for item in node.get("inputs", [])]
    assert factors["Gas"] in inputs
    assert any(item.get("ref") == "synthetic geography-year mapping; expected_fuels=['Gas', 'Wind']; missing_generation_fuels=[]; missing_factor_fuels=[]" for item in inputs)
    assert "generation-weighted average; not marginal dispatch intensity" in evidence_text(result)
    assert risk in evidence_text(result) and makeup in evidence_text(result)
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] == 1.2


def test_opaque_ref_is_preserved_exactly_instead_of_interpreted_or_truncated():
    raw = wind()
    opaque = '{"other_format":{"url":"https://example.test/data","year":2024}}'
    raw["wind_absorption_mwh_in_observed_hours"]["ref"] = opaque
    result = compose_grid_impact("NODE", wind_summary=raw)
    assert any(item.get("ref") == opaque for node in result["evidence"].values() for item in node.get("inputs", []))


def test_supplied_dangling_evidence_ref_is_rejected():
    raw = wind()
    raw["proxy_hours"]["ref"] = EVIDENCE_PREFIX + "0" * 64
    with pytest.raises(ValueError, match="Unresolved"):
        compose_grid_impact("NODE", wind_summary=raw)


def test_nested_assumption_cannot_be_upgraded_by_data_parent():
    raw = wind()
    raw["observed_hours"] = datum(4, json.dumps({"method": "submitted count", "inputs": [datum(4)]}), "data")
    with pytest.raises(ValueError, match="upgrade"):
        compose_grid_impact("NODE", wind_summary=raw)


def test_dag_json_is_identical_under_pair_permutation():
    raw = scheduled_pairs(3)
    reordered = deepcopy(raw)
    reordered["pairs"].reverse()
    first = compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage(8784))
    second = compose_grid_impact("NODE", carbon_shift=reordered, shift_coverage=coverage(8784))
    assert json.dumps(first, allow_nan=False) == json.dumps(second, allow_nan=False)


def test_daily_pair_payload_is_bounded_and_graph_has_no_orphans():
    result = compose_grid_impact("NODE", carbon_shift=scheduled_pairs(365), shift_coverage=coverage(8784))
    assert len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode()) < 2_000_000
    assert len(result["carbon_shifted_tonnes_per_year"]["ref"]) == len(EVIDENCE_PREFIX) + 64
    assert_complete_evidence_graph(result)


def test_reader_composes_only_requested_location_after_envelope_validation(tmp_path):
    path = cache(tmp_path / "cache.json", wind_summary=wind())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["locations"].append({"location_id": "OTHER", "wind_summary": {"malformed": True}, "carbon_shift": None, "shift_coverage": None})
    path.write_text(json.dumps(payload), encoding="utf-8")
    import api.grid_impact as module
    with patch.object(module, "compose_grid_impact", wraps=module.compose_grid_impact) as compose:
        result = get_location_grid_impact("NODE", path)
    assert result["location_id"] == "NODE"
    assert compose.call_count == 1
    with pytest.raises(ValueError):
        get_location_grid_impact("OTHER", path)


def test_zero_removed_energy_cannot_be_hidden_by_a_tiny_positive_makeup():
    raw = shift(energy=0.)
    raw["pairs"][0]["mwh_made_up"]["value"] = 1e-10
    raw["mwh_made_up"]["value"] = 1e-10
    with pytest.raises(ValueError, match="conserve"):
        compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage())


@pytest.mark.parametrize("field", ["mwh_removed", "mwh_made_up", "carbon_shifted_kg_co2"])
def test_zero_aggregate_cannot_hide_a_tiny_nonzero_pair(field):
    raw = shift(energy=1e-12)
    raw[field]["value"] = 0.
    with pytest.raises(ValueError, match="total"):
        compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage())


def test_zero_energy_pair_cannot_carry_nonzero_carbon_even_when_total_matches():
    raw = shift(energy=0.)
    raw["pairs"][0]["carbon_shifted_kg_co2"]["value"] = 1e-10
    raw["carbon_shifted_kg_co2"]["value"] = 1e-10
    with pytest.raises(ValueError, match="zero-MWh"):
        compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage())


def test_at_most_one_float_step_is_allowed_for_nonzero_aggregate_roundoff():
    raw = shift(energy=.1)
    raw["mwh_removed"]["value"] = math.nextafter(.1, math.inf)
    compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage())
    raw["mwh_removed"]["value"] = math.nextafter(raw["mwh_removed"]["value"], math.inf)
    with pytest.raises(ValueError, match="total"):
        compose_grid_impact("NODE", carbon_shift=raw, shift_coverage=coverage())


@pytest.mark.parametrize("bad", [None, 1, [], {}, "unrecognized"])
def test_bad_source_types_have_explicit_validation_errors(bad):
    raw = wind()
    raw["observed_hours"]["source_type"] = bad
    with pytest.raises(ValueError):
        compose_grid_impact("NODE", wind_summary=raw)


def test_path_like_refs_are_preserved_as_text_without_resolution():
    raw = wind()
    original = "../../outside/secret.txt#rows=1-5"
    raw["wind_absorption_mwh_in_observed_hours"]["ref"] = original
    from pathlib import Path
    with patch.object(Path, "read_text", side_effect=AssertionError("provenance must not read files")):
        result = compose_grid_impact("NODE", wind_summary=raw)
    assert any(item.get("ref") == original for node in result["evidence"].values() for item in node.get("inputs", []))
    assert_complete_evidence_graph(result)
