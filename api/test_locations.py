"""Catalog identity is independent of annual model readiness and authored demos."""

import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from api import pipeline_provider
from api.locations import DEMO_LABELS
from api.main import app
from api.test_pipeline_provider import payload, write_manifests


ZONES = ["CSWS", "EDE", "GRDA", "INDN", "KACY", "KCPL", "LES", "MPS", "NPPD",
         "OKGE", "OPPD", "SECI", "SPRM", "SPS", "WAUE", "WFEC", "WR"]


@pytest.fixture
def catalog_path(monkeypatch, tmp_path):
    path = tmp_path / "exposure_by_location.parquet"
    monkeypatch.setattr(pipeline_provider, "PARQUET_PATH", path)
    return path


@pytest.fixture
def client():
    with TestClient(app) as instance:
        yield instance


def write_ids(path, values):
    pd.DataFrame({"location_id": values}).to_parquet(path, index=False)


def test_catalog_reads_all_artifact_ids_once_and_retains_scenarios(catalog_path, client):
    # Authored catalog fixture matching the current 18 aggregate/zone + 3 demo IDs.
    ids = ["SPP_SYSTEM", *ZONES, *DEMO_LABELS]
    write_ids(catalog_path, list(reversed(ids)) * 7)
    original = catalog_path.read_bytes()
    response = client.get("/api/locations")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"locations"}
    choices = response.json()["locations"]
    assert len(choices) == 21
    assert choices[0] == {"id": "SPP_SYSTEM", "label": "SPP system aggregate", "kind": "system"}
    assert choices[1:18] == [
        {"id": location_id, "label": f"{location_id} · SPP load zone", "kind": "zone"}
        for location_id in sorted(ZONES)
    ]
    assert choices[18:] == [
        {"id": location_id, "label": label, "kind": "scenario"}
        for location_id, label in DEMO_LABELS.items()
    ]
    assert catalog_path.read_bytes() == original


def test_new_artifact_ids_appear_without_changing_code(catalog_path, client):
    write_ids(catalog_path, ["CSWS"])
    first = client.get("/api/locations").json()["locations"]
    write_ids(catalog_path, ["CSWS", "FUTURE_ZONE"])
    second = client.get("/api/locations").json()["locations"]
    assert "FUTURE_ZONE" not in {row["id"] for row in first}
    assert {"id": "FUTURE_ZONE", "label": "FUTURE_ZONE · SPP load zone", "kind": "zone"} in second


def test_absent_artifact_retains_existing_four_choices(catalog_path, client):
    response = client.get("/api/locations")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["locations"]] == ["SPP_SYSTEM", *DEMO_LABELS]
    assert not catalog_path.exists()


@pytest.mark.parametrize("values", [[], [None], [""], [" \t"], [" CSWS "], [123], [True], ["CSWS", None]])
def test_present_invalid_ids_do_not_silently_fall_back(catalog_path, client, values):
    write_ids(catalog_path, values)
    response = client.get("/api/locations")
    assert response.status_code == 503
    assert "nonempty string IDs" in response.json()["detail"]


@pytest.mark.parametrize("failure", ["bytes", "column", "directory"])
def test_present_unreadable_catalog_is_503(catalog_path, client, failure):
    if failure == "bytes":
        catalog_path.write_bytes(b"authored invalid parquet fixture")
    elif failure == "column":
        pd.DataFrame({"other": ["CSWS"]}).to_parquet(catalog_path, index=False)
    else:
        catalog_path.mkdir()
    response = client.get("/api/locations")
    assert response.status_code == 503
    assert "unreadable" in response.json()["detail"]


def test_catalog_does_not_import_models_or_request_estimates(catalog_path, client, monkeypatch):
    write_ids(catalog_path, ["CSWS", "OKGE", "LES"])
    forbidden = Mock(side_effect=AssertionError("Catalog must only read the location_id column"))
    monkeypatch.setattr(pipeline_provider, "import_module", forbidden)
    monkeypatch.setattr("api.main.build_estimate", forbidden)
    reader = Mock(wraps=pd.read_parquet)
    monkeypatch.setattr("api.locations.pd.read_parquet", reader)
    response = client.get("/api/locations", headers={"Origin": "http://127.0.0.1:5174"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"
    reader.assert_called_once_with(catalog_path, columns=["location_id"])
    forbidden.assert_not_called()


@pytest.mark.parametrize("location_id", ["SPP_SYSTEM", *ZONES, *DEMO_LABELS])
def test_listed_zone_can_reach_existing_ready_provider_with_authored_fixture(
    catalog_path, client, monkeypatch, payload, location_id,
):
    """Disposable full-year software fixture, not evidence that real zones are ready."""
    from pipeline import simulate

    payload["location_id"] = location_id
    pd.DataFrame([
        {**row, "location_id": location_id,
         "confidence_level": payload["confidence"]["level"],
         "confidence_score": payload["confidence"]["score"],
         "n_similar_historical_hours": payload["confidence"]["n_similar_historical_hours"],
         "model_version": payload["model_version"]}
        for row in payload["by_year"]
    ]).to_parquet(catalog_path, index=False)
    write_manifests(catalog_path, payload)
    originals = {path: path.read_bytes() for path in catalog_path.parent.iterdir()}
    forbidden = Mock(side_effect=AssertionError("The ready fixture must only be read"))
    monkeypatch.setattr(simulate, "simulate_exposure", forbidden)
    assert location_id in {row["id"] for row in client.get("/api/locations").json()["locations"]}
    request = {"location_id": location_id, "load_mw": 100, "term_years": 2,
               "flexibility_split": 0.6, "site_exposure": 0.25}
    response = client.post("/api/estimate", json=request)
    assert response.status_code == 200
    result = response.json()
    assert result["inputs_echo"] == {**request, "vpp_solar_homes": 0.0}
    assert result["modeled_exposure"]["p50"] == 37.5
    assert result["modeled_exposure"]["source"]["source_type"] == "model"
    assert "Software-test input provenance" in result["modeled_exposure"]["source"]["ref"]
    assert result["confidence"]["level"] == "Low"
    assert result["economics"]["source"]["source_type"] == "assumption"
    forbidden.assert_not_called()
    assert {path: path.read_bytes() for path in originals} == originals


def test_listing_does_not_override_insufficient_annual_evidence(catalog_path, client, payload):
    """Catalog membership cannot make the unchanged annual guard pass."""
    payload["location_id"] = "CSWS"
    pd.DataFrame([
        {**row, "location_id": "CSWS",
         "confidence_level": payload["confidence"]["level"],
         "confidence_score": payload["confidence"]["score"],
         "n_similar_historical_hours": payload["confidence"]["n_similar_historical_hours"],
         "model_version": payload["model_version"]}
        for row in payload["by_year"]
    ]).to_parquet(catalog_path, index=False)
    write_manifests(catalog_path, payload)
    card_path = catalog_path.with_name("model_card.json")
    card = json.loads(card_path.read_text())
    card["splits"]["test"] = {"start": "2024-10-20T10:00:00Z", "end": "2024-12-31T23:00:00Z"}
    card["test_by_location"]["CSWS"]["n_hours"] = 1717
    card_path.write_text(json.dumps(card), encoding="utf-8")
    assert "CSWS" in {row["id"] for row in client.get("/api/locations").json()["locations"]}
    response = client.post("/api/estimate", json={
        "location_id": "CSWS", "load_mw": 100, "term_years": 2,
        "flexibility_split": 0.6, "site_exposure": 0.25,
    })
    assert response.status_code == 503
    assert response.json() == {"detail": (
        f"Precomputed location CSWS exists, but model_version={payload['model_version']}; "
        "annual reference not ready: held-out span=1742 hours, local scored hours=1717 for CSWS; "
        "requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"
    )}


@pytest.mark.parametrize("location_id", ZONES)
def test_published_zone_reader_succeeds_and_readiness_error_is_not_not_found(client, location_id):
    """Track the real bundle's availability honestly; authored ready tests above
    do not establish that the published short-history bundle is annual-ready."""
    from pipeline.simulate import get_location_estimate
    from api.pipeline_provider import PipelineEstimate, _annual_reference_issue

    payload = get_location_estimate(location_id, path=pipeline_provider.PARQUET_PATH)
    card = json.loads(pipeline_provider.PARQUET_PATH.with_name("model_card.json").read_text(encoding="utf-8"))
    issue = _annual_reference_issue(PipelineEstimate.model_validate(payload), card)
    result = client.post("/api/estimate", json={"location_id": location_id, "load_mw": 100,
        "term_years": 7, "flexibility_split": .6, "site_exposure": .5})
    if issue:
        assert result.status_code == 503
        assert result.json() == {"detail": f"Precomputed location {location_id} exists, but model_version={payload['model_version']}; {issue}"}
    else:
        assert result.status_code == 200
        exposure = result.json()["modeled_exposure"]
        assert exposure["source"]["source_type"] in {"model", "data"}
        assert "mock:" not in exposure["source"]["ref"]
        assert "placeholder" not in exposure["source"]["ref"].lower()
        for annual, saved in zip(exposure["by_year"], payload["by_year"], strict=True):
            for quantile in ("p50", "p90", "p99"):
                assert annual[quantile] == saved[f"{quantile}_hours"] * .5
