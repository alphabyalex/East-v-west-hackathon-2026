"""Explicit seasonal opt-in must not change default evidence eligibility."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.locations import get_locations
from api import portfolio


def request(location):
    return dict(location_id=location, load_mw=100, term_years=7,
                flexibility_split=.6, site_exposure=.5, vpp_solar_homes=0)


@pytest.mark.parametrize("flag", [None, "0", "true"])
def test_default_and_nonexplicit_flags_keep_all_under_evidenced_zones_unavailable(monkeypatch, flag):
    monkeypatch.delenv("FLUXLINE_ALLOW_PROVISIONAL", raising=False)
    if flag is not None:
        monkeypatch.setenv("FLUXLINE_ALLOW_PROVISIONAL", flag)
    client = TestClient(app)
    zones = [row.id for row in get_locations().locations if row.kind == "zone"]
    assert len(zones) == 17
    for zone in zones:
        response = client.post("/api/estimate", json=request(zone))
        assert response.status_code == 503, (zone, response.text)
        assert "annual reference not ready" in response.json()["detail"]


def test_explicit_opt_in_labels_every_zone_and_cannot_clear_portfolio_checks(monkeypatch):
    monkeypatch.setenv("FLUXLINE_ALLOW_PROVISIONAL", "1")
    monkeypatch.setattr(portfolio, "store", portfolio.PortfolioStore())
    client = TestClient(app)
    for zone in [row.id for row in get_locations().locations if row.kind == "zone"]:
        response = client.post("/api/estimate", json=request(zone))
        assert response.status_code == 200, (zone, response.text)
        data = response.json()
        assert data["modeled_exposure"]["source"]["source_type"] == "assumption"
        assert "provisional seasonal estimate" in data["modeled_exposure"]["source"]["ref"]
        assert "not a validated annual number" in data["modeled_exposure"]["source"]["ref"]
        assert data["confidence"]["level"] == "Low"
        assert data["confidence"]["source"]["source_type"] == "assumption"
        assert data["economics"]["source"]["source_type"] == "assumption"
    session = client.post("/api/portfolios").json()["id"]
    path = f"/api/portfolios/{session}"
    site = client.post(path + "/sites", json={"name": "LES", "inputs": request("LES")}).json()["sites"][0]
    data = client.put(path + "/sites/" + site["id"] + "/thresholds", json={"exposure_p90_hours_above": 10000}).json()["sites"][0]
    assert data["ranking"]["status"] == "unavailable"
    assert data["estimate"] is None
    assert data["threshold_status"]["status"] == "unavailable"


def test_launcher_never_enables_provisional_automatically():
    script = (Path(__file__).resolve().parents[1] / "scripts/start-fluxline.ps1").read_text(encoding="utf-8")
    assert "$env:FLUXLINE_ALLOW_PROVISIONAL" not in script
