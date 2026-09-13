import json
import pytest
from starlette.testclient import TestClient

from api.main import app
from api.zone_ranking import load_zone_rankings, ZoneRankingsResponse
from api import zone_ranking


@pytest.fixture
def ranking_manifest(tmp_path, monkeypatch):
    """Authored serving fixture; no dependency on a teammate's ignored outputs."""
    monkeypatch.setattr(zone_ranking, "ROOT_DIR", tmp_path)
    path = tmp_path / "data/processed/national_stack/zone_rankings.json"
    path.parent.mkdir(parents=True)
    rows = [{
        "location_id": location, "rank": rank,
        "avg_p50_risk_hours": 10, "avg_p90_risk_hours": 20,
        "avg_p99_risk_hours": 30, "avg_worst_contiguous_hours": 2,
        "wind_absorption_mwh_per_year": 100,
        "carbon_absorbed_tonnes_per_year": 0,
        "wind_source_ref": "test-fixture://authored-ranking; not production evidence",
        "score_risk": score, "score_wind": score, "score_carbon": score,
        "composite_score": score,
    } for rank, (location, score) in enumerate([("LES", .8), ("OKGE", .6)], start=1)]
    path.write_text(json.dumps({"operator": "SPP",
        "composite_weight_formula": "0.5*S_risk + 0.3*S_wind + 0.2*S_carbon",
        "description": "Authored software test fixture", "rankings": rows}), encoding="utf-8")
    return path

@pytest.fixture
def client():
    return TestClient(app)

def test_load_zone_rankings_success(ranking_manifest):
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

def test_api_zone_rankings_endpoint(client, ranking_manifest):
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


def test_api_zone_rankings_missing_manifest_is_unavailable(client, tmp_path, monkeypatch):
    monkeypatch.setattr(zone_ranking, "ROOT_DIR", tmp_path)
    response = client.get("/api/zone-rankings")
    assert response.status_code == 503
    assert response.json() == {"detail": "Zone rankings JSON manifest is missing; run 'python -m pipeline.site_rank' first."}
