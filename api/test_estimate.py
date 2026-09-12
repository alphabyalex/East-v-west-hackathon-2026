"""HTTP contract, fixture parity, scenario arithmetic, and provider swap checks."""

from dataclasses import replace
import json

from fastapi.testclient import TestClient
import pytest

from api.main import app, get_location_provider
from api.mock_provider import FIXTURE_PATH, LOCATION_SCALES, get_mock_location
from api.schemas import Confidence, Source


EXAMPLE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
DEFAULT_REQUEST = EXAMPLE["inputs_echo"]


@pytest.fixture
def client():
    with TestClient(app) as instance:
        yield instance
    app.dependency_overrides.clear()


def estimate(client, **overrides):
    response = client.post("/api/estimate", json={**DEFAULT_REQUEST, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def assert_fixture_match(actual, expected):
    """Numeric JSON parity allows only ordinary cross-runtime floating-point noise."""
    if isinstance(expected, dict):
        assert set(actual) == set(expected)
        for key in expected:
            assert_fixture_match(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for item, reference in zip(actual, expected):
            assert_fixture_match(item, reference)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12)
    else:
        assert actual == expected


def test_default_response_matches_checked_in_frontend_fixture(client):
    response = client.post("/api/estimate", json=DEFAULT_REQUEST)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert_fixture_match(response.json(), EXAMPLE)


@pytest.mark.parametrize("site_exposure,decision", [(0.4, "worth_it"), (0.55, "close_call"), (0.9, "not_worth_it")])
def test_exposure_assumption_changes_distribution_costs_and_decision(client, site_exposure, decision):
    result = estimate(client, site_exposure=site_exposure)
    for quantile in ("p50", "p90", "p99"):
        assert result["modeled_exposure"][quantile] == pytest.approx(
            EXAMPLE["modeled_exposure"][quantile] * site_exposure / DEFAULT_REQUEST["site_exposure"]
        )
        assert result["economics"]["annual_cost_usd"][quantile] == pytest.approx(
            EXAMPLE["economics"]["annual_cost_usd"][quantile] * site_exposure / DEFAULT_REQUEST["site_exposure"]
        )
    assert result["economics"]["decision"] == decision
    # Mock estimate support is explicit and fixed, not a future probability.
    assert result["confidence"] == EXAMPLE["confidence"]


@pytest.mark.parametrize("location_id,scale", LOCATION_SCALES.items())
@pytest.mark.parametrize("term_years", [1, 3, 7])
def test_locations_and_full_ordered_contract_horizon(client, location_id, scale, term_years):
    result = estimate(client, location_id=location_id, term_years=term_years, site_exposure=1)
    rows = result["modeled_exposure"]["by_year"]
    assert [row["year"] for row in rows] == list(range(1, term_years + 1))
    assert rows[0]["p50"] == pytest.approx(200 * scale)
    for quantile in ("p50", "p90", "p99"):
        assert result["modeled_exposure"][quantile] == pytest.approx(sum(row[quantile] for row in rows) / term_years)
    assert result["inputs_echo"] == {**DEFAULT_REQUEST, "location_id": location_id, "term_years": term_years, "site_exposure": 1}
    assert result["economics"]["value_of_early_connection_usd"] == min(term_years, 3) * 100 * 500000


def test_load_and_flexibility_change_economics_without_changing_exposure(client):
    baseline = estimate(client)
    larger = estimate(client, load_mw=200)
    less_flexible = estimate(client, flexibility_split=0.3)
    for result in (larger, less_flexible):
        assert result["modeled_exposure"]["by_year"] == baseline["modeled_exposure"]["by_year"]
    for quantile in ("p50", "p90", "p99"):
        base_cost = baseline["economics"]["annual_cost_usd"][quantile]
        assert larger["economics"]["annual_cost_usd"][quantile] == pytest.approx(base_cost * 2)
        assert less_flexible["economics"]["annual_cost_usd"][quantile] == pytest.approx(base_cost / 2)
    assert larger["economics"]["value_of_early_connection_usd"] == baseline["economics"]["value_of_early_connection_usd"] * 2
    assert less_flexible["economics"]["breakeven_exposure_hours_per_year"] == pytest.approx(
        baseline["economics"]["breakeven_exposure_hours_per_year"] * 2
    )


def test_zero_exposure_has_zero_hours_and_costs(client):
    result = estimate(client, site_exposure=0)
    for quantile in ("p50", "p90", "p99"):
        assert result["modeled_exposure"][quantile] == 0
        assert result["economics"]["annual_cost_usd"][quantile] == 0
    assert result["modeled_exposure"]["worst_contiguous_outage_hours"] == 0
    assert result["economics"]["decision"] == "worth_it"


def test_zero_interruptible_load_has_no_finite_break_even(client):
    result = estimate(client, flexibility_split=0)
    assert result["modeled_exposure"]["p50"] > 0
    assert result["economics"]["annual_cost_usd"] == {"p50": 0, "p90": 0, "p99": 0}
    assert result["economics"]["lost_gpu_hours_per_year"] == {"p50": 0, "p90": 0, "p99": 0}
    assert result["economics"]["breakeven_exposure_hours_per_year"] is None


@pytest.mark.parametrize("updates", [
    {"load_mw": 0}, {"load_mw": -1}, {"load_mw": "100"}, {"load_mw": True},
    {"term_years": 0}, {"term_years": 8}, {"term_years": 2.5}, {"term_years": "7"}, {"term_years": True},
    {"site_exposure": -0.01}, {"site_exposure": 1.01}, {"site_exposure": "0.4"},
    {"flexibility_split": -0.01}, {"flexibility_split": 1.01}, {"flexibility_split": False},
    {"location_id": ""}, {"location_id": "  "}, {"location_id": 42},
    {"gpu_hour_value_usd": 2}, {"load_mw": 1e308},
])
def test_invalid_requests_return_422_not_coerced_or_500(client, updates):
    response = client.post("/api/estimate", json={**DEFAULT_REQUEST, **updates})
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize("field", DEFAULT_REQUEST.keys())
def test_every_canonical_request_field_is_required(client, field):
    request = {key: value for key, value in DEFAULT_REQUEST.items() if key != field}
    assert client.post("/api/estimate", json=request).status_code == 422


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_nonfinite_json_returns_serializable_validation_error(client, nonfinite):
    payload = json.dumps({**DEFAULT_REQUEST, "load_mw": "NONFINITE"}).replace('"NONFINITE"', nonfinite)
    response = client.post("/api/estimate", content=payload, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "detail" in response.json()


def test_unknown_location_does_not_silently_substitute_a_fixture(client):
    response = client.post("/api/estimate", json={**DEFAULT_REQUEST, "location_id": "SPP_UNKNOWN"})
    assert response.status_code == 404


def test_all_mock_blocks_have_explicit_assumption_provenance(client):
    result = estimate(client)
    sources = [result[key]["source"] for key in ("modeled_exposure", "confidence", "economics")]
    sources += [row["source"] for row in result["tariff"]["curtailment_triggers"]]
    for source in sources:
        assert source["source_type"] == "assumption"
        assert source["ref"].startswith("mock://illustrative/")
    assert "not_ensemble_inference" in result["confidence"]["basis"]
    assert "not extracted" in result["tariff"]["service"]


def test_fixture_is_deterministic_and_not_mutated_across_requests(client):
    before = estimate(client)
    estimate(client, location_id="spp-oklahoma-city-demo", term_years=1, site_exposure=0.9)
    assert estimate(client) == before


def test_precomputed_provider_can_be_replaced_without_changing_http_contract(client):
    def replacement(location_id):
        mock = get_mock_location(location_id)
        return replace(
            mock,
            by_year=tuple(replace(row, p50_hours=10, p90_hours=20, p99_hours=30) for row in mock.by_year),
            confidence=Confidence(
                level="Low", score=0.2, basis="test-provider-disagreement",
                source=Source(source_type="model", ref="test://precomputed/confidence"),
            ),
            source=Source(source_type="model", ref="test://precomputed/exposure/version"),
        )

    app.dependency_overrides[get_location_provider] = lambda: replacement
    result = estimate(client, site_exposure=0.5)
    assert result["modeled_exposure"]["p50"] == 5
    assert result["modeled_exposure"]["source"]["source_type"] == "model"
    assert result["modeled_exposure"]["source"]["ref"].startswith("test://precomputed/exposure/version?")
    assert result["confidence"]["basis"] == "test-provider-disagreement"
    assert result["confidence"]["score"] == 0.2
    # Real exposure would not silently make the still-unsourced economics real.
    assert result["economics"]["source"]["source_type"] == "assumption"
    assert result["economics"]["source"]["ref"].startswith("mock://")
