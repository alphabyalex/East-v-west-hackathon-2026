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
                "score_risk": score / 100,
                "score_wind": score / 100,
                "score_carbon": score / 100,
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


@pytest.mark.parametrize("field,value", [
    ("avg_p50_risk_hours", 21), ("avg_p99_risk_hours", 19),
    ("avg_p99_risk_hours", 8761), ("avg_worst_contiguous_hours", 8761),
    ("score_risk", 1.01), ("score_wind", 80), ("score_carbon", -1),
    ("composite_score", 101), ("rank", 0), ("rank", True),
    ("rank", 1.0), ("rank", 2), ("location_id", "test-zone-b"),
])
def test_invalid_ranking_rows_return_503(client, rankings_manifest, field, value):
    data = json.loads(rankings_manifest.read_text(encoding="utf-8"))
    data["rankings"][0][field] = value
    rankings_manifest.write_text(json.dumps(data), encoding="utf-8")
    response = client.get("/api/zone-rankings")
    assert response.status_code == 503
    assert "Malformed zone rankings schema" in response.json()["detail"]


@pytest.mark.parametrize("change", ["empty", "rank_gap", "reversed_scores", "reversed_rows"])
def test_inconsistent_ranking_lists_return_503(client, rankings_manifest, change):
    data = json.loads(rankings_manifest.read_text(encoding="utf-8"))
    if change == "empty":
        data["rankings"] = []
    elif change == "rank_gap":
        data["rankings"][1]["rank"] = 3
    elif change == "reversed_scores":
        data["rankings"][1]["composite_score"] = 90
    else:
        data["rankings"].reverse()
    rankings_manifest.write_text(json.dumps(data), encoding="utf-8")
    assert client.get("/api/zone-rankings").status_code == 503


def test_equal_composite_scores_preserve_authored_tie_order(client, rankings_manifest):
    data = json.loads(rankings_manifest.read_text(encoding="utf-8"))
    data["rankings"][1]["composite_score"] = 80
    rankings_manifest.write_text(json.dumps(data), encoding="utf-8")
    response = client.get("/api/zone-rankings")
    assert response.status_code == 200
    assert [row["location_id"] for row in response.json()["rankings"]] == ["test-zone-a", "test-zone-b"]


def test_duplicate_keys_do_not_silently_overwrite_a_ranking(client, rankings_manifest):
    contents = rankings_manifest.read_text(encoding="utf-8").replace('"rank": 1', '"rank": 2, "rank": 1', 1)
    rankings_manifest.write_text(contents, encoding="utf-8")
    assert client.get("/api/zone-rankings").status_code == 503


def test_unreadable_rankings_return_503_without_exposing_file_path(client, rankings_manifest):
    rankings_manifest.unlink()
    rankings_manifest.mkdir()
    response = client.get("/api/zone-rankings")
    assert response.status_code == 503
    assert response.json()["detail"] == "Zone rankings manifest could not be read"

def test_api_zone_rankings_missing_manifest_is_unavailable(client, tmp_path, monkeypatch):
    monkeypatch.setattr(zone_ranking, "ROOT_DIR", tmp_path)
    response = client.get("/api/zone-rankings")
    assert response.status_code == 503
    assert response.json() == {"detail": "Zone rankings JSON manifest is missing; provide a reviewed rankings artifact."}
