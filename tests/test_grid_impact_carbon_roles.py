"""Authored integration fixtures: shared citations survive saved API evidence."""
from copy import deepcopy
import json
import socket

import pandas as pd
import pytest

import api.grid_impact as api
from pipeline.carbon import BOUNDARY, FACTOR_UNIT, fuel_mix_intensity, shift_carbon

HOURS = ("2025-01-01T00:00:00+00:00", "2025-01-01T01:00:00+00:00")
SHARED = ' {"table": "authored software fixture, no grid observation"} '
POLICY = {"source_type": "assumption", "ref": "Authored factor application and conserved schedule"}


def datum(value, kind="assumption"):
    return {"value": value, "source_type": kind, "ref": SHARED}


@pytest.mark.parametrize("kind", ["data", "model", "assumption"])
@pytest.mark.parametrize("unknown", [False, True], ids=["known", "unknown_makeup"])
def test_fuel_hour_and_shift_roles_survive_compiled_response(tmp_path, monkeypatch, kind, unknown):
    def forbidden(*args, **kwargs):
        raise AssertionError("Saved carbon evidence requires neither network nor recomposition")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)

    values = {(HOURS[0], "Coal"): 3., (HOURS[0], "Wind"): 1.,
              (HOURS[1], "Coal"): None if unknown else 1., (HOURS[1], "Wind"): 3.}
    frame = pd.DataFrame([
        {"timestamp_utc": hour, "fuel": fuel, "generation_mwh": value,
         "generation_status": "incomplete_observations" if value is None else "complete",
         "source_type": kind, "ref": SHARED}
        for (hour, fuel), value in values.items()
    ], dtype=object)
    factors = {fuel: {**datum(value, kind), "unit": FACTOR_UNIT, "boundary": BOUNDARY}
               for fuel, value in (("Coal", 1000.), ("Wind", 0.))}
    original_frame, original_factors = deepcopy((frame, factors))
    intensities = fuel_mix_intensity(frame, factors, expected_fuels=["Coal", "Wind"], application_source=POLICY)
    shifted = shift_carbon(intensities,
        [{"risk_hour": HOURS[0], "makeup_hour": HOURS[1], "mwh": datum(2.)}],
        selection_source=POLICY, hourly_limits={
            HOURS[0]: {"removable_mwh": datum(2.)}, HOURS[1]: {"makeup_capacity_mwh": datum(2.)}})
    coverage = {"period_start_utc": HOURS[0], "period_end_exclusive_utc": "2025-01-01T02:00:00+00:00",
                "observed_hours": datum(2), "evaluable_hours": datum(1 if unknown else 2),
                "unknown_hours": datum(1 if unknown else 0), "missing_interval_hours": datum(0),
                "schedule_scope": "complete_period"}
    original_shift, original_coverage = deepcopy((shifted, coverage))
    path = tmp_path / "authored-carbon.snapshot.json"
    api.compile_grid_impact_snapshot("TEST_NODE", path, carbon_shift=shifted, shift_coverage=coverage)
    monkeypatch.setattr(api, "compose_grid_impact", forbidden)
    cache = api.GridImpactSnapshotCache()
    result = api.read_grid_impact_snapshot("TEST_NODE", path, cache=cache, required=True)
    assert api.read_grid_impact_snapshot("TEST_NODE", path, cache=cache, required=True) == result
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] == (None if unknown else 1.)
    assert result["carbon_shifted_tonnes_per_year"]["value"] is None
    assert result["units"]["carbon_shifted_tonnes_in_observed_hours"] == "tonnes CO2"
    assert all(set(result[key]) == {"value", "source_type", "ref"} and result[key]["source_type"] == "assumption"
               for key in api.UNITS)

    # The local graph may reuse evidence; each distinct fuel/hour must still exist.
    inputs = [item for node in result["evidence"].values() for item in node.get("inputs", [])]
    generation = {(item["timestamp_utc"], item["fuel"]): item for item in inputs
                  if item.get("quantity") == "generation_mwh"}
    assert set(generation) == set(values)
    for (hour, fuel), value in values.items():
        assert generation[hour, fuel] == {**datum(value, kind), "quantity": "generation_mwh",
                                         "fuel": fuel, "timestamp_utc": hour, "unit": "MWh"}
    for fuel, factor in factors.items():
        assert {**factor, "quantity": "factor_kg_co2_per_mwh", "fuel": fuel} in inputs
    annual_factors = [item for item in inputs if item.get("quantity") == "factor_kg_co2_per_mwh"]
    assert all("timestamp_utc" not in item for item in annual_factors)
    roles = {item["role"]: item for item in inputs if item.get("quantity") == "intensity_kg_co2_per_mwh"}
    assert set(roles) == {"risk", "makeup"}
    for role, hour, intensity in zip(("risk", "makeup"), HOURS, intensities):
        actual = roles[role]
        assert actual["timestamp_utc"] == hour and actual["unit"] == FACTOR_UNIT and actual["boundary"] == BOUNDARY
        assert actual["value"] == intensity["intensity_kg_co2_per_mwh"]["value"]
        assert actual["source_type"] == intensity["intensity_kg_co2_per_mwh"]["source_type"]
        assert actual["ref"].startswith(api.EVIDENCE_PREFIX)
        assert actual["ref"][len(api.EVIDENCE_PREFIX):] in result["evidence"]
    json.dumps(result, allow_nan=False)
    pd.testing.assert_frame_equal(frame, original_frame, check_exact=True)
    assert factors == original_factors and shifted == original_shift and coverage == original_coverage
