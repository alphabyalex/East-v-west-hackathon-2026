"""Disposable contract fixtures; no production model fitting or weather fetches."""
import copy
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api import location_estimator as adapter
from pipeline.workbench import Workspace, handler_for

INPUTS = dict(location_id="SPP_SYSTEM", load_mw=200, contract_years=3, flexibility_percent=50,
              site_exposure=.25, firm_wait_years=2, gpu_per_mw=100, gpu_hour_value_usd=2,
              early_margin_usd_per_mw_year=10000, vpp_solar_homes=0)
SCAN = "data/processed/workbench/sites/scan-fixture"
REPORT = "data/processed/workbench/sites/report-fixture"
POINT = dict(name="Authored test location", latitude=38., longitude=-98.)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    scan = {"query": "Test, KS", "candidates": [POINT], "source": {"ref": "test://authored"}}
    report = {"status": "research_transfer_exposure", "location": POINT,
              "estimates": {"annual_expected_hours": 400, "assumed_site_annual_hours": 400},
              "assumptions": {"site_exposure": 1, "load_mw": 100, "conditional_share": 1, "years": 7},
              "coverage": {"status": "historical_spp_match"}, "model_version": "test-only-model",
              "confidence": {"level": "Low"}, "input_hashes": {"model": "test-hash"},
              "limitations": ["Authored fixture, not measured data."]}
    for folder, name, payload in [(SCAN, "scan.json", scan), (REPORT, "site_report.json", report)]:
        path = tmp_path / folder / name
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    runner = Mock(side_effect=AssertionError("No offline jobs on the cached path"))
    monkeypatch.setattr(adapter, "workspace_job", runner)
    return TestClient(app), tmp_path, runner


def body(**changes):
    return {"scan_id": SCAN, "candidate": 0, "inputs": {**INPUTS, **changes}}


def test_cached_location_reuses_unscaled_expectation_and_all_inputs(setup):
    client, root, runner = setup
    original = (root / REPORT / "site_report.json").read_bytes()
    response = client.post("/api/location-estimator/estimate", json=body())
    assert response.status_code == 200
    value = response.json()["result"]
    assert value["inputs_echo"] == INPUTS
    assert value["exposure"] == dict(annual_expected_hours=100, regional_expected_hours=400,
                                     term_expected_hours=300, annual_energy_mwh=10000)
    assert value["economics"]["annual_gpu_hours"] == 1000000
    assert value["economics"]["annual_cost_usd"] == 2000000
    assert value["economics"]["net_value_usd"] == -2000000
    assert value["economics"]["break_even_hours"] == pytest.approx(66.6666666667)
    assert not any(key in value["exposure"] for key in ("p50", "p90", "p99"))
    assert value["confidence"] == {"level": "Low"}
    assert "test-hash" in value["source"]["ref"]
    assert response.headers["cache-control"] == "no-store"
    assert (root / REPORT / "site_report.json").read_bytes() == original
    runner.assert_not_called()


def test_zero_exposure_and_flexibility_have_distinct_effects(setup):
    client, _, _ = setup
    zero_site = client.post("/api/location-estimator/estimate", json=body(site_exposure=0)).json()["result"]
    zero_share = client.post("/api/location-estimator/estimate", json=body(flexibility_percent=0)).json()["result"]
    assert zero_site["exposure"]["annual_expected_hours"] == 0
    assert zero_site["exposure"]["regional_expected_hours"] == 400
    assert zero_share["exposure"]["annual_expected_hours"] == 100
    assert zero_share["exposure"]["annual_energy_mwh"] == 0
    assert zero_share["economics"]["break_even_hours"] is None


def test_economic_overrides_and_vpp_are_applied_without_changing_hours(setup):
    client, _, _ = setup
    result = client.post("/api/location-estimator/estimate", json=body(vpp_solar_homes=1000, gpu_hour_value_usd=3)).json()["result"]
    rate = result["calculation_inputs"]["vpp_battery_discharge_mw_per_home"]["value"]
    arbitrage = result["calculation_inputs"]["vpp_arbitrage_revenue_usd_per_mwh"]["value"]
    assert result["economics"]["vpp_offset_mw"] == 1000 * rate
    assert result["economics"]["annual_cost_usd"] == 100 * max(0, 100 - 1000 * rate) * 100 * 3 - 100 * 1000 * rate * arbitrage
    assert result["exposure"]["annual_expected_hours"] == 100


def test_cached_search_and_suggestions_are_read_only(setup):
    client, _, runner = setup
    assert client.get("/api/location-estimator/locations").json() == {"locations": ["Test, KS"]}
    result = client.post("/api/location-estimator/search", json={"query": " test, ks "}).json()
    assert result["candidates"] == [POINT]
    assert result["scan_id"] == SCAN
    runner.assert_not_called()


def test_uncached_search_and_estimate_use_existing_workspace_job_contract(setup):
    client, root, runner = setup
    runner.side_effect = None
    runner.return_value = {"status": "running", "job_id": "test-job"}
    assert client.post("/api/location-estimator/search", json={"query": "Another, KS"}).json()["status"] == "running"
    runner.assert_called_with({"kind": "site-scan", "query": "Another, KS"})
    (root / REPORT / "site_report.json").unlink()
    assert client.post("/api/location-estimator/estimate", json=body()).json()["status"] == "running"
    runner.assert_called_with(dict(kind="site-transfer", scan=SCAN, candidate=0, load_mw=200,
                                   conditional_share=.5, site_exposure=.25, years=3, confirm_spp=False))


@pytest.mark.parametrize("field,value", [("load_mw", 0), ("site_exposure", 1.1), ("contract_years", 1.5),
                                         ("flexibility_percent", -1), ("gpu_per_mw", True), ("vpp_solar_homes", -1)])
def test_invalid_inputs_never_start_jobs(setup, field, value):
    client, _, runner = setup
    assert client.post("/api/location-estimator/estimate", json=body(**{field: value})).status_code == 422
    runner.assert_not_called()


@pytest.mark.parametrize("headers", [{"Origin": "https://untrusted.example"}, {"Host": "untrusted.example"},
                                    {"Sec-Fetch-Site": "cross-site"}, {"Content-Type": "text/plain"}])
def test_cross_origin_or_non_json_cannot_start_jobs(setup, headers):
    client, _, runner = setup
    assert client.post("/api/location-estimator/search", json={"query": "Another, KS"}, headers=headers).status_code in (403, 415)
    runner.assert_not_called()


def test_mismatched_report_and_unverified_coverage_are_rejected(setup):
    client, root, _ = setup
    path = root / REPORT / "site_report.json"
    report = json.loads(path.read_text())
    other = copy.deepcopy(report)
    other["location"]["latitude"] = 40
    path.write_text(json.dumps(other))
    assert client.post("/api/location-estimator/estimate", json={**body(), "report_id": REPORT}).status_code == 422
    report["coverage"]["status"] = "unverified"
    path.write_text(json.dumps(report))
    assert client.post("/api/location-estimator/estimate", json={**body(), "report_id": REPORT}).status_code == 422


def test_job_polling_is_specific_and_blocks_path_traversal(setup):
    client, root, _ = setup
    identifier = "20260913T100000_1234abcd"
    path = root / "data/processed/workbench/jobs" / identifier / "job.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(status="succeeded", kind="site-transfer", result_id=REPORT)))
    assert client.get(f"/api/location-estimator/jobs/{identifier}").json() == dict(status="succeeded", result_id=REPORT)
    assert client.get("/api/location-estimator/jobs/invalid").status_code == 404
    assert client.post("/api/location-estimator/estimate", json={**body(), "scan_id": "../../AGENTS.md"}).status_code == 404


def test_adapter_uses_workspace_token_and_single_job_owner(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    workspace.submit = Mock(return_value={"id": "contract-test"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(workspace))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(adapter, "WORKSPACE_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        assert adapter.workspace_job({"kind": "site-scan", "query": "Test, KS"}) == {"status": "running", "job_id": "contract-test"}
        workspace.submit.assert_called_once_with({"kind": "site-scan", "query": "Test, KS"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("failure,detail", [
    (URLError("connection refused"), "workbench at 127.0.0.1:8765 is not reachable"),
    (TimeoutError(), "starting only uvicorn and Vite is not enough"),
])
def test_missing_workbench_reports_required_process(monkeypatch, tmp_path, failure, detail):
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    monkeypatch.setattr(adapter, "build_opener", lambda *args: Mock(open=Mock(side_effect=failure)))
    response = TestClient(app).post("/api/location-estimator/search", json={"query": "Wichita, KS"})
    assert response.status_code == 503
    assert detail in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("page,job,expected", [
    (b"<html>different service</html>", b"{}", "no workspace token"),
    (b'<meta name="workspace-token" content="test-token">', b"not-json", "invalid job response"),
    (b'<meta name="workspace-token" content="test-token">', b"{}", "invalid job response"),
    (b'<meta name="workspace-token" content="test-token">', b"null", "invalid job response"),
])
def test_workbench_bad_response_is_distinct_from_stopped_service(monkeypatch, tmp_path, page, job, expected):
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    opener = Mock(open=Mock(side_effect=[BytesIO(page), BytesIO(job)]))
    monkeypatch.setattr(adapter, "build_opener", lambda *args: opener)
    response = TestClient(app).post("/api/location-estimator/search", json={"query": "Wichita, KS"})
    assert response.status_code == 503
    assert expected in response.json()["detail"]
    assert "not reachable" not in response.json()["detail"]
    assert opener.open.call_count == (1 if "token" in expected else 2)


def test_busy_workbench_retains_conflict_response(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    opener = Mock(open=Mock(side_effect=[BytesIO(b'<meta name="workspace-token" content="test-token">'),
                                        HTTPError(adapter.WORKSPACE_URL, 400, "busy", {}, None)]))
    monkeypatch.setattr(adapter, "build_opener", lambda *args: opener)
    response = TestClient(app).post("/api/location-estimator/search", json={"query": "Wichita, KS"})
    assert response.status_code == 409
    assert "busy" in response.json()["detail"]
