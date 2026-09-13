import json
import pytest
from starlette.testclient import TestClient

from api.main import app
from api import zone_ranking
from api.zone_ranking import load_zone_rankings, ZoneRankingsResponse

@pytest.fixture
def rankings_manifest(tmp_path, monkeypatch):
    """Exercise the reader without depending on ignored local model outputs."""
    monkeypatch.setattr(zone_ranking, "ROOT_DIR", tmp_path)
    path = tmp_path / "data/processed/national_stack/zone_rankings.json"
    path.parent.mkdir(parents=True)
    payload = {
        "operator": "SPP",
        "composite_weight_formula": "0.5*S_risk + 0.3*S_wind + 0.2*S_carbon",
        "description": "Authored contract-test fixture; not measured rankings.",
        "rankings": [
            {
                "location_id": location,
                "avg_p50_risk_hours": 10,
                "avg_p90_risk_hours": 20,
                "avg_p99_risk_hours": 30,
                "avg_worst_contiguous_hours": 4,
                "wind_absorption_mwh_per_year": 100,
                "carbon_absorbed_tonnes_per_year": 20,
                "wind_source_ref": "test-fixture://authored/wind",
                "score_risk": score,
                "score_wind": score,
                "score_carbon": score,
                "composite_score": score,
                "rank": rank,
            }
            for rank, location, score in ((1, "test-zone-a", 80), (2, "test-zone-b", 60))
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def client():
    return TestClient(app)

def test_load_zone_rankings_success(rankings_manifest):
    rankings = load_zone_rankings()
    assert isinstance(rankings, ZoneRankingsResponse)
    assert rankings.operator == "SPP"
    assert "S_risk" in rankings.composite_weight_formula
    assert len(rankings.rankings) > 0
    
    # Assert rankings are properly sorted by composite score descending
    scores = [item.composite_score for item in rankings.rankings]
    assert scores == sorted(scores, reverse=True)
    
    # Assert ranks are sequential starting from 1
    ranks = [item.rank for item in rankings.rankings]
    assert ranks == list(range(1, len(rankings.rankings) + 1))

def test_api_zone_rankings_endpoint(client, rankings_manifest):
    response = client.get("/api/zone-rankings")
    assert response.status_code == 200
    data = response.json()
    assert data["operator"] == "SPP"
    assert "composite_weight_formula" in data
    assert len(data["rankings"]) > 0
    
    # Check first item fields
    item = data["rankings"][0]
    assert "location_id" in item
    assert "composite_score" in item
    assert "rank" in item
    assert "score_risk" in item
    assert "score_wind" in item
    assert "score_carbon" in item


def test_api_zone_rankings_missing_manifest_returns_unavailable(client, rankings_manifest):
    rankings_manifest.unlink()
    response = client.get("/api/zone-rankings")
    assert response.status_code == 503
    assert "missing" in response.json()["detail"]


@pytest.mark.parametrize("contents", ["not JSON", "{}"])
def test_api_zone_rankings_invalid_manifest_returns_unavailable(client, rankings_manifest, contents):
    rankings_manifest.write_text(contents, encoding="utf-8")
    response = client.get("/api/zone-rankings")
    assert response.status_code == 503
    assert "Malformed zone rankings schema" in response.json()["detail"]
