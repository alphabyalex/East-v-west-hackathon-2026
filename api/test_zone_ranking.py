import json
import pytest
from starlette.testclient import TestClient

from api.main import app
from api.zone_ranking import load_zone_rankings, ZoneRankingsResponse

@pytest.fixture
def client():
    return TestClient(app)

def test_load_zone_rankings_success():
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

def test_api_zone_rankings_endpoint(client):
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
