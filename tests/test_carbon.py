"""Software fixtures exercise accounting; these are not SPP emission estimates."""
from copy import deepcopy
import hashlib
from io import BytesIO
from itertools import permutations
import json
import math

import pandas as pd
import pytest
from openpyxl import Workbook

import pipeline.carbon as carbon
from pipeline.carbon import (
    BOUNDARY, FACTOR_UNIT, WIND_OPERATIONAL_CO2_FACTOR,
    fuel_mix_intensity, load_egrid_swpp_factors, shift_carbon, wind_carbon,
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
        assert result[fuel]["value"] == value
        assert result[fuel]["source_type"] == "data"
        assert result[fuel]["unit"] == FACTOR_UNIT and result[fuel]["boundary"] == BOUNDARY
        ref = json.loads(result[fuel]["ref"])
        assert ref["ref"] == carbon.EGRID_METRIC_URL
        assert (ref["sheet"], ref["YEAR"], ref["BACODE"], ref["field"], ref["cell"]) == ("BA23", 2023, "SWPP", code, cell)
        assert ref["sha256"] == carbon.EGRID_METRIC_SHA256
        assert ref["retrieved_utc"] == "2026-09-13T08:06:02Z"
    for fuel in ("Wind", "Solar", "Hydro", "Nuclear"):
        assert result[fuel]["value"] == 0
        assert "egrid2023_technical_guide.pdf#page=21" in result[fuel]["ref"]
        assert "excludes lifecycle" in result[fuel]["ref"]
    assert set(result) == {"Coal", "Natural Gas", "Oil", "Wind", "Solar", "Hydro", "Nuclear"}
    assert json.dumps(result, allow_nan=False) == json.dumps(load_egrid_swpp_factors(path), allow_nan=False)


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
