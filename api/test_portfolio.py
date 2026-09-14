"""Session and static-check contracts; production evidence is never upgraded."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api import portfolio
from api.alerts import Thresholds, evaluate_thresholds
from api.main import app
from api.schemas import EstimateResponse


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(portfolio, "store", portfolio.PortfolioStore())
    return TestClient(app)


def new_session(client):
    response = client.post("/api/portfolios")
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return "/api/portfolios/" + response.json()["id"]


def save(client, url, zone="LES", name="Candidate"):
    response = client.post(url + "/sites", json={"name": name, "inputs": {
        "location_id": zone, "load_mw": 100, "term_years": 7,
        "flexibility_split": .6, "site_exposure": .5, "vpp_solar_homes": 500,
    }})
    assert response.status_code == 201, response.text
    return response.json()


def test_session_crud_real_evidence_and_no_invented_scores(client):
    url, other = new_session(client), new_session(client)
    save(client, url, "LES", "Wind evidence")
    data = save(client, url, "EDE", "No wind reference")
    assert len(data["sites"]) == 2
    assert client.get(url).json()["sites"] == data["sites"]
    assert client.get(other).json()["sites"] == []
    les, ede = data["sites"]
    assert les["wind_evidence"]["proxy_hours"]["value"] == 764
    assert "sha256=" in les["wind_evidence"]["proxy_hours"]["ref"]
    assert ede["wind_evidence"] is None
    for site in data["sites"]:
        assert site["ranking"]["status"] == "unavailable"
        assert site["ranking"]["composite_score"] is None
        assert site["ranking"]["zone_rank"] is None
        assert site["ranking"]["reasons"]
        assert site["estimate"] is None  # Current annual-readiness guard remains intact.
        assert site["estimate_unavailable_reason"]
    site_url = url + "/sites/" + les["id"]
    assert client.delete(other + "/sites/" + les["id"]).status_code == 404
    changed = client.put(site_url + "/thresholds", json={"exposure_p90_hours_above": 100, "confidence_score_below": .7}).json()
    status = changed["sites"][0]["threshold_status"]
    assert status["status"] == "unavailable"
    assert status["mode"] == "static_precomputed_check"
    assert all(check["observed"] is None for check in status["checks"])
    assert "no live monitoring" in changed["check_mode"]
    assert client.get(url).json()["sites"][0]["thresholds"]["confidence_score_below"] == .7
    assert client.delete(site_url).json()["sites"] == [ede]
    assert client.delete(site_url).status_code == 404


def test_system_placeholder_cannot_clear_threshold(client):
    url = new_session(client)
    site = save(client, url, "SPP_SYSTEM")["sites"][0]
    result = client.put(url + "/sites/" + site["id"] + "/thresholds", json={"exposure_p90_hours_above": 100000}).json()["sites"][0]
    assert result["estimate"] is None
    assert "placeholder" in result["estimate_unavailable_reason"]
    assert result["threshold_status"]["status"] == "unavailable"


@pytest.mark.parametrize("thresholds", [{"exposure_p90_hours_above": -1}, {"confidence_score_below": 1.1}, {"confidence_score_below": True}, {"unknown": 5}])
def test_invalid_threshold_rejected_without_mutation(client, thresholds):
    url = new_session(client)
    site = save(client, url)["sites"][0]
    response = client.put(url + "/sites/" + site["id"] + "/thresholds", json=thresholds)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert client.get(url).json()["sites"][0]["thresholds"] == Thresholds().model_dump()


def test_unknown_location_and_expired_session_are_explicit(client):
    url = new_session(client)
    response = client.post(url + "/sites", json={"name": "Unknown", "inputs": {"location_id": "UNKNOWN", "load_mw": 100, "term_years": 1, "site_exposure": .5, "flexibility_split": .5}})
    assert response.status_code == 422
    assert client.get(url).json()["sites"] == []
    response = client.get("/api/portfolios/" + str(uuid4()))
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_store_limits_expiration_and_copy_isolation():
    clock = [0.]
    store = portfolio.PortfolioStore(clock=lambda: clock[0], max_sessions=1, max_sites=1, ttl=10)
    key = store.create()
    item = portfolio.NewSite(name="Site", inputs={"location_id": "LES", "load_mw": 100, "term_years": 1, "site_exposure": .5, "flexibility_split": .5})
    store.add(key, item)
    copied = store.read(key)
    copied[0].inputs.load_mw = 999
    assert store.read(key)[0].inputs.load_mw == 100
    with pytest.raises(Exception, match="Portfolio is full"):
        store.add(key, item)
    with pytest.raises(Exception, match="Session capacity"):
        store.create()
    clock[0] = 10
    with pytest.raises(Exception, match="expired"):
        store.read(key)
    assert store.create() != key


def evidence_estimate():
    """Authored software fixture only; not a production coverage claim."""
    from api.estimate import build_estimate
    from api.mock_provider import get_mock_location
    from api.schemas import EstimateRequest
    request = EstimateRequest(location_id="SPP_SYSTEM", load_mw=100, term_years=7, flexibility_split=.5, site_exposure=.5)
    data = build_estimate(request, get_mock_location).model_dump()
    data["modeled_exposure"]["source"] = {"source_type": "model", "ref": "test-fixture://authored annual-ready evidence"}
    data["confidence"]["source"] = {"source_type": "model", "ref": "test-fixture://authored confidence evidence"}
    data["modeled_exposure"].update(p50=10, p90=20, p99=30)
    data["confidence"]["score"] = .7
    return EstimateResponse.model_validate(data)


@pytest.mark.parametrize("exposure,confidence,expected", [(19,.6,"breached"),(20,.7,"within_thresholds"),(21,.8,"breached"),(21,.6,"within_thresholds")])
def test_static_threshold_math_and_equality(exposure, confidence, expected):
    result = evaluate_thresholds(Thresholds(exposure_p90_hours_above=exposure, confidence_score_below=confidence), evidence_estimate(), "")
    assert result.status == expected
    assert [check.observed for check in result.checks] == [20, .7]
    assert all(check.threshold_source.source_type == "assumption" for check in result.checks)
    assert all(check.observed_source.source_type == "model" for check in result.checks)


def test_partial_evidence_and_disabled_thresholds():
    estimate = evidence_estimate()
    estimate.confidence.source.source_type = "assumption"
    assert evaluate_thresholds(Thresholds(), estimate, "").status == "not_configured"
    result = evaluate_thresholds(Thresholds(exposure_p90_hours_above=30, confidence_score_below=.1), estimate, "")
    assert result.status == "unavailable"
    assert result.checks[0].status == "within_threshold"
    assert result.checks[1].observed is None


def test_cors_supports_threshold_updates_and_deletes(client):
    for method in ("PUT", "DELETE"):
        response = client.options("/api/portfolios", headers={"Origin":"http://127.0.0.1:5174", "Access-Control-Request-Method":method, "Access-Control-Request-Headers":"Content-Type"})
        assert response.status_code == 200
        assert method in response.headers["access-control-allow-methods"]


def test_supported_fixture_thresholds_refresh_and_clear_without_storing_results(client, monkeypatch):
    """Exercise the available path with explicitly authored, nonproduction evidence."""
    estimate = evidence_estimate()
    monkeypatch.setattr(portfolio, "build_estimate", lambda request, provider: estimate.model_copy(update={"inputs_echo": request}))
    url = new_session(client)
    entry = save(client, url)["sites"][0]
    path = url + "/sites/" + entry["id"] + "/thresholds"
    response = client.put(path, json={"exposure_p90_hours_above": 19}).json()
    assert response["sites"][0]["threshold_status"]["status"] == "breached"
    estimate.modeled_exposure.p90 = 15
    refreshed = client.get(url).json()["sites"][0]
    assert refreshed["threshold_status"]["status"] == "within_thresholds"
    assert refreshed["threshold_status"]["checks"][0]["observed"] == 15
    assert client.put(path, json={}).json()["sites"][0]["threshold_status"]["status"] == "not_configured"


def test_published_ranks_are_reused_not_renormalized_for_saved_scenarios(client, monkeypatch):
    from api.zone_ranking import ZoneRankingsResponse
    rows = [{"location_id": location, "rank": rank, "avg_p50_risk_hours": 10, "avg_p90_risk_hours": 20,
             "avg_p99_risk_hours": 30, "avg_worst_contiguous_hours": 1, "wind_absorption_mwh_per_year": 100,
             "carbon_absorbed_tonnes_per_year": 0, "wind_source_ref": "test-fixture://authored evidence",
             "score_risk": score, "score_wind": score, "score_carbon": score, "composite_score": score}
            for rank, (location, score) in enumerate([("LES", .8), ("OKGE", .6)], start=1)]
    manifest = ZoneRankingsResponse(operator="SPP", description="Authored software fixture only",
        composite_weight_formula="0.5*S_risk + 0.3*S_wind + 0.2*S_carbon", rankings=rows)
    monkeypatch.setattr(portfolio, "load_zone_rankings", lambda: manifest)
    url = new_session(client)
    save(client, url, "OKGE")
    save(client, url, "EDE")
    result = save(client, url, "LES")["sites"]
    assert [s["inputs"]["location_id"] for s in result] == ["LES", "OKGE", "EDE"]
    assert [s["ranking"]["composite_score"] for s in result] == [.8, .6, None]
    assert [s["ranking"]["zone_rank"] for s in result] == [1, 2, None]
    assert all(s["ranking"]["source"]["ref"] for s in result)


def test_missing_manifest_preserves_saved_sites_with_explicit_unavailability(client, monkeypatch):
    from api.zone_ranking import ZoneRankingsError
    def unavailable():
        raise ZoneRankingsError("Ranking evidence missing")
    monkeypatch.setattr(portfolio, "load_zone_rankings", unavailable)
    url = new_session(client)
    entry = save(client, url)["sites"][0]
    assert entry["ranking"]["reasons"] == ["Ranking evidence missing"]
    assert entry["ranking"]["composite_score"] is None
    assert client.get(url).json()["sites"][0]["id"] == entry["id"]
