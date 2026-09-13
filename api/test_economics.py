"""File-backed input changes and transparent economics edge cases."""

import json
from pathlib import Path

import pytest

from .economics import (
    ArithmeticRangeError,
    AssumptionsError,
    build_economics,
    load_assumptions,
)
from .schemas import EstimateRequest, Source


REQUEST = {
    "location_id": "SPP_SPS_HUB",
    "load_mw": 100,
    "term_years": 7,
    "flexibility_split": 0.6,
    "site_exposure": 0.4,
}
SUMMARY = {"p50": 80, "p90": 140, "p99": 220}
PLACEHOLDER_SOURCE = Source(source_type="assumption", ref="mock://exposure; placeholder, pipeline not wired yet")
REAL_SOURCE = Source(source_type="model", ref="pipeline/simulate.py model_version=verified_test_model")


def document(payload: dict) -> str:
    return "# Fixture assumptions\n\n<!-- headroom:economics-assumptions:v1 -->\n```json\n" + json.dumps(payload) + "\n```\n"


@pytest.fixture
def payload() -> dict:
    return load_assumptions().model_dump()


@pytest.fixture
def assumptions_path(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "ASSUMPTIONS.md"
    path.write_text(document(payload), encoding="utf-8")
    return path


def calculate(path: Path, source: Source = PLACEHOLDER_SOURCE, **inputs):
    return build_economics(EstimateRequest(**{**REQUEST, **inputs}), SUMMARY, source, assumptions_path=path)


def test_scaffold_is_explicitly_unsourced():
    result = load_assumptions()
    assert result.status == "placeholder"
    assert result.gpu_rental_price_usd_per_hour.value == 2
    assert result.industrial_electricity_price_usd_per_mwh.value == 50
    assert result.gpus_per_mw.value == 1000
    for key, item in result.model_dump().items():
        if key in {"schema_version", "status"}:
            continue
        assert item["source_type"] == "assumption"
        assert "placeholder" in item["ref"]
        assert item["source_url"] is None
        assert item["retrieved_on"] is None


def test_default_formula_and_provenance(assumptions_path):
    result = calculate(assumptions_path)
    assert result.gpus_per_mw == 1000
    assert result.lost_gpu_hours_per_year.model_dump() == {"p50": 4800000, "p90": 8400000, "p99": 13200000}
    assert result.annual_cost_usd.model_dump() == {"p50": 9600000, "p90": 16800000, "p99": 26400000}
    assert result.value_of_early_connection_usd == 150000000
    assert result.breakeven_exposure_hours_per_year == pytest.approx(150000000 / (7 * 60 * 1000 * 2))
    assert result.decision == "worth_it"
    assert result.source.source_type == "assumption"
    assert result.source.ref.startswith("mock://")
    assert "placeholder, pipeline not wired yet" in result.source.ref
    assert "gpu_rental_price_usd_per_hour=2" in result.source.ref
    assert "docs/ASSUMPTIONS.md#gpu-rental-price" in result.source.ref


def test_same_path_edit_changes_next_result_without_restart(assumptions_path, payload):
    first = calculate(assumptions_path)
    payload["gpu_rental_price_usd_per_hour"]["value"] = 4
    assumptions_path.write_text(document(payload), encoding="utf-8")
    second = calculate(assumptions_path)
    assert second.annual_cost_usd.p50 == first.annual_cost_usd.p50 * 2
    assert second.breakeven_exposure_hours_per_year == first.breakeven_exposure_hours_per_year / 2
    assert second.decision == "close_call"
    assert "gpu_rental_price_usd_per_hour=4" in second.source.ref


def test_gpu_density_is_read_from_file(assumptions_path, payload):
    first = calculate(assumptions_path)
    payload["gpus_per_mw"]["value"] = 500
    assumptions_path.write_text(document(payload), encoding="utf-8")
    second = calculate(assumptions_path)
    assert second.gpus_per_mw == 500
    assert second.lost_gpu_hours_per_year.p90 == first.lost_gpu_hours_per_year.p90 / 2
    assert second.annual_cost_usd.p90 == first.annual_cost_usd.p90 / 2


def test_electricity_is_informational_not_double_counted(assumptions_path, payload):
    first = calculate(assumptions_path)
    payload["industrial_electricity_price_usd_per_mwh"]["value"] = 100
    assumptions_path.write_text(document(payload), encoding="utf-8")
    second = calculate(assumptions_path)
    assert second.annual_cost_usd == first.annual_cost_usd
    assert second.value_of_early_connection_usd == first.value_of_early_connection_usd
    assert "industrial_electricity_price_usd_per_mwh=100" in second.source.ref
    assert "electricity=informational, not applied" in second.source.ref


def test_zero_interruptible_load_has_no_finite_crossover(assumptions_path):
    result = calculate(assumptions_path, flexibility_split=0)
    assert result.breakeven_exposure_hours_per_year is None
    assert result.lost_gpu_hours_per_year.p99 == 0
    assert result.annual_cost_usd.p99 == 0
    assert result.decision == "worth_it"


def test_zero_rental_price_has_no_finite_crossover(assumptions_path, payload):
    payload["gpu_rental_price_usd_per_hour"].update(value=0, low=0)
    assumptions_path.write_text(document(payload), encoding="utf-8")
    result = calculate(assumptions_path)
    assert result.breakeven_exposure_hours_per_year is None
    assert result.annual_cost_usd.p99 == 0


def test_earlier_connection_is_capped_by_term(assumptions_path):
    result = calculate(assumptions_path, term_years=1)
    assert result.value_of_early_connection_usd == 100 * 500000


def test_real_exposure_does_not_relabel_placeholder_economics(assumptions_path):
    result = calculate(assumptions_path, REAL_SOURCE)
    assert result.source.source_type == "assumption"
    assert result.source.ref.startswith("mock://economics-placeholder/")
    assert "verified_test_model" in result.source.ref
    assert "pipeline not wired yet" not in result.source.ref


def test_reviewed_inputs_preserve_their_references(assumptions_path, payload):
    payload["status"] = "sourced"
    for key in payload:
        if key in {"status", "schema_version"}:
            continue
        payload[key].update(source_type="data", ref=f"https://example.org/verified-test/{key}",
                            source_url=f"https://example.org/verified-test/{key}", retrieved_on="2026-09-12")
    assumptions_path.write_text(document(payload), encoding="utf-8")
    result = calculate(assumptions_path, REAL_SOURCE)
    assert not result.source.ref.startswith("mock:")
    assert "https://example.org/verified-test/gpu_rental_price_usd_per_hour" in result.source.ref
    assert result.source.source_type == "assumption"
    # Real economics cannot turn a missing model into real exposure.
    assert calculate(assumptions_path).source.ref.startswith("mock:")


@pytest.mark.parametrize("field,value", [
    ("gpu_rental_price_usd_per_hour", -1),
    ("gpu_rental_price_usd_per_hour", True),
    ("gpu_rental_price_usd_per_hour", "2"),
    ("gpus_per_mw", 0),
    ("industrial_electricity_price_usd_per_mwh", float("nan")),
    ("early_margin_usd_per_mw_year", float("inf")),
    ("close_call_fraction", 1),
])
def test_invalid_values_fail_explicitly_without_defaults(assumptions_path, payload, field, value):
    payload[field]["value"] = value
    payload[field]["low"] = 0
    payload[field]["high"] = 10000000
    assumptions_path.write_text(document(payload), encoding="utf-8")
    with pytest.raises(AssumptionsError):
        calculate(assumptions_path)


@pytest.mark.parametrize("change", [
    {"unit": "cents/kWh"},
    {"source_type": "data"},
    {"ref": "plausible looking unsupported estimate"},
    {"source_url": "https://example.org/fake-source"},
    {"retrieved_on": "2026-09-12"},
    {"low": 10, "high": 1},
])
def test_invalid_provenance_units_or_ranges_fail(assumptions_path, payload, change):
    payload["gpu_rental_price_usd_per_hour"].update(change)
    assumptions_path.write_text(document(payload), encoding="utf-8")
    with pytest.raises(AssumptionsError):
        calculate(assumptions_path)


def test_status_cannot_silently_promote_placeholders(assumptions_path, payload):
    payload["status"] = "sourced"
    assumptions_path.write_text(document(payload), encoding="utf-8")
    with pytest.raises(AssumptionsError):
        calculate(assumptions_path)


@pytest.mark.parametrize("content", [
    "# Empty assumptions",
    "<!-- headroom:economics-assumptions:v1 -->\n```json\n{invalid}\n```",
    "<!-- headroom:economics-assumptions:v1 -->\n```json\n{\"schema_version\":1,\"schema_version\":2}\n```",
])
def test_missing_or_malformed_json_never_falls_back(assumptions_path, content):
    assumptions_path.write_text(content, encoding="utf-8")
    with pytest.raises(AssumptionsError):
        calculate(assumptions_path)


def test_multiple_marked_blocks_are_rejected(assumptions_path, payload):
    assumptions_path.write_text(document(payload) + document(payload), encoding="utf-8")
    with pytest.raises(AssumptionsError):
        calculate(assumptions_path)


def test_missing_file_is_explicit_configuration_error(tmp_path):
    with pytest.raises(AssumptionsError):
        calculate(tmp_path / "missing.md")


def test_arithmetic_overflow_is_an_explicit_range_error(assumptions_path):
    with pytest.raises(ArithmeticRangeError):
        calculate(assumptions_path, load_mw=1e308)


def test_tolerance_comes_from_file_and_equality_is_close_call(assumptions_path, payload):
    request = EstimateRequest(**{**REQUEST, "term_years": 1, "load_mw": 1, "flexibility_split": 1})
    # For this setup cost/hour is 2000 and early value is 500000.
    equal_upper = {"p50": 262.5, "p90": 262.5, "p99": 262.5}
    result = build_economics(request, equal_upper, REAL_SOURCE, assumptions_path=assumptions_path)
    assert result.decision == "close_call"
    payload["close_call_fraction"]["value"] = 0
    assumptions_path.write_text(document(payload), encoding="utf-8")
    result = build_economics(request, equal_upper, REAL_SOURCE, assumptions_path=assumptions_path)
    assert result.decision == "not_worth_it"
