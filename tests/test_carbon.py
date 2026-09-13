"""Software fixtures exercise accounting; these are not SPP emission estimates."""
from copy import deepcopy
import hashlib
from io import BytesIO
from itertools import permutations
import json
import math

import numpy as np
import pandas as pd
import pytest
from openpyxl import Workbook

import pipeline.carbon as carbon
from pipeline.carbon import (
    BOUNDARY, FACTOR_UNIT, WIND_OPERATIONAL_CO2_FACTOR,
    fuel_mix_intensity, load_egrid_swpp_factors, normalize_spp_generation_archive,
    shift_carbon, wind_carbon,
)


HOURS = [f"2024-01-01T0{hour}:00:00+00:00" for hour in range(4)]
POLICY = {"source_type": "assumption", "ref": "test-only explicit scenario selection; not a dispatch observation"}


def datum(value, kind="data", ref="test-only authored input"):
    return {"value": value, "source_type": kind, "ref": ref}


def factor(value, kind="data"):
    return {**datum(value, kind), "unit": FACTOR_UNIT, "boundary": BOUNDARY}


def mix(rows=None):
    rows = rows or [(HOURS[0], "coal", 30.0), (HOURS[0], "wind", 70.0)]
    return pd.DataFrame([{ "timestamp_utc": time, "fuel": fuel, "generation_mwh": energy,
                           "source_type": "data", "ref": f"test-only fuel row {fuel} {time}" }
                         for time, fuel, energy in rows])


def intensity(values=(800, 200, 900, 400)):
    return [{"timestamp_utc": time, "boundary": BOUNDARY,
             "intensity_kg_co2_per_mwh": datum(value, "model", f"test-only intensity {time}")}
            for time, value in zip(HOURS, values)]


def limits():
    return {time: {"removable_mwh": datum(10), "makeup_capacity_mwh": datum(10)} for time in HOURS}


def moves(risk=HOURS[0], makeup=HOURS[1], amount=10):
    return [{"risk_hour": risk, "makeup_hour": makeup, "mwh": datum(amount)}]


def calculate(frame, factors=None, **kwargs):
    return fuel_mix_intensity(frame, factors or {"coal": factor(1000), "wind": factor(0)},
                              expected_fuels=["coal", "wind"], application_source=POLICY, **kwargs)


def shift(records=None, schedule=None, cap=None):
    return shift_carbon(intensity() if records is None else records,
                        moves() if schedule is None else schedule,
                        selection_source=POLICY, hourly_limits=limits() if cap is None else cap)


def test_full_generation_denominator_keeps_zero_emitting_energy_and_exact_provenance():
    result = calculate(mix())[0]
    assert result["intensity_kg_co2_per_mwh"]["value"] == 300
    assert result["generation_mwh"]["value"] == 100
    assert result["known_generation_mwh"]["value"] == 100
    assert result["factor_coverage_fraction"]["value"] == 1
    assert result["intensity_kg_co2_per_mwh"]["source_type"] == "assumption"
    ref = result["intensity_kg_co2_per_mwh"]["ref"]
    assert "test-only fuel row coal" in ref and "test-only fuel row wind" in ref
    assert FACTOR_UNIT in ref and "not marginal" in ref


def test_unknown_positive_fuel_is_not_zero_or_renormalized_away():
    result = calculate(mix(), {"wind": factor(0)})[0]
    assert result["intensity_kg_co2_per_mwh"]["value"] is None
    assert result["status"] == "missing_factors"
    assert result["missing_fuels"] == ["coal"]
    assert result["generation_mwh"]["value"] == 100
    assert result["factor_coverage_fraction"]["value"] == 0.7


def test_missing_fuel_row_cannot_make_wind_only_mix_look_like_full_grid_zero():
    result = calculate(mix([(HOURS[0], "wind", 70)]))[0]
    assert result["status"] == "missing_generation"
    assert result["missing_generation_fuels"] == ["coal"]
    assert result["intensity_kg_co2_per_mwh"]["value"] is None
    assert result["generation_mwh"]["value"] is None
    assert result["reported_generation_mwh"]["value"] == 70
    assert result["factor_coverage_fraction"]["value"] is None


def test_explicit_zero_generation_needs_no_missing_fuel_factor():
    result = calculate(mix([(HOURS[0], "coal", 0), (HOURS[0], "wind", 70)]), {"wind": factor(0)})[0]
    assert result["intensity_kg_co2_per_mwh"]["value"] == 0
    assert result["status"] == "available"


def test_zero_total_generation_is_unavailable_not_zero_intensity():
    result = calculate(mix([(HOURS[0], "coal", 0), (HOURS[0], "wind", 0)]))[0]
    assert result["status"] == "no_generation"
    assert result["intensity_kg_co2_per_mwh"]["value"] is None
    assert result["factor_coverage_fraction"]["value"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "1"])
def test_invalid_generation_never_becomes_zero(bad):
    frame = mix().astype({"generation_mwh": object})
    frame.loc[0, "generation_mwh"] = bad
    with pytest.raises(ValueError):
        calculate(frame)


@pytest.mark.parametrize("change", [
    {"value": -1}, {"value": True}, {"value": float("nan")},
    {"unit": "lbCO2/MWh"}, {"unit": "kgCO2e/MWh"}, {"boundary": "lifecycle_co2e"},
    {"source_type": "unknown"}, {"ref": ""},
])
def test_factor_requires_explicit_compatible_units_boundary_and_provenance(change):
    with pytest.raises(ValueError):
        calculate(mix(), {"coal": {**factor(1000), **change}, "wind": factor(0)})


@pytest.mark.parametrize("time", ["2024-01-01T00:00:00", "2024-01-01T00:30:00Z", None])
def test_hourly_rows_require_timezone_and_complete_utc_hours(time):
    frame = mix()
    frame.loc[0, "timestamp_utc"] = time
    with pytest.raises(ValueError):
        calculate(frame)


def test_duplicate_fuels_and_unexpected_fuels_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        calculate(pd.concat([mix(), mix()]))
    with pytest.raises(ValueError, match="outside"):
        calculate(mix([(HOURS[0], "other", 10)]))


@pytest.mark.parametrize("expected", [[], ["wind", "wind"], "wind", [""], None, {"wind"}])
def test_complete_fuel_universe_must_be_explicit(expected):
    with pytest.raises(ValueError, match="expected_fuels"):
        fuel_mix_intensity(mix(), {}, expected_fuels=expected, application_source=POLICY)


def test_duplicate_columns_cannot_silently_discard_input_evidence():
    frame = pd.concat([mix(), mix()[["source_type"]]], axis=1)
    with pytest.raises(ValueError, match="columns"):
        calculate(frame)


def test_intensity_is_deterministic_and_does_not_mutate_inputs():
    frame = mix()
    original = frame.copy(deep=True)
    factors = {"coal": factor(1000), "wind": factor(0)}
    saved = deepcopy(factors)
    assert calculate(frame, factors) == calculate(frame.iloc[::-1], factors)
    pd.testing.assert_frame_equal(frame, original)
    assert factors == saved
    json.dumps(calculate(frame, factors), allow_nan=False)


def test_shift_preserves_equal_energy_and_positive_signed_co2_difference():
    result = shift()
    assert result["mwh_removed"]["value"] == result["mwh_made_up"]["value"] == 10
    assert result["carbon_shifted_kg_co2"]["value"] == 6000
    assert result["carbon_shifted_kg_co2"]["source_type"] == "assumption"
    assert result["pairs"][0]["mwh_removed"]["source_type"] == "assumption"
    assert HOURS[0] in result["carbon_shifted_kg_co2"]["ref"]
    assert HOURS[1] in result["carbon_shifted_kg_co2"]["ref"]
    assert "test-only intensity" in result["carbon_shifted_kg_co2"]["ref"]


def test_dirtier_makeup_has_negative_shift_and_is_not_clamped():
    assert shift(schedule=moves(HOURS[1], HOURS[2]))["carbon_shifted_kg_co2"]["value"] == -7000


def test_equal_intensity_and_zero_moved_energy_have_zero_shift():
    assert shift(records=intensity((200, 200, 200, 200)))["carbon_shifted_kg_co2"]["value"] == 0
    assert shift(schedule=moves(amount=0))["carbon_shifted_kg_co2"]["value"] == 0


def test_unknown_intensity_propagates_to_total_instead_of_summing_only_known_pairs():
    result = shift(records=intensity((800, 200, None, 400)), schedule=moves() + moves(HOURS[2], HOURS[3]))
    assert result["pairs"][0]["carbon_shifted_kg_co2"]["value"] == 6000
    assert result["pairs"][1]["carbon_shifted_kg_co2"]["value"] is None
    assert result["carbon_shifted_kg_co2"]["value"] is None
    assert result["mwh_removed"]["value"] == result["mwh_made_up"]["value"] == 20


def test_shared_makeup_capacity_and_shared_risk_energy_cannot_be_double_counted():
    with pytest.raises(ValueError, match="energy limit"):
        shift(schedule=moves(HOURS[0], HOURS[3], 6) + moves(HOURS[1], HOURS[3], 6))
    with pytest.raises(ValueError, match="energy limit"):
        shift(schedule=moves(HOURS[0], HOURS[1], 6) + moves(HOURS[0], HOURS[2], 6))
    result = shift(schedule=moves(HOURS[0], HOURS[3], 5) + moves(HOURS[1], HOURS[3], 5))
    assert result["mwh_made_up"]["value"] == 10


@pytest.mark.parametrize("schedule,match", [
    (moves() + moves(), "Duplicate shift pair"),
    (moves(HOURS[0], HOURS[0]), "later"),
    (moves(HOURS[1], HOURS[0]), "later"),
    (moves(amount=-1), "nonnegative"),
])
def test_invalid_shift_schedules_are_rejected(schedule, match):
    with pytest.raises(ValueError, match=match):
        shift(schedule=schedule)


def test_missing_hour_or_energy_limit_does_not_get_an_inferred_replacement():
    with pytest.raises(ValueError, match="intensity record"):
        shift(records=intensity()[:1])
    with pytest.raises(ValueError, match="energy limit"):
        shift(cap={})
    with pytest.raises(ValueError, match="Duplicate hourly intensity"):
        shift(records=intensity() + intensity())


@pytest.mark.parametrize("kwargs", [
    {"cap": []}, {"cap": {HOURS[0]: None}},
    {"records": [None]}, {"records": [{"timestamp_utc": HOURS[0]}]},
    {"schedule": [None]}, {"schedule": [{"risk_hour": HOURS[0]}]},
])
def test_malformed_accounting_records_fail_explicitly(kwargs):
    with pytest.raises(ValueError):
        shift(**kwargs)


def test_wind_has_zero_direct_operational_co2_without_claiming_physical_carbon_removal():
    result = wind_carbon(datum(25), selection_source=POLICY)
    assert result["wind_energy_absorbed_mwh"]["value"] == 25
    assert result["wind_operational_co2_kg"]["value"] == 0
    assert result["wind_operational_co2_kg"]["source_type"] == "assumption"
    assert "www.epa.gov" in result["wind_operational_co2_kg"]["ref"]
    assert "excludes lifecycle" in result["wind_operational_co2_kg"]["ref"]
    assert "not atmospheric carbon removal" in result["interpretation"]
    assert WIND_OPERATIONAL_CO2_FACTOR["value"] == 0


def test_nonzero_or_lifecycle_wind_factor_requires_a_separate_model():
    with pytest.raises(ValueError, match="operational CO2 is zero"):
        wind_carbon(datum(25), selection_source=POLICY, factor=factor(11))
    with pytest.raises(ValueError, match="lifecycle"):
        wind_carbon(datum(25), selection_source=POLICY, factor={**factor(11), "boundary": "lifecycle_co2e"})


def test_outputs_never_claim_avoided_emissions_and_are_reproducible_offline(monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_args, **_kwargs: pytest.fail("No network in carbon arithmetic"))
    original = moves()
    first = shift(schedule=original)
    assert first == shift(schedule=deepcopy(original))
    result = [calculate(mix()), first, wind_carbon(datum(25), selection_source=POLICY)]
    assert "avoided" not in json.dumps(result, allow_nan=False).lower()
    assert original == moves()


@pytest.mark.parametrize("ref", ["PLACEHOLDER fuel factor", "mock://wind-hour", "Illustrative scenario"])
@pytest.mark.parametrize("kind", ["data", "model"])
def test_placeholder_reference_cannot_be_laundered_through_any_carbon_input(ref, kind):
    authored = datum(10, kind, ref)
    frame = mix()
    frame.loc[0, ["source_type", "ref"]] = [kind, ref]
    wrong_factor = {**factor(1000), **authored}
    observations = intensity()
    observations[0]["intensity_kg_co2_per_mwh"] = authored
    cap = limits()
    cap[HOURS[0]]["removable_mwh"] = authored
    schedule = moves()
    schedule[0]["mwh"] = authored
    calls = [
        lambda: calculate(frame),
        lambda: calculate(mix(), {"coal": wrong_factor, "wind": factor(0)}),
        lambda: fuel_mix_intensity(mix(), {}, expected_fuels=["coal", "wind"], application_source=authored),
        lambda: shift(records=observations),
        lambda: shift(cap=cap),
        lambda: shift(schedule=schedule),
        lambda: shift_carbon(intensity(), moves(), hourly_limits=limits(), selection_source=authored),
        lambda: wind_carbon(authored, selection_source=POLICY),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="Placeholder"):
            call()


def test_explicit_placeholder_assumptions_keep_their_exact_refs_and_never_upgrade():
    origin = datum(25, "assumption", "mock://Illustrative wind PLACEHOLDER")
    result = wind_carbon(origin, selection_source={"source_type": "data", "ref": "test-only observed selection"})
    for key in ("wind_energy_absorbed_mwh", "wind_operational_co2_kg"):
        assert result[key]["source_type"] == "assumption"
        assert origin["ref"] in result[key]["ref"]
    assert origin == datum(25, "assumption", "mock://Illustrative wind PLACEHOLDER")


def test_fuel_row_and_factor_order_produce_byte_identical_json():
    frame = mix([
        (HOURS[0], "coal", 0.1), (HOURS[0], "wind", 0.2),
        (HOURS[1], "coal", 1e12), (HOURS[1], "wind", 0.0001),
    ])
    expected = json.dumps(calculate(frame), allow_nan=False)
    for order in permutations(range(len(frame))):
        actual = calculate(frame.iloc[list(order)], {"wind": factor(0), "coal": factor(1000)})
        assert json.dumps(actual, allow_nan=False) == expected


def test_move_intensity_and_limit_order_produce_byte_identical_json():
    schedule = moves(HOURS[0], HOURS[1], 0.1) + moves(HOURS[1], HOURS[3], 0.2) + moves(HOURS[2], HOURS[3], 0.3)
    expected = json.dumps(shift(schedule=schedule), allow_nan=False)
    for order in permutations(schedule):
        actual = shift(records=list(reversed(intensity())), schedule=order,
                       cap=dict(reversed(list(limits().items()))))
        assert json.dumps(actual, allow_nan=False) == expected


@pytest.mark.parametrize("shared", ["risk", "makeup"])
def test_shared_capacity_tolerates_only_binary64_sum_roundoff_independent_of_order(shared):
    cap = limits()
    if shared == "risk":
        cap[HOURS[0]]["removable_mwh"] = datum(0.3)
        schedule = moves(HOURS[0], HOURS[1], 0.1) + moves(HOURS[0], HOURS[2], 0.2)
    else:
        cap[HOURS[2]]["makeup_capacity_mwh"] = datum(0.3)
        schedule = moves(HOURS[0], HOURS[2], 0.1) + moves(HOURS[1], HOURS[2], 0.2)
    result = shift(schedule=schedule, cap=cap)
    assert result["mwh_made_up"]["value"] == math.fsum([0.1, 0.2])
    assert json.dumps(result) == json.dumps(shift(schedule=list(reversed(schedule)), cap=cap))
    schedule[1]["mwh"] = datum(0.20000001)
    for order in permutations(schedule):
        with pytest.raises(ValueError, match="energy limit"):
            shift(schedule=order, cap=cap)


def test_zero_capacity_never_gets_a_positive_roundoff_allowance():
    cap = limits()
    cap[HOURS[1]]["makeup_capacity_mwh"] = datum(0)
    with pytest.raises(ValueError, match="energy limit"):
        shift(schedule=moves(amount=math.nextafter(0.0, math.inf)), cap=cap)


@pytest.mark.parametrize("records,schedule", [([], []), ([], moves()), (intensity(), [])])
def test_absent_observations_or_schedule_cannot_claim_known_zero(records, schedule):
    with pytest.raises(ValueError, match="No intensity observations|No explicit energy moves"):
        shift(records=records, schedule=schedule)


def test_empty_fuel_frame_is_no_observations_not_an_invented_zero_hour():
    assert calculate(mix().iloc[:0]) == []


def test_explicit_zero_energy_with_unknown_intensity_remains_unknown():
    result = shift(records=intensity((None, None, None, None)), schedule=moves(amount=0))
    assert result["carbon_shifted_kg_co2"]["value"] is None
    assert result["mwh_removed"]["value"] == 0


# Synthetic workbook fixtures reproduce EPA's schema, not its whole document.
# Pin only the expected digest to each explicitly authored fixture. Production
# remains pinned to the separately verified official revision-2 bytes.
EPA_CODES = ["YEAR", "BACODE", "BACCO2RT", "BAGCO2RT", "BAOCO2RT"]
EPA_LABELS = ["Data Year", "Balancing Authority Code", *[
    f"BA annual CO2 {fuel} output emission rate (kg/MWh)" for fuel in ("coal", "gas", "oil")
]]
EPA_ROW = [2023, "SWPP", 1024.2995616, 488.030508, 1300.920264]


def epa_workbook(*, rows=None, labels=None, codes=None, sheet_name="BA23"):
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.append(EPA_LABELS if labels is None else labels)
    sheet.append(EPA_CODES if codes is None else codes)
    for row in [EPA_ROW] if rows is None else rows:
        sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def epa_cache(tmp_path, monkeypatch, *, content=None, origin_change=None):
    content = epa_workbook() if content is None else content
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(carbon, "EGRID_METRIC_SHA256", digest)
    origin = {"source_type": "data", "ref": carbon.EGRID_METRIC_URL,
              "retrieved_utc": "2026-09-13T08:06:02Z", "sha256": digest}
    origin.update(origin_change or {})
    path = tmp_path / "synthetic-epa.parquet"
    pd.DataFrame([{"content": content, "source_json": json.dumps(origin)}]).to_parquet(path, index=False)
    return path


def test_verified_egrid_schema_extracts_swpp_factors_with_exact_document_cells(tmp_path, monkeypatch):
    path = epa_cache(tmp_path, monkeypatch)
    result = load_egrid_swpp_factors(path)
    expected = {"Coal": (1024.2995616, "BACCO2RT", "C3"),
                "Natural Gas": (488.030508, "BAGCO2RT", "D3"),
                "Oil": (1300.920264, "BAOCO2RT", "E3")}
    for fuel, (value, code, cell) in expected.items():
        assert set(result[fuel]) == {"value", "source_type", "ref", "unit", "boundary"}
        assert result[fuel]["value"] == value
        assert result[fuel]["source_type"] == "data"
        assert result[fuel]["unit"] == FACTOR_UNIT and result[fuel]["boundary"] == BOUNDARY
        ref = json.loads(result[fuel]["ref"])
        assert ref["ref"] == carbon.EGRID_METRIC_URL
        assert (ref["sheet"], ref["YEAR"], ref["BACODE"], ref["field"], ref["cell"]) == ("BA23", 2023, "SWPP", code, cell)
        assert ref["sha256"] == carbon.EGRID_METRIC_SHA256
        assert ref["retrieved_utc"] == "2026-09-13T08:06:02Z"
        assert "excludes biogenic CO2" in ref["accounting_basis"]
        assert "allocates CHP emissions to electricity" in ref["accounting_basis"]
        assert "not all physical stack CO2" in ref["accounting_basis"]
        assert "grouped by primary fuel" in ref["aggregation"]
        assert "combustion net generation" in ref["aggregation"]
        assert "egrid2023_technical_guide.pdf#page=25" in ref["methodology_ref"]
        assert all(section in ref["methodology_ref"] for section in ("3.1.2.1", "3.1.2.2", "3.1.3.3"))
    for fuel in ("Wind", "Solar", "Hydro", "Nuclear"):
        assert result[fuel]["value"] == 0
        assert "egrid2023_technical_guide.pdf#page=21" in result[fuel]["ref"]
        assert "excludes lifecycle" in result[fuel]["ref"]
    assert set(result) == {"Coal", "Natural Gas", "Oil", "Wind", "Solar", "Hydro", "Nuclear", "Waste Heat"}
    assert json.dumps(result, allow_nan=False) == json.dumps(load_egrid_swpp_factors(path), allow_nan=False)


def test_egrid_waste_heat_zero_is_a_sourced_accounting_convention_not_a_plant_measurement(tmp_path, monkeypatch):
    path = epa_cache(tmp_path, monkeypatch)
    original_cache = path.read_bytes()
    factors = load_egrid_swpp_factors(path)
    waste_heat = factors["Waste Heat"]
    assert set(waste_heat) == {"value", "source_type", "ref", "unit", "boundary"}
    assert waste_heat["value"] == 0.0 and waste_heat["source_type"] == "data"
    assert waste_heat["unit"] == FACTOR_UNIT == "kgCO2/MWh"
    assert waste_heat["boundary"] == BOUNDARY == "direct_operational_co2"
    ref = waste_heat["ref"]
    assert "https://www.epa.gov/system/files/documents/2025-01/egrid2023_technical_guide.pdf#page=21" in ref
    assert "12164c665217f1b4d00ac6a8115a3806710dab79993d02959cdf7f7f66abd841" in ref
    assert "https://portal.spp.org/api/pageConfig/by-slug/generation-mix-historical" in ref
    assert "0160d0a41a70029190b79c3856cd427eeb59381a0d1af1f083f8e40011d435db" in ref
    assert all(qualification in ref for qualification in ("eGRID accounting convention", "host-process", "upstream", "lifecycle"))
    assert "WH" not in factors and "waste heat" not in factors
    assert path.read_bytes() == original_cache


def test_egrid_waste_heat_stays_in_full_generation_denominator_and_assumption_lineage(tmp_path, monkeypatch):
    path = epa_cache(tmp_path, monkeypatch, content=epa_workbook(rows=[[2023, "SWPP", 1000, 500, 1200]]))
    factors = load_egrid_swpp_factors(path)
    frame = mix([(HOURS[0], "Coal", 30), (HOURS[0], "Waste Heat", 70)])
    original_frame, original_factors, original_policy = deepcopy((frame, factors, POLICY))
    result = fuel_mix_intensity(frame, factors, expected_fuels=["Coal", "Waste Heat"], application_source=POLICY)[0]
    assert result["status"] == "available"
    assert result["missing_fuels"] == result["missing_generation_fuels"] == []
    assert result["intensity_kg_co2_per_mwh"]["value"] == 300.
    assert result["generation_mwh"]["value"] == result["known_generation_mwh"]["value"] == 100.
    assert result["factor_coverage_fraction"]["value"] == 1.
    assert result["intensity_kg_co2_per_mwh"]["source_type"] == "assumption"
    evidence = json.loads(result["intensity_kg_co2_per_mwh"]["ref"])["inputs"]
    assert {**factors["Coal"], "quantity": "factor_kg_co2_per_mwh", "fuel": "Coal"} in evidence
    assert {**factors["Waste Heat"], "quantity": "factor_kg_co2_per_mwh", "fuel": "Waste Heat"} in evidence
    assert all(any(item["ref"] == ref for item in evidence) for ref in frame.ref)
    assert any(POLICY["ref"] in item["ref"] for item in evidence)
    pd.testing.assert_frame_equal(frame, original_frame, check_exact=True)
    assert factors == original_factors and POLICY == original_policy


def test_egrid_waste_heat_does_not_supply_factors_for_other_positive_unmapped_fuels(tmp_path, monkeypatch):
    factors = load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch))
    frame = mix([(HOURS[0], "Waste Heat", 40), (HOURS[0], "Waste Disposal Services", 10),
                 (HOURS[0], "Other", 20), (HOURS[0], "Diesel Fuel Oil", 30)])
    original_frame, original_factors, original_policy = deepcopy((frame, factors, POLICY))
    expected = ["Waste Heat", "Waste Disposal Services", "Other", "Diesel Fuel Oil"]
    result = fuel_mix_intensity(frame, factors, expected_fuels=expected, application_source=POLICY)[0]
    assert "Oil" in factors
    assert not {"Waste Disposal Services", "Other", "Diesel Fuel Oil"} & set(factors)
    assert result["status"] == "missing_factors"
    assert result["missing_fuels"] == ["Diesel Fuel Oil", "Other", "Waste Disposal Services"]
    assert result["missing_generation_fuels"] == []
    assert result["intensity_kg_co2_per_mwh"]["value"] is None
    assert result["generation_mwh"]["value"] == 100.
    assert result["known_generation_mwh"]["value"] == 40.
    assert result["factor_coverage_fraction"]["value"] == .4
    assert result["intensity_kg_co2_per_mwh"]["source_type"] == "assumption"
    evidence = json.loads(result["intensity_kg_co2_per_mwh"]["ref"])["inputs"]
    assert {**factors["Waste Heat"], "quantity": "factor_kg_co2_per_mwh", "fuel": "Waste Heat"} in evidence
    assert all(any(item["ref"] == ref for item in evidence) for ref in frame.ref)
    pd.testing.assert_frame_equal(frame, original_frame, check_exact=True)
    assert factors == original_factors and POLICY == original_policy


def test_egrid_zero_waste_heat_factor_does_not_make_unknown_generation_evaluable(tmp_path, monkeypatch):
    factors = load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch))
    frame = mix([(HOURS[0], "Coal", 30), (HOURS[0], "Waste Heat", None)]).astype({"generation_mwh": object})
    frame["generation_status"] = ["complete", "incomplete_observations"]
    frame.loc[1, "generation_mwh"] = None
    original_frame, original_factors, original_policy = deepcopy((frame, factors, POLICY))
    result = fuel_mix_intensity(frame, factors, expected_fuels=["Coal", "Waste Heat"], application_source=POLICY)[0]
    assert factors["Waste Heat"]["value"] == 0.
    assert result["status"] == "missing_generation"
    assert result["missing_generation_fuels"] == ["Waste Heat"]
    assert result["missing_fuels"] == []
    for name in ("intensity_kg_co2_per_mwh", "generation_mwh", "factor_coverage_fraction"):
        assert result[name]["value"] is None and result[name]["source_type"] == "assumption"
    assert result["reported_generation_mwh"]["value"] == result["known_generation_mwh"]["value"] == 30.
    evidence = json.loads(result["intensity_kg_co2_per_mwh"]["ref"])["inputs"]
    assert {**factors["Waste Heat"], "quantity": "factor_kg_co2_per_mwh", "fuel": "Waste Heat"} in evidence
    assert any(item["ref"] == frame.loc[1, "ref"] and item["value"] is None for item in evidence)
    pd.testing.assert_frame_equal(frame, original_frame, check_exact=True)
    assert factors == original_factors and POLICY == original_policy


def test_egrid_accounting_qualification_survives_hourly_intensity_provenance(tmp_path, monkeypatch):
    factors = load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch))
    frame = mix([(HOURS[0], "Coal", 30), (HOURS[0], "Wind", 70)])
    result = fuel_mix_intensity(frame, factors, expected_fuels=["Coal", "Wind"], application_source=POLICY)[0]
    assert result["boundary"] == BOUNDARY == "direct_operational_co2"
    ref = result["intensity_kg_co2_per_mwh"]["ref"]
    assert "excludes biogenic CO2" in ref and "allocates CHP emissions to electricity" in ref
    assert "3.1.3.3" in ref
    assert result["intensity_kg_co2_per_mwh"]["source_type"] == "assumption"


def test_egrid_factors_are_parsed_not_hardcoded_and_leave_other_fuels_unknown(tmp_path, monkeypatch):
    path = epa_cache(tmp_path, monkeypatch, content=epa_workbook(rows=[[2023, "SWPP", 1000, 500, 1200]]))
    result = load_egrid_swpp_factors(path)
    assert result["Coal"]["value"] == 1000 and result["Natural Gas"]["value"] == 500
    frame = mix([(HOURS[0], "Coal", 30), (HOURS[0], "Wind", 50),
                 (HOURS[0], "Diesel Fuel Oil", 10), (HOURS[0], "Other", 10)])
    calculated = fuel_mix_intensity(frame, result, expected_fuels=["Coal", "Wind", "Diesel Fuel Oil", "Other"], application_source=POLICY)[0]
    assert calculated["intensity_kg_co2_per_mwh"]["value"] is None
    assert calculated["missing_fuels"] == ["Diesel Fuel Oil", "Other"]
    assert calculated["generation_mwh"]["value"] == 100


@pytest.mark.parametrize("change", [
    {"source_type": "assumption"}, {"ref": "https://www.epa.gov/egrid"},
    {"ref": "https://www.epa.gov/system/files/documents/2025-06/egrid2023_data_rev2.xlsx"},
    {"sha256": "0" * 64}, {"retrieved_utc": "2026-09-13T08:06:02"},
    {"retrieved_utc": "2026-09-13T09:06:02+01:00"}, {"retrieved_utc": None},
])
def test_egrid_rejects_wrong_source_digest_or_utc_retrieval_metadata(tmp_path, monkeypatch, change):
    with pytest.raises(ValueError):
        load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch, origin_change=change))


def test_egrid_rejects_modified_bytes_even_when_metadata_digest_is_rewritten(tmp_path, monkeypatch):
    path = epa_cache(tmp_path, monkeypatch)
    frame = pd.read_parquet(path)
    altered = epa_workbook(rows=[[2023, "SWPP", 1, 1, 1]])
    origin = json.loads(frame.iloc[0]["source_json"])
    origin["sha256"] = hashlib.sha256(altered).hexdigest()
    pd.DataFrame([{"content": altered, "source_json": json.dumps(origin)}]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="verified release"):
        load_egrid_swpp_factors(path)


@pytest.mark.parametrize("rows", [
    [], [EPA_ROW, EPA_ROW], [[2022, *EPA_ROW[1:]]], [[2023, "OTHER", *EPA_ROW[2:]]],
])
def test_egrid_requires_one_swpp_2023_row(tmp_path, monkeypatch, rows):
    with pytest.raises(ValueError, match="display labels|SWPP/YEAR"):
        load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch, content=epa_workbook(rows=rows)))


@pytest.mark.parametrize("label", [
    "BA annual CO2 coal output emission rate (kg/GJ)",
    "BA annual CO2 coal output emission rate (lb/MWh)",
    "BA annual CO2e coal output emission rate (kg/MWh)", None,
])
def test_egrid_rejects_wrong_units_or_emissions_boundary_in_display_labels(tmp_path, monkeypatch, label):
    labels = list(EPA_LABELS)
    labels[2] = label
    with pytest.raises(ValueError, match="kg/MWh"):
        load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch, content=epa_workbook(labels=labels)))


@pytest.mark.parametrize("codes", [
    ["YEAR", "BACODE", "BACCO2RT2", "BAGCO2RT", "BAOCO2RT"],
    [*EPA_CODES, "BACCO2RT"],
])
def test_egrid_requires_unique_exact_field_codes(tmp_path, monkeypatch, codes):
    with pytest.raises(ValueError, match="unambiguous BACCO2RT"):
        load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch, content=epa_workbook(codes=codes)))


@pytest.mark.parametrize("value", [-1, None, True, "1024.3", "=1+1"])
def test_egrid_rejects_non_numeric_or_uncomputed_factor_cells(tmp_path, monkeypatch, value):
    row = list(EPA_ROW)
    row[2] = value
    with pytest.raises(ValueError, match="EPA SWPP BACCO2RT"):
        load_egrid_swpp_factors(epa_cache(tmp_path, monkeypatch, content=epa_workbook(rows=[row])))


def test_egrid_rejects_missing_sheet_duplicate_metadata_and_ambiguous_cache_rows(tmp_path, monkeypatch):
    path = epa_cache(tmp_path, monkeypatch, content=epa_workbook(sheet_name="BA22"))
    with pytest.raises(ValueError, match="BA23"):
        load_egrid_swpp_factors(path)
    path = epa_cache(tmp_path, monkeypatch)
    frame = pd.read_parquet(path)
    frame.loc[0, "source_json"] = frame.loc[0, "source_json"].replace('{', '{"source_type":"data",', 1)
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="Duplicate EPA metadata"):
        load_egrid_swpp_factors(path)
    pd.concat([frame, frame]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="one content/source_json"):
        load_egrid_swpp_factors(path)


def test_egrid_loader_never_fetches_or_creates_a_missing_cache(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_args, **_kwargs: pytest.fail("EPA loader must stay offline"))
    path = epa_cache(tmp_path, monkeypatch)
    assert load_egrid_swpp_factors(path)["Oil"]["value"] == EPA_ROW[4]
    missing = tmp_path / "missing-epa.parquet"
    with pytest.raises(FileNotFoundError):
        load_egrid_swpp_factors(missing)
    assert not missing.exists()


ARCHIVE_SOURCE = {"source_type": "data", "ref": "test-only cached full SPP-style fuel archive; digest identifies authored fixture"}
TIMING_SOURCE = {"source_type": "assumption", "ref": "test-only declared left-closed UTC observation-time convention, not verified interval start/end"}


def archive(periods=12, *, start=HOURS[0]):
    frame = pd.DataFrame({"GMT MKT Interval": pd.date_range(start, periods=periods, freq="5min").astype(str)})
    for number, fuel in enumerate(carbon.SPP_ARCHIVE_FUELS, start=1):
        frame[fuel + " Market"] = float(number)
        frame[("Gas" if fuel == "Natural Gas" else fuel) + " Self"] = float(number)
    frame["Load"] = 10000.0  # Demand must never enter the generation denominator.
    return frame


def normalize_archive(raw, **kwargs):
    return normalize_spp_generation_archive(raw, generation_source=ARCHIVE_SOURCE,
                                            timing_source=TIMING_SOURCE, **kwargs)


def archive_intensity(frame, factors=None):
    return fuel_mix_intensity(frame, {} if factors is None else factors,
                              expected_fuels=frame.attrs["expected_fuels"], application_source=POLICY)


def test_archive_preserves_full_fuel_denominator_and_only_maps_the_documented_gas_alias():
    result = normalize_archive(archive())
    assert result.attrs["expected_fuels"] == sorted(carbon.SPP_ARCHIVE_FUELS)
    assert len(result) == 10 and set(result.unit) == {"MWh"}
    assert set(result.generation_status) == {"complete"}
    assert set(result.source_type) == {"assumption"}
    assert result.set_index("fuel").loc["Natural Gas", "generation_mwh"] == 8
    assert set(result.fuel) >= {"Diesel Fuel Oil", "Waste Disposal Services", "Waste Heat", "Other"}
    assert "Load" not in set(result.fuel) and "Oil" not in set(result.fuel)
    calculated = archive_intensity(result, {fuel: factor(0 if fuel in {"Wind", "Solar"} else 1000)
                                          for fuel in ("Coal", "Natural Gas", "Wind", "Solar", "Hydro", "Nuclear")})[0]
    assert calculated["generation_mwh"]["value"] == 110
    assert calculated["reported_generation_mwh"]["value"] == 110
    assert calculated["missing_fuels"] == ["Diesel Fuel Oil", "Other", "Waste Disposal Services", "Waste Heat"]
    assert calculated["intensity_kg_co2_per_mwh"]["value"] is None
    natural_gas_ref = json.loads(result.set_index("fuel").loc["Natural Gas", "ref"])
    assert natural_gas_ref["components"] == ["Natural Gas Market", "Gas Self"]
    assert natural_gas_ref["complete_samples"] == 12
    assert natural_gas_ref["input_unit"] == "MW" and natural_gas_ref["output_unit"] == "MWh"
    assert ARCHIVE_SOURCE in natural_gas_ref["inputs"] and TIMING_SOURCE in natural_gas_ref["inputs"]


def test_archive_keeps_additional_fuel_pairs_instead_of_silently_dropping_them():
    raw = archive()
    raw["New Fuel Market"], raw["New Fuel Self"] = 6.0, 4.0
    result = normalize_archive(raw)
    assert "New Fuel" in result.attrs["expected_fuels"]
    assert result.set_index("fuel").loc["New Fuel", "generation_mwh"] == 10
    calculated = archive_intensity(result)[0]
    assert calculated["generation_mwh"]["value"] == 120
    assert "New Fuel" in calculated["missing_fuels"]


def test_archive_missing_component_stays_unknown_even_if_every_other_fuel_is_complete():
    result = normalize_archive(archive().drop(columns="Coal Self"))
    coal = result.set_index("fuel").loc["Coal"]
    assert coal["generation_mwh"] is None and coal["generation_status"] == "missing_component"
    calculated = archive_intensity(result)[0]
    assert calculated["generation_mwh"]["value"] is None
    assert calculated["missing_generation_fuels"] == ["Coal"]
    assert calculated["reported_generation_mwh"]["value"] == 108


def test_archive_wind_only_input_never_becomes_a_complete_zero_intensity_grid():
    raw = archive()[["GMT MKT Interval", "Wind Market", "Wind Self"]]
    result = normalize_archive(raw)
    assert len(result) == 10
    calculated = archive_intensity(result, {"Wind": factor(0)})[0]
    assert calculated["generation_mwh"]["value"] is None
    assert calculated["intensity_kg_co2_per_mwh"]["value"] is None
    assert calculated["reported_generation_mwh"]["value"] == 16


def test_archive_requires_twelve_complete_distinct_samples_for_each_fuel_independently():
    raw = archive()
    raw.loc[0, "Coal Self"] = float("nan")
    result = normalize_archive(raw)
    assert result.set_index("fuel").loc["Coal", "generation_mwh"] is None
    assert result.set_index("fuel").loc["Wind", "generation_mwh"] == 16
    coal_ref = json.loads(result.set_index("fuel").loc["Coal", "ref"])
    assert coal_ref["complete_samples"] == 11
    assert coal_ref["component_samples"] == {"Coal Market": 12, "Coal Self": 11}
    missing_sample = normalize_archive(archive().iloc[:11])
    assert missing_sample.generation_mwh.isna().all()
    calculated = archive_intensity(missing_sample)[0]
    assert calculated["generation_mwh"]["value"] is None
    assert calculated["reported_generation_mwh"]["value"] is None
    assert calculated["known_generation_mwh"]["value"] is None


def test_archive_all_missing_observation_and_missing_whole_hour_are_not_zeros():
    raw = archive(periods=36)
    raw = raw.drop(index=range(12, 24))
    raw.loc[0, raw.columns != "GMT MKT Interval"] = float("nan")
    result = normalize_archive(raw)
    assert len(result) == 30
    hours = {item["timestamp_utc"]: item for item in archive_intensity(result)}
    for time in HOURS[:2]:
        assert hours[time]["generation_mwh"]["value"] is None
        assert hours[time]["reported_generation_mwh"]["value"] is None
    assert hours[HOURS[2]]["generation_mwh"]["value"] == 110


def test_archive_keeps_signed_components_but_does_not_clamp_negative_fuel_net():
    raw = archive()
    raw["Hydro Market"], raw["Hydro Self"] = -10.0, 20.0
    raw.loc[0, "Solar Market"], raw.loc[0, "Solar Self"] = 0.0, -0.1
    result = normalize_archive(raw).set_index("fuel")
    assert result.loc["Hydro", "generation_mwh"] == 10
    hydro = json.loads(result.loc["Hydro", "ref"])
    assert hydro["negative_component_samples"] == {"Hydro Market": 12, "Hydro Self": 0}
    assert result.loc["Solar", "generation_mwh"] is None
    assert result.loc["Solar", "generation_status"] == "negative_net_generation"
    solar = json.loads(result.loc["Solar", "ref"])
    assert solar["negative_net_samples"] == 1 and solar["complete_samples"] == 12
    assert solar["negative_component_samples"]["Solar Self"] == 1


def test_archive_deduplicates_exact_chunk_overlap_but_rejects_conflicting_revisions():
    raw = archive()
    expected = normalize_archive(raw)
    repeated = pd.concat([raw, raw.iloc[[0, 1, 1]]], ignore_index=True)
    pd.testing.assert_frame_equal(normalize_archive(repeated), expected)
    repeated.loc[len(repeated) - 1, "Coal Market"] += 1
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        normalize_archive(repeated)
    repeated = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
    repeated.loc[len(repeated) - 1, "Load"] += 1
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        normalize_archive(repeated)


def test_archive_row_and_column_permutations_are_byte_identical_and_inputs_are_unchanged():
    raw = archive(periods=24)
    raw.columns = [" " + name + " " for name in raw.columns]
    original = raw.copy(deep=True)
    expected = normalize_archive(raw)
    shuffled = raw.sample(frac=1, random_state=17)[list(reversed(raw.columns))]
    actual = normalize_archive(shuffled)
    assert json.dumps(actual.to_dict("records"), allow_nan=False) == json.dumps(expected.to_dict("records"), allow_nan=False)
    assert actual.attrs == expected.attrs
    pd.testing.assert_frame_equal(raw, original)


def test_archive_binning_does_not_shift_observation_timestamps_or_invent_utc_year_coverage():
    result = normalize_archive(archive(periods=12, start="2024-12-31T23:05:00Z"))
    assert set(result.timestamp_utc) == {"2024-12-31T23:00:00+00:00", "2025-01-01T00:00:00+00:00"}
    assert result.generation_mwh.isna().all()
    assert result.attrs["timing_convention"] == "observation_time_left_closed_utc_hour"
    assert all("no interval-start/end claim" in ref for ref in result.ref)


@pytest.mark.parametrize("value", ["2024-01-01T00:01:00Z", "2024-01-01T00:00:01Z", "2024-01-01T00:00:00", None])
def test_archive_rejects_malformed_cadence_and_ambiguous_timezones(value):
    raw = archive()
    raw.loc[0, "GMT MKT Interval"] = value
    with pytest.raises(ValueError, match="cadence|timezone"):
        normalize_archive(raw)


@pytest.mark.parametrize("value", [True, float("inf"), "not a number"])
def test_archive_rejects_malformed_component_values(value):
    raw = archive().astype({"Coal Market": object})
    raw.loc[0, "Coal Market"] = value
    with pytest.raises(ValueError):
        normalize_archive(raw)


@pytest.mark.parametrize("quantity", [
    pytest.param(pd.Series([60 + 5j] * 12, dtype=complex), id="complex-dtype"),
    pytest.param(pd.Series([60.] * 11 + [60 + 5j], dtype=object), id="mixed-complex"),
    pytest.param(pd.Series([np.datetime64(60, 'ns')] * 12), id="datetime-dtype"),
    pytest.param(pd.Series([np.timedelta64(60, 'ns')] * 12), id="timedelta-dtype"),
    pytest.param(pd.Series([pd.Timestamp(60, tz='UTC')] * 12), id="timezone-datetime-dtype"),
    pytest.param(pd.Series([60.] * 11 + [pd.Timestamp(60)], dtype=object), id="mixed-timestamp"),
    pytest.param(pd.Series([60.] * 11 + [np.datetime64(60, 'ns')], dtype=object), id="mixed-numpy-datetime"),
    pytest.param(pd.Series([60.] * 11 + [np.timedelta64(60, 'ns')], dtype=object), id="mixed-numpy-timedelta"),
    pytest.param(pd.Series([pd.NaT] * 12, dtype='datetime64[ns]'), id="temporal-null-dtype"),
    pytest.param(pd.Series([60.] * 11 + [pd.NaT], dtype=object), id="mixed-temporal-null"),
])
def test_archive_rejects_temporal_or_complex_power_instead_of_inventing_real_mwh(quantity):
    raw = archive()
    raw["Wind Market"] = quantity
    original = raw.copy(deep=True)
    with pytest.raises(ValueError, match="real numeric MW"):
        normalize_archive(raw)
    pd.testing.assert_frame_equal(raw, original, check_exact=True)


@pytest.mark.parametrize("dtype,known", [("Float64", 2.5), ("Int64", 2), (object, "2.5")])
def test_archive_numeric_nullable_and_text_inputs_retain_energy_missingness_and_sources(dtype, known):
    raw = archive(periods=24)
    raw["Wind Market"] = pd.Series([known] * 23 + [pd.NA], dtype=dtype)
    expected = archive(periods=24)
    expected["Wind Market"] = [float(known)] * 23 + [float('nan')]
    actual = normalize_archive(raw)
    pd.testing.assert_frame_equal(actual, normalize_archive(expected), check_exact=True)
    wind = actual[actual.fuel.eq("Wind")].reset_index(drop=True)
    assert wind.loc[0, "generation_mwh"] == float(known) + 8
    assert wind.loc[1, "generation_mwh"] is None
    assert wind.loc[1, "generation_status"] == "incomplete_observations"
    assert json.loads(wind.loc[1, "ref"])["complete_samples"] == 11


def test_archive_rejects_ambiguous_columns_and_aliases_instead_of_double_counting():
    raw = archive()
    raw["Natural Gas Self"] = raw["Gas Self"]
    with pytest.raises(ValueError, match="Ambiguous"):
        normalize_archive(raw)
    raw = archive()
    raw[" Coal Market"] = raw["Coal Market"]
    with pytest.raises(ValueError, match="unique columns"):
        normalize_archive(raw)
    raw = archive()
    raw["Unexplained Total"] = 100
    with pytest.raises(ValueError, match="Unknown archive column"):
        normalize_archive(raw)


@pytest.mark.parametrize("kind", ["data", "model"])
def test_archive_binning_cannot_be_promoted_from_assumption_to_observation(kind):
    with pytest.raises(ValueError, match="timing assumption"):
        normalize_spp_generation_archive(archive(), generation_source=ARCHIVE_SOURCE,
                                        timing_source={"source_type": kind, "ref": TIMING_SOURCE["ref"]})


def test_marked_unknown_generation_survives_parquet_null_roundtrip_without_weakening_nan_validation(tmp_path):
    original = normalize_archive(archive().iloc[:11])
    path = tmp_path / "unknown-generation.parquet"
    original.to_parquet(path, index=False)
    restored = pd.read_parquet(path)
    assert archive_intensity(original) == archive_intensity(restored)
    restored.loc[0, "generation_status"] = "complete"
    with pytest.raises(ValueError, match="finite number"):
        archive_intensity(restored)
    restored = original.copy()
    restored.loc[0, "generation_mwh"] = 1.0
    with pytest.raises(ValueError, match="requires a missing value"):
        archive_intensity(restored)


def test_generation_unit_is_checked_if_supplied():
    frame = normalize_archive(archive())
    frame.loc[0, "unit"] = "MW"
    with pytest.raises(ValueError, match="energy in MWh"):
        archive_intensity(frame)


def test_complete_generation_total_traces_the_declared_universe_that_controls_missingness():
    frame = mix([(HOURS[0], "wind", 70.)])
    factors = {"wind": factor(0.)}
    complete = fuel_mix_intensity(frame, factors, expected_fuels=["wind"], application_source=POLICY)[0]
    incomplete = fuel_mix_intensity(frame, factors, expected_fuels=["wind", "coal"], application_source=POLICY)[0]
    assert complete["generation_mwh"]["value"] == 70.
    assert incomplete["generation_mwh"]["value"] is None
    assert complete["generation_mwh"]["ref"] != incomplete["generation_mwh"]["ref"]
    assert "coal" in incomplete["generation_mwh"]["ref"]
    assert POLICY["ref"] in incomplete["generation_mwh"]["ref"]
    assert incomplete["generation_mwh"]["source_type"] == "assumption"
    # The sum of supplied observations does not depend on declaring completeness.
    assert incomplete["reported_generation_mwh"]["value"] == 70.
    assert incomplete["reported_generation_mwh"]["source_type"] == "data"


@pytest.mark.parametrize("parent,child", [("data", "model"), ("data", "assumption"), ("model", "assumption")])
def test_recognized_nested_provenance_cannot_upgrade_a_descendant(parent, child):
    leaf = datum(25., child, "authored audit descendant")
    original = datum(25., parent, json.dumps({"method": "authored derived quantity", "inputs": [leaf]}))
    with pytest.raises(ValueError, match="upgrade"):
        wind_carbon(original, selection_source={"source_type": "data", "ref": "authored observed selection"})


def test_nested_source_guard_applies_to_all_accounting_input_positions():
    leaf = datum(10., "assumption", "authored scenario descendant")
    nested = json.dumps({"method": "authored derived input", "inputs": [leaf]})
    authored = datum(10., "data", nested)
    frame = mix()
    frame.loc[0, ["source_type", "ref"]] = ["data", nested]
    bad_factor = {**factor(1000.), "ref": nested}
    observations = intensity()
    observations[0]["intensity_kg_co2_per_mwh"] = authored
    cap = limits()
    cap[HOURS[0]]["removable_mwh"] = authored
    schedule = moves()
    schedule[0]["mwh"] = authored
    for call in (
        lambda: calculate(frame),
        lambda: calculate(mix(), {"coal": bad_factor, "wind": factor(0.)}),
        lambda: fuel_mix_intensity(mix(), {}, expected_fuels=["coal", "wind"], application_source=authored),
        lambda: shift(records=observations),
        lambda: shift(cap=cap),
        lambda: shift(schedule=schedule),
        lambda: shift_carbon(intensity(), moves(), selection_source=authored, hourly_limits=limits()),
        lambda: wind_carbon(authored, selection_source=POLICY),
        lambda: wind_carbon(datum(10.), selection_source=authored),
        lambda: wind_carbon(datum(10.), selection_source=POLICY, factor={**factor(0.), "ref": nested}),
        lambda: normalize_spp_generation_archive(archive(), generation_source=authored, timing_source=TIMING_SOURCE),
    ):
        with pytest.raises(ValueError, match="upgrade"):
            call()


def test_nested_source_guard_reaches_multiple_levels_and_preserves_valid_assumptions():
    leaf = datum(25., "assumption", "authored original capacity assumption")
    inner = datum(25., "data", json.dumps({"method": "authored inner", "inputs": [leaf]}))
    outer = datum(25., "data", json.dumps({"method": "authored outer", "inputs": [inner]}))
    with pytest.raises(ValueError, match="upgrade"):
        wind_carbon(outer, selection_source=POLICY)
    inner["source_type"] = "assumption"
    outer = datum(25., "assumption", json.dumps({"method": "authored outer", "inputs": [inner]}))
    original = deepcopy(outer)
    result = wind_carbon(outer, selection_source={"source_type": "data", "ref": "authored observed selection"})
    assert result["wind_operational_co2_kg"]["source_type"] == "assumption"
    assert outer in json.loads(result["wind_operational_co2_kg"]["ref"])["inputs"]
    assert outer == original


@pytest.mark.parametrize("opaque", [
    '{"different_format":{"source_type":"assumption","ref":"authored citation"}}',
    '{"method":"other schema","inputs":[1,2]}',
    '{"method":"other schema","inputs":[],"extra":"not the derived format"}',
    '{not valid JSON; original source citation}',
    '{"different_format":{"source_type":"assumption","source_type":"data"}}',
    '{"different_format":{"value":NaN}}',
    '{"method":"other schema","inputs":[],"extra":1e400}',
])
def test_other_json_citation_formats_stay_opaque_and_are_preserved(opaque):
    origin = datum(25., "data", opaque)
    result = wind_carbon(origin, selection_source={"source_type": "data", "ref": "authored observed selection"})
    assert result["wind_operational_co2_kg"]["source_type"] == "data"
    assert origin in json.loads(result["wind_operational_co2_kg"]["ref"])["inputs"]


def test_legitimate_data_calculations_keep_data_provenance_with_verified_scope():
    observed_scope = {"source_type": "data", "ref": "authored verified observation scope"}
    result = fuel_mix_intensity(mix(), {"coal": factor(1000.), "wind": factor(0.)},
                                expected_fuels=["coal", "wind"], application_source=observed_scope)[0]
    assert result["generation_mwh"]["source_type"] == "data"
    assert result["reported_generation_mwh"]["source_type"] == "data"
    assert result["intensity_kg_co2_per_mwh"]["source_type"] == "model"
    nested = datum(25., "data", json.dumps({"method": "authored derived metered energy", "inputs": [datum(25.)]}))
    assert wind_carbon(nested, selection_source=observed_scope)["wind_operational_co2_kg"]["source_type"] == "data"


@pytest.mark.parametrize("kind", ["data", "assumption"])
@pytest.mark.parametrize("ref", [
    '{"method":"derived","inputs":[{"source_type":"assumption","source_type":"data","ref":"authored source"}]}',
    '{"method":"derived","inputs":[{"source_type":"data","ref":"first","ref":"second"}]}',
    '{"method":"derived","inputs":[{"value":10,"value":20,"source_type":"data","ref":"authored source"}]}',
    '{"method":"first","method":"second","inputs":[]}',
    '{"method":"derived","inputs":[{"source_type":"assumption","ref":"authored source"}],"inputs":[]}',
])
def test_recognized_provenance_rejects_duplicate_keys_instead_of_using_the_last_value(kind, ref):
    with pytest.raises(ValueError, match="Duplicate JSON provenance key"):
        wind_carbon(datum(25., kind, ref), selection_source=POLICY)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
@pytest.mark.parametrize("kind", ["data", "assumption"])
def test_recognized_provenance_rejects_nonfinite_nested_values_and_exponent_overflow(number, kind):
    ref = ('{"method":"derived","inputs":[{"value":' + number
           + ',"source_type":"data","ref":"authored source"}]}')
    with pytest.raises(ValueError, match="Nonfinite JSON provenance number"):
        wind_carbon(datum(25., kind, ref), selection_source=POLICY)


def test_duplicate_key_guard_checks_json_references_inside_nested_source_strings():
    inner_ref = '{"method":"derived","inputs":[{"source_type":"assumption","source_type":"data","ref":"authored source"}]}'
    outer_ref = json.dumps({"method": "outer derived quantity", "inputs": [datum(25., "data", inner_ref)]})
    with pytest.raises(ValueError, match="Duplicate JSON provenance key"):
        wind_carbon(datum(25., "data", outer_ref), selection_source=POLICY)


def test_strict_recognized_reference_preserves_finite_numbers_and_original_text():
    ref = '{ "method" : "authored measured quantity", "inputs" : [{"value":1e-200,"source_type":"data","ref":"authored source"}] }'
    origin = datum(25., "data", ref)
    result = wind_carbon(origin, selection_source={"source_type": "data", "ref": "authored measured selection"})
    assert result["wind_energy_absorbed_mwh"]["value"] == 25.
    assert result["wind_operational_co2_kg"]["value"] == 0.
    assert result["wind_operational_co2_kg"]["source_type"] == "data"
    assert origin in json.loads(result["wind_operational_co2_kg"]["ref"])["inputs"]
