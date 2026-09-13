"""Synthetic scenarios exercise the standalone boundary; no fixture is real SPP data."""
from copy import deepcopy
import hashlib
import json
import math
import os
from unittest.mock import patch

import pandas as pd
import pytest

from api.grid_impact import (
    EVIDENCE_PREFIX, UNITS, WIND_ENERGY_MODEL, GridImpactSnapshotCache, compile_grid_impact_snapshot,
    compose_grid_impact, get_location_grid_impact, read_grid_impact_snapshot, read_wind_scenario,
)
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


def wind(hours=4, start="2024-01-01T00:00:00Z", price=-1., flexible_mw=100., available_fraction=.5):
    frame = pd.DataFrame({"timestamp_utc": pd.date_range(start, periods=hours, freq="h"),
                          "location_id": "NODE", "system_wind_mw": 60.,
                          "system_load_mw": 100., "lmp_usd_mwh": price})
    sources = {name: {"source_type": "assumption", "ref": "synthetic unit fixture"}
               for name in ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")}
    return summarize_wind(frame, sources=sources, wind_scope="SPP_SYSTEM", load_scope="SPP_SYSTEM",
                          flexible_load_mw=datum(flexible_mw), available_fraction=datum(available_fraction))[0]


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


def test_provenance_retains_factor_value_unit_boundary_and_original_refs(tmp_path):
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
    path = snapshot(tmp_path / "factor-result.json", carbon_shift=shifted, shift_coverage=coverage())
    assert read_grid_impact_snapshot("NODE", path) == result
    assert "under the supplied factors' accounting convention" in result["basis"]["carbon_shifted"]
    assert "exclude biogenic CO2 and allocate CHP emissions to electricity" in result["basis"]["carbon_shifted"]
    assert "do not represent total physical stack CO2" in result["basis"]["carbon_shifted"]


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


def snapshot(path, **inputs):
    return compile_grid_impact_snapshot("NODE", path, **inputs)


def rewrite_snapshot(path, transform, *, checksum=True):
    """A corruption fixture, including deliberately recomputed outer checksums."""
    document = json.loads(path.read_bytes())
    transform(document["result"])
    if checksum:
        encoded = json.dumps(document["result"], sort_keys=True, separators=(",", ":"), allow_nan=False)
        document["result_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.parametrize("inputs", [
    {}, {"wind_summary": wind()}, {"wind_summary": wind(price=float("nan"))},
    {"carbon_shift": shift(), "shift_coverage": coverage(8784)},
    {"carbon_shift": shift(makeup_intensity=None), "shift_coverage": coverage()},
])
def test_snapshot_roundtrip_preserves_every_value_and_graph(tmp_path, inputs):
    path = snapshot(tmp_path / "result.json", **inputs)
    result = read_grid_impact_snapshot("NODE", path)
    assert result == compose_grid_impact("NODE", **inputs)
    assert_complete_evidence_graph(result)


def test_snapshot_bytes_are_deterministic_under_pair_permutation(tmp_path):
    raw = scheduled_pairs(3)
    first = snapshot(tmp_path / "first.json", carbon_shift=raw, shift_coverage=coverage(8784))
    raw["pairs"].reverse()
    second = snapshot(tmp_path / "second.json", carbon_shift=raw, shift_coverage=coverage(8784))
    assert first.read_bytes() == second.read_bytes()


def test_snapshot_read_cold_warm_and_missing_never_recompose(tmp_path):
    path = snapshot(tmp_path / "result.json", wind_summary=wind(), carbon_shift=shift(), shift_coverage=coverage())
    memo = GridImpactSnapshotCache()
    with patch("api.grid_impact.compose_grid_impact", side_effect=AssertionError("must stay offline")), \
         patch("api.grid_impact.wind_carbon", side_effect=AssertionError("must stay offline")), \
         patch("api.grid_impact._shift_total", side_effect=AssertionError("must stay offline")):
        first = read_grid_impact_snapshot("NODE", path, cache=memo)
        assert read_grid_impact_snapshot("NODE", path, cache=memo) == first
        missing = read_grid_impact_snapshot("OTHER", tmp_path / "missing.json", cache=memo)
    assert first["carbon_shifted_tonnes_in_observed_hours"]["value"] == -2.
    assert all(missing[field]["value"] is None and missing[field]["source_type"] == "assumption" for field in UNITS)
    assert "missing precompiled" in evidence_text(missing)
    assert_complete_evidence_graph(missing)


def test_snapshot_existing_destination_is_preserved_before_composition(tmp_path):
    path = tmp_path / "result.json"
    path.write_bytes(b"owned by another publisher")
    with patch("api.grid_impact.compose_grid_impact", side_effect=AssertionError("do not start")), pytest.raises(FileExistsError):
        snapshot(path)
    assert path.read_bytes() == b"owned by another publisher"


def test_snapshot_publication_race_cannot_overwrite_existing_destination(tmp_path):
    path = tmp_path / "result.json"
    real_link = os.link
    def competing_publication(source, destination):
        destination.write_bytes(b"racing publisher")
        real_link(source, destination)
    with patch("api.grid_impact.os.link", side_effect=competing_publication), pytest.raises(FileExistsError):
        snapshot(path)
    assert path.read_bytes() == b"racing publisher"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_snapshot_failed_publication_leaves_no_partial_file_or_temp(tmp_path, operation):
    with patch(f"api.grid_impact.os.{operation}", side_effect=OSError("simulated publication failure")), pytest.raises(OSError):
        snapshot(tmp_path / "result.json")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("warm", [False, True])
def test_snapshot_wrong_location_is_an_error_even_if_warm(tmp_path, warm):
    path = snapshot(tmp_path / "result.json")
    memo = GridImpactSnapshotCache()
    if warm:
        read_grid_impact_snapshot("NODE", path, cache=memo)
    with pytest.raises(ValueError, match="location"):
        read_grid_impact_snapshot("OTHER", path, cache=memo)


@pytest.mark.parametrize("payload", [b"{", b"[]", b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}',
    b'{"snapshot_version":"grid-impact-snapshot-v0"}'])
def test_snapshot_malformed_envelope_is_rejected(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        read_grid_impact_snapshot("NODE", path)


def test_snapshot_value_change_without_new_checksum_is_rejected(tmp_path):
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    rewrite_snapshot(path, lambda value: value["wind_absorption_mwh_in_observed_hours"].update(value=201.), checksum=False)
    with pytest.raises(ValueError, match="checksum"):
        read_grid_impact_snapshot("NODE", path)


@pytest.mark.parametrize("field,value", [("value", True), ("value", "200"), ("source_type", []),
    ("source_type", "data"), ("ref", None), ("ref", "../../other.json"),
    ("ref", EVIDENCE_PREFIX + "0" * 64), ("ref", EVIDENCE_PREFIX + "../" * 20)])
def test_snapshot_rehashed_invalid_scalars_still_fail(tmp_path, field, value):
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    rewrite_snapshot(path, lambda result: result["wind_absorption_mwh_in_observed_hours"].update({field: value}))
    with pytest.raises(ValueError):
        read_grid_impact_snapshot("NODE", path)


def test_snapshot_rehashed_invalid_graph_node_still_fails(tmp_path):
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    def tamper(result):
        next(iter(result["evidence"].values()))["method"] = "corrupted method"
    rewrite_snapshot(path, tamper)
    with pytest.raises(ValueError, match="hash"):
        read_grid_impact_snapshot("NODE", path)


def test_snapshot_rehashed_orphan_evidence_is_rejected(tmp_path):
    path = snapshot(tmp_path / "result.json")
    node = {"method": "orphaned", "inputs": [datum(1.)]}
    digest = hashlib.sha256(json.dumps(node, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rewrite_snapshot(path, lambda result: result["evidence"].update({digest: node}))
    with pytest.raises(ValueError, match="unreachable"):
        read_grid_impact_snapshot("NODE", path)


def test_snapshot_rehashed_unsupported_annual_claim_is_rejected(tmp_path):
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    rewrite_snapshot(path, lambda result: result["wind_absorption_mwh_per_year"].update(value=200.))
    with pytest.raises(ValueError, match="annual"):
        read_grid_impact_snapshot("NODE", path)


def test_snapshot_rehashed_wind_null_cannot_disagree_with_evaluable_history(tmp_path):
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    def tamper(result):
        result["wind_absorption_mwh_in_observed_hours"]["value"] = None
        result["carbon_absorbed_tonnes_in_observed_hours"]["value"] = None
    rewrite_snapshot(path, tamper)
    with pytest.raises(ValueError, match="coverage"):
        read_grid_impact_snapshot("NODE", path)


def test_snapshot_cache_validates_once_and_returns_isolated_copies(tmp_path):
    import api.grid_impact as module
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    memo = GridImpactSnapshotCache()
    with patch.object(module, "_validate_snapshot_result", wraps=module._validate_snapshot_result) as validate:
        first = read_grid_impact_snapshot("NODE", path, cache=memo)
        expected = deepcopy(first)
        first["wind_absorption_mwh_in_observed_hours"]["value"] = 99999.
        first["evidence"].clear()
        assert read_grid_impact_snapshot("NODE", path, cache=memo) == expected
        assert validate.call_count == 1


def test_snapshot_cache_detects_same_size_mutation_with_restored_timestamp(tmp_path):
    import api.grid_impact as module
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    memo = GridImpactSnapshotCache()
    read_grid_impact_snapshot("NODE", path, cache=memo)
    original = path.read_bytes()
    stat = path.stat()
    # Change a numeric value without changing byte count, then restore metadata.
    damaged = original.replace(b'"value":200.0', b'"value":201.0')
    assert damaged != original and len(damaged) == len(original)
    path.write_bytes(damaged)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="checksum"):
        read_grid_impact_snapshot("NODE", path, cache=memo)
    path.write_bytes(original)
    with patch.object(module, "_validate_snapshot_result", side_effect=AssertionError("original content already validated")):
        assert read_grid_impact_snapshot("NODE", path, cache=memo)["wind_absorption_mwh_in_observed_hours"]["value"] == 200.


def test_snapshot_cache_never_returns_deleted_file(tmp_path):
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    memo = GridImpactSnapshotCache()
    read_grid_impact_snapshot("NODE", path, cache=memo)
    path.unlink()
    result = read_grid_impact_snapshot("NODE", path, cache=memo)
    assert all(result[field]["value"] is None for field in UNITS)


@pytest.mark.parametrize("mode", ["entries", "bytes", "oversized"])
def test_snapshot_cache_limits_evict_or_bypass(tmp_path, mode):
    import api.grid_impact as module
    first, second = snapshot(tmp_path / "a.json"), snapshot(tmp_path / "b.json")
    size = first.stat().st_size
    limits = {"max_entries": 1} if mode == "entries" else {"max_bytes": size if mode == "bytes" else size - 1}
    memo = GridImpactSnapshotCache(**limits)
    with patch.object(module, "_validate_snapshot_result", wraps=module._validate_snapshot_result) as validate:
        read_grid_impact_snapshot("NODE", first, cache=memo)
        read_grid_impact_snapshot("NODE", second, cache=memo)
        read_grid_impact_snapshot("NODE", first, cache=memo)
        assert validate.call_count == 3
    assert memo._bytes <= memo.max_bytes and len(memo._entries) <= memo.max_entries


@pytest.mark.parametrize("limits", [{"max_entries": True}, {"max_entries": 0}, {"max_bytes": -1}, {"max_bytes": 1.5}])
def test_snapshot_cache_invalid_limits_are_rejected(limits):
    with pytest.raises(ValueError):
        GridImpactSnapshotCache(**limits)


@pytest.mark.parametrize("bad", [None, [], {"evaluable_hours": 4}])
def test_snapshot_rehashed_malformed_wind_context_is_rejected(tmp_path, bad):
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    def tamper(result):
        old = result["evidence_context"]["wind"][len(EVIDENCE_PREFIX):]
        node = result["evidence"].pop(old)
        node["coverage"] = bad
        key = hashlib.sha256(json.dumps(node, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        result["evidence"][key] = node
        result["evidence_context"]["wind"] = EVIDENCE_PREFIX + key
    rewrite_snapshot(path, tamper)
    with pytest.raises(ValueError):
        read_grid_impact_snapshot("NODE", path)


@pytest.mark.parametrize("key,value", [("units", {}), ("basis", {}), ("boundary", "lifecycle_co2e"),
    ("evidence", []), ("evidence_context", []), ("location_id", "OTHER")])
def test_snapshot_rehashed_incompatible_metadata_is_rejected(tmp_path, key, value):
    path = snapshot(tmp_path / "result.json")
    rewrite_snapshot(path, lambda result: result.update({key: value}))
    with pytest.raises(ValueError):
        read_grid_impact_snapshot("NODE", path)


def test_snapshot_cache_revalidates_a_new_valid_version_and_evicts_old_version(tmp_path):
    import api.grid_impact as module
    path = snapshot(tmp_path / "result.json", wind_summary=wind())
    next_path = snapshot(tmp_path / "new.json", wind_summary=wind(price=10.))
    memo = GridImpactSnapshotCache()
    with patch.object(module, "_validate_snapshot_result", wraps=module._validate_snapshot_result) as validate:
        assert read_grid_impact_snapshot("NODE", path, cache=memo)["wind_absorption_mwh_in_observed_hours"]["value"] == 200.
        path.write_bytes(next_path.read_bytes())
        updated = read_grid_impact_snapshot("NODE", path, cache=memo)
        assert updated["wind_absorption_mwh_in_observed_hours"]["value"] == 0.
        assert read_grid_impact_snapshot("NODE", path, cache=memo) == updated
        assert validate.call_count == 2
    assert len(memo._entries) == 1


def test_snapshot_invalid_cache_argument_is_explicit(tmp_path):
    with pytest.raises(ValueError, match="cache"):
        read_grid_impact_snapshot("NODE", tmp_path / "missing.json", cache={})


def wind_scenario_context(result):
    return result["evidence"][result["evidence_context"]["wind"][len(EVIDENCE_PREFIX):]]


def test_wind_producer_controls_survive_composition_and_snapshot(tmp_path):
    raw = wind(flexible_mw=123.45678912345, available_fraction=.12345678912345)
    assert raw["energy_model"] == WIND_ENERGY_MODEL
    path = snapshot(tmp_path / "bound.json", wind_summary=raw)
    result = read_grid_impact_snapshot("NODE", path)
    context = wind_scenario_context(result)
    assert context["energy_model"] == raw["energy_model"]
    assert context["scenario_inputs"] == raw["scenario_inputs"]
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == 4 * 123.45678912345 * .12345678912345


@pytest.mark.parametrize("field,value", [("flexible_load_mw", 101.), ("available_fraction", .75)])
def test_wind_declared_control_product_must_match_observed_energy(field, value):
    raw = wind()
    raw["scenario_inputs"][field]["value"] = value
    with pytest.raises(ValueError, match="disagrees"):
        compose_grid_impact("NODE", wind_summary=raw)


@pytest.mark.parametrize("bad", [None, {}, {"flexible_load_mw": datum(100.)},
    {"flexible_load_mw": datum(100.), "available_fraction": datum(.5), "site_exposure": datum(.5)}])
def test_wind_partial_or_unrecognized_control_shape_is_rejected(bad):
    raw = wind()
    raw["scenario_inputs"] = bad
    with pytest.raises(ValueError, match="structured controls"):
        compose_grid_impact("NODE", wind_summary=raw)


def test_wind_unrecognized_energy_model_cannot_claim_linear_recompute():
    raw = wind()
    raw["energy_model"] = "hypothetical capped recoverable wind model"
    with pytest.raises(ValueError, match="energy_model"):
        compose_grid_impact("NODE", wind_summary=raw)


def test_wind_legacy_snapshot_remains_readable_but_not_recomputable(tmp_path):
    raw = wind()
    raw.pop("energy_model")
    raw.pop("scenario_inputs")
    path = snapshot(tmp_path / "legacy.json", wind_summary=raw)
    assert read_grid_impact_snapshot("NODE", path)["wind_absorption_mwh_in_observed_hours"]["value"] == 200.
    with pytest.raises(ValueError, match="Legacy fixed"):
        read_wind_scenario("NODE", path, flexible_load_mw=datum(50.), available_fraction=datum(.25))


def test_wind_scenario_uses_new_controls_and_preserves_original_lineage(tmp_path):
    import api.grid_impact as module
    raw = wind()
    path = snapshot(tmp_path / "bound.json", wind_summary=raw)
    controls = {"flexible_load_mw": datum(80., "explicit new flexible capacity assumption"),
                "available_fraction": datum(.25, "explicit new available upward fraction assumption")}
    result = read_wind_scenario("NODE", path, **controls)
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == 80.
    assert result["wind_absorption_mwh_per_year"]["value"] is None
    assert result["carbon_absorbed_tonnes_in_observed_hours"]["value"] == 0.
    assert all(result[field]["value"] is None for field in ("carbon_shifted_tonnes_in_observed_hours", "carbon_shifted_tonnes_per_year"))
    context = wind_scenario_context(result)
    assert context["scenario_inputs"] == controls
    baseline = result["evidence"][context["baseline"][len(EVIDENCE_PREFIX):]]
    original_context = result["evidence"][baseline["wind_context"][len(EVIDENCE_PREFIX):]]
    assert baseline["wind_absorption_mwh_in_observed_hours"]["value"] == 200.
    assert baseline["units"]["wind_absorption_mwh_in_observed_hours"] == "MWh"
    assert original_context["scenario_inputs"] == raw["scenario_inputs"]
    assert "audit lineage only" in baseline["method"]
    assert str(path) == baseline["snapshot_path"]
    assert "explicit new flexible capacity assumption" in evidence_text(result)
    assert "egrid2023_technical_guide" in evidence_text(result)
    assert all(result[field]["source_type"] == "assumption" for field in UNITS)
    assert_complete_evidence_graph(result)
    module._validate_snapshot_result(result, "NODE")


@pytest.mark.parametrize("inputs", [{}, {"flexible_load_mw": datum(10.)}, {"available_fraction": datum(.5)}])
def test_wind_scenario_has_no_hidden_defaults_for_request_controls(tmp_path, inputs):
    with pytest.raises(TypeError):
        read_wind_scenario("NODE", tmp_path / "missing.json", **inputs)


@pytest.mark.parametrize("field,value", [("flexible_load_mw", None), ("flexible_load_mw", -1.),
    ("flexible_load_mw", True), ("flexible_load_mw", math.inf), ("available_fraction", 1.01),
    ("available_fraction", -1.), ("available_fraction", math.nan), ("available_fraction", "0.5")])
def test_wind_scenario_invalid_request_controls_fail_even_without_a_file(tmp_path, field, value):
    controls = {"flexible_load_mw": datum(10.), "available_fraction": datum(.5)}
    controls[field]["value"] = value
    with pytest.raises(ValueError):
        read_wind_scenario("NODE", tmp_path / "missing.json", **controls)


@pytest.mark.parametrize("energy,intensity", [(10., 300.), (0., 300.), (10., None)])
def test_wind_scenario_never_carries_or_rescales_an_existing_shift_schedule(tmp_path, energy, intensity):
    path = snapshot(tmp_path / "combined.json", wind_summary=wind(), carbon_shift=shift(energy=energy, makeup_intensity=intensity), shift_coverage=coverage())
    with pytest.raises(ValueError, match="wind-only"):
        read_wind_scenario("NODE", path, flexible_load_mw=datum(50.), available_fraction=datum(.25))


@pytest.mark.parametrize("load,fraction", [(0., .5), (100., 0.)])
def test_wind_scenario_recomputes_from_counts_when_old_capacity_was_zero(tmp_path, load, fraction):
    path = snapshot(tmp_path / "zero-baseline.json", wind_summary=wind(flexible_mw=load, available_fraction=fraction))
    result = read_wind_scenario("NODE", path, flexible_load_mw=datum(80.), available_fraction=datum(.25))
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == 80.


def test_wind_scenario_known_zero_and_unknown_history_remain_distinct(tmp_path):
    known = snapshot(tmp_path / "known.json", wind_summary=wind(price=10.))
    unknown = snapshot(tmp_path / "unknown.json", wind_summary=wind(price=math.nan))
    controls = {"flexible_load_mw": datum(0.), "available_fraction": datum(0.)}
    assert read_wind_scenario("NODE", known, **controls)["wind_absorption_mwh_in_observed_hours"]["value"] == 0.
    result = read_wind_scenario("NODE", unknown, **controls)
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] is None
    assert result["carbon_absorbed_tonnes_in_observed_hours"]["value"] is None
    assert all(read_wind_scenario("NODE", tmp_path / "missing.json", **controls)[field]["value"] is None for field in UNITS)


def test_wind_scenario_annual_total_uses_complete_year_without_extrapolation(tmp_path):
    path = snapshot(tmp_path / "year.json", wind_summary=wind(8784))
    result = read_wind_scenario("NODE", path, flexible_load_mw=datum(80.), available_fraction=datum(.25))
    assert result["wind_absorption_mwh_per_year"]["value"] == 8784 * 80. * .25
    assert result["carbon_absorbed_tonnes_per_year"]["value"] == 0.


def test_wind_scenario_is_deterministic_isolated_and_avoids_heavy_revalidation(tmp_path):
    import api.grid_impact as module
    path = snapshot(tmp_path / "bound.json", wind_summary=wind())
    memo = GridImpactSnapshotCache()
    read_grid_impact_snapshot("NODE", path, cache=memo)
    controls = {"flexible_load_mw": datum(80.), "available_fraction": datum(.25)}
    with patch.object(module, "_validate_snapshot_result", side_effect=AssertionError("validated cache should be reused")), \
         patch.object(module, "compose_grid_impact", side_effect=AssertionError("no raw composition")), \
         patch("pipeline.wind_signal.wind_oversupply_hours", side_effect=AssertionError("no hourly screening")), \
         patch("pandas.read_parquet", side_effect=AssertionError("no hourly cache scanning")):
        first = read_wind_scenario("NODE", path, cache=memo, **controls)
        second = read_wind_scenario("NODE", path, cache=memo, **dict(reversed(list(controls.items()))))
        assert json.dumps(first) == json.dumps(second)
        first["evidence"].clear()
        assert read_wind_scenario("NODE", path, cache=memo, **controls) == second
        assert read_grid_impact_snapshot("NODE", path, cache=memo)["wind_absorption_mwh_in_observed_hours"]["value"] == 200.


def test_wind_snapshot_published_period_cannot_disagree_with_screening_context(tmp_path):
    path = snapshot(tmp_path / "bound.json", wind_summary=wind())
    def change_period(result):
        result["coverage"]["wind"]["period_start_utc"] = "2024-01-01T01:00:00+00:00"
        result["coverage"]["wind"]["period_end_exclusive_utc"] = "2024-01-01T05:00:00+00:00"
    rewrite_snapshot(path, change_period)
    with pytest.raises(ValueError, match="published coverage"):
        read_wind_scenario("NODE", path, flexible_load_mw=datum(10.), available_fraction=datum(.5))


def test_wind_structured_inputs_cannot_hide_tiny_nonzero_energy_as_zero():
    raw = wind(flexible_mw=1e-10)
    raw["wind_absorption_mwh_in_observed_hours"]["value"] = 0.
    with pytest.raises(ValueError, match="disagrees"):
        compose_grid_impact("NODE", wind_summary=raw)


@pytest.mark.parametrize("fraction", [0., 1e-308])
def test_wind_scenario_extreme_capacity_with_zero_or_tiny_availability_stays_finite(tmp_path, fraction):
    import api.grid_impact as module
    path = snapshot(tmp_path / "bound.json", wind_summary=wind())
    controls = {"flexible_load_mw": datum(1e308, "synthetic extreme capacity input"),
                "available_fraction": datum(fraction, "synthetic extreme availability input")}
    result = read_wind_scenario("NODE", path, **controls)
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == 4 * (1e308 * fraction)
    assert wind_scenario_context(result)["scenario_inputs"] == controls
    assert result["wind_absorption_mwh_in_observed_hours"]["source_type"] == "assumption"
    assert_complete_evidence_graph(result)
    module._validate_snapshot_result(result, "NODE")


def test_wind_scenario_still_rejects_a_true_unrepresentable_energy_total(tmp_path):
    path = snapshot(tmp_path / "bound.json", wind_summary=wind())
    with pytest.raises(ValueError, match="finite"):
        read_wind_scenario("NODE", path, flexible_load_mw=datum(1e308), available_fraction=datum(1.))


def test_wind_earlier_multiplication_order_is_accepted_within_one_float_step(tmp_path):
    raw = wind(hours=3, flexible_mw=.3, available_fraction=.3)
    previous = (3 * .3) * .3
    current = 3 * (.3 * .3)
    assert previous != current and math.nextafter(previous, current) == current
    raw["wind_absorption_mwh_in_observed_hours"]["value"] = previous
    path = snapshot(tmp_path / "earlier-order.json", wind_summary=raw)
    assert read_grid_impact_snapshot("NODE", path)["wind_absorption_mwh_in_observed_hours"]["value"] == previous
    result = read_wind_scenario("NODE", path, flexible_load_mw=datum(.3), available_fraction=datum(.3))
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == current
