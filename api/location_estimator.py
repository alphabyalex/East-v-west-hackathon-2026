"""Local Fluxline adapter for the existing, single-job location workspace.

Reads completed reports for cheap scenario arithmetic. Only explicit search/estimate
requests can start an offline job; the canonical estimate API remains read-only.
"""
import json
import math
import re
from pathlib import Path
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, build_opener, ProxyHandler

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from .economics import load_assumptions
from .schemas import ContractModel

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_URL = "http://127.0.0.1:8765"


def local_request(request: Request):
    """Do not turn the private job runner into a cross-origin/public API."""
    if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
        raise HTTPException(403, "Location estimation is available on this computer only.")
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Location estimation is available on this computer only.")
    allowed = {"http://127.0.0.1:5174", "http://127.0.0.1:8000", "http://localhost:5174"}
    if request.headers.get("origin") and request.headers["origin"] not in allowed:
        raise HTTPException(403, "Open the local Fluxline app to estimate a location.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Open the local Fluxline app to estimate a location.")
    if request.method == "POST" and request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Submit a JSON estimate request.")


router = APIRouter(prefix="/api/location-estimator", dependencies=[Depends(local_request)])


class LocationSearch(ContractModel):
    query: Annotated[str, Field(min_length=2, max_length=120, pattern=r"^[^\x00-\x1f]+$")]


class LocationInputs(ContractModel):
    # All existing Fluxline controls travel together, including economic overrides.
    location_id: str
    load_mw: Annotated[float, Field(ge=0.001, le=10000)]
    contract_years: Annotated[int, Field(ge=1, le=7)]
    flexibility_percent: Annotated[float, Field(ge=0, le=100)]
    site_exposure: Annotated[float, Field(ge=0, le=1)]
    firm_wait_years: Annotated[float, Field(ge=0, le=20)]
    gpu_per_mw: Annotated[float, Field(gt=0, le=2000)]
    gpu_hour_value_usd: Annotated[float, Field(ge=0, le=100)]
    early_margin_usd_per_mw_year: Annotated[float, Field(ge=0, le=10000000)]
    vpp_solar_homes: Annotated[float, Field(ge=0, le=10000)]


class LocationEstimate(ContractModel):
    scan_id: str
    candidate: Annotated[int, Field(ge=0)]
    inputs: LocationInputs
    report_id: str | None = None


def records(filename):
    home = (ROOT / "data/processed/workbench/sites").resolve()
    for path in sorted(home.glob("*/" + filename), reverse=True):
        if not path.resolve().is_relative_to(home):
            continue
        try:
            yield path.parent.relative_to(ROOT).as_posix(), json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # A partially written report is not ready.


def record(identifier, filename):
    for key, value in records(filename):
        if key == identifier:
            return value
    raise HTTPException(404, "This location result is unavailable. Search again.")


def workspace_job(payload):
    # Fixed loopback target, no proxy inheritance or user-controlled URL. The
    # workspace retains its token check, registered-input checks and one-job lock.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(WORKSPACE_URL + "/", timeout=5) as response:
            page = response.read().decode("utf-8")
        token = re.search(r'<meta name="workspace-token" content="([\w-]+)">', page)
        if not token:
            raise HTTPException(503, "The location estimator is unavailable. Start Open Fluxline.cmd and retry.")
        request = UrlRequest(WORKSPACE_URL + "/api/jobs", data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Origin": WORKSPACE_URL, "X-Workspace-Token": token[1],
        }, method="POST")
        with opener.open(request, timeout=20) as response:
            job = json.load(response)
        return {"status": "running", "job_id": job["id"]}
    except HTTPError as error:
        raise HTTPException(409 if error.code == 400 else 503,
                            "The location estimator is busy or could not accept this request. Retry after the current estimate finishes.") from error
    except (URLError, TimeoutError, OSError, ValueError, KeyError) as error:
        raise HTTPException(503, "The location estimator is unavailable. Start Open Fluxline.cmd and retry.") from error


def same_point(a, b):
    return a.get("latitude") == b.get("latitude") and a.get("longitude") == b.get("longitude")


def result_for(report_id, report, inputs):
    if report.get("status") != "research_transfer_exposure":
        raise HTTPException(422, report.get("message") or "No regional estimate is available for this location.")
    if report.get("coverage", {}).get("status") not in {"historical_spp_match", "regional_spp_comparison"}:
        raise HTTPException(422, "This location needs an independent coverage review before a regional estimate can be used.")
    try:
        regional = float(report["estimates"]["annual_expected_hours"])
        if not math.isfinite(regional) or not 0 <= regional <= 8760:
            raise ValueError("Invalid annual hours")
        model = report["model_version"]
        model_hash = report["input_hashes"]["model"]
        if report["confidence"]["level"] != "Low":
            raise ValueError("Unrecognized regional confidence")
        defaults = load_assumptions()
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(503, "The saved location estimate is incomplete or invalid.") from error
    hours = regional * inputs.site_exposure
    capacity = inputs.load_mw * inputs.flexibility_percent / 100
    vpp = inputs.vpp_solar_homes * defaults.vpp_battery_discharge_mw_per_home.value
    net_capacity = max(0, capacity - vpp)
    gpu_hours = hours * net_capacity * inputs.gpu_per_mw
    vpp_revenue = hours * vpp * defaults.vpp_arbitrage_revenue_usd_per_mwh.value
    annual_cost = gpu_hours * inputs.gpu_hour_value_usd - vpp_revenue
    benefit = min(inputs.firm_wait_years, inputs.contract_years) * inputs.load_mw * inputs.early_margin_usd_per_mw_year
    cost_per_hour = net_capacity * inputs.gpu_per_mw * inputs.gpu_hour_value_usd - vpp * defaults.vpp_arbitrage_revenue_usd_per_mwh.value
    term_cost = annual_cost * inputs.contract_years
    ref = f"{report_id}/site_report.json; model={model}; model_sha256={model_hash}"
    source = {"source_type": "model", "ref": ref + "; stationary annual expectation, not P50/P90/P99"}
    assumption_source = {"source_type": "assumption", "ref": ref + "; site hours = regional hours * user site_exposure; energy = site hours * user load_mw * user flexibility_percent/100"}
    economic_source = {"source_type": "assumption", "ref": ref + "; user economic inputs; expected annual GPU loss less assumed VPP arbitrage; docs/ASSUMPTIONS.md"}
    return {
        "inputs_echo": inputs.model_dump(), "report_id": report_id, "location": report["location"],
        "model_version": model, "created_utc": report.get("created_utc"), "confidence": {"level": "Low"},
        "location_data_note": report.get("location_data_note", ""), "limitations": report.get("limitations", []),
        "source": source, "assumption_source": assumption_source, "economic_source": economic_source,
        "exposure": {"annual_expected_hours": hours, "regional_expected_hours": regional,
                     "term_expected_hours": hours * inputs.contract_years, "annual_energy_mwh": hours * capacity},
        "economics": {"interruptible_mw": capacity, "vpp_offset_mw": vpp, "net_interruptible_mw": net_capacity,
                      "annual_gpu_hours": gpu_hours, "vpp_annual_revenue_usd": vpp_revenue,
                      "annual_cost_usd": annual_cost, "term_cost_usd": term_cost,
                      "early_access_value_usd": benefit, "net_value_usd": benefit - term_cost,
                      "break_even_hours": benefit / (inputs.contract_years * cost_per_hour) if cost_per_hour > 0 else None},
        "calculation_inputs": {"vpp_battery_discharge_mw_per_home": defaults.vpp_battery_discharge_mw_per_home.model_dump(),
                               "vpp_arbitrage_revenue_usd_per_mwh": defaults.vpp_arbitrage_revenue_usd_per_mwh.model_dump()},
        "provenance": {key: report.get(key) for key in ("input_hashes", "query_weather", "training_sources", "coverage", "geocoding")},
    }


@router.get("/locations")
def locations(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"locations": sorted({scan["query"] for _, scan in records("scan.json") if scan.get("candidates")})}


@router.post("/search")
def search(body: LocationSearch, response: Response):
    response.headers["Cache-Control"] = "no-store"
    query = body.query.strip()
    if len(query) < 2:
        raise HTTPException(422, "Enter a city and state, or latitude and longitude.")
    for identifier, scan in records("scan.json"):
        if scan.get("query", "").strip().casefold() == query.casefold():
            return {"status": "succeeded", "scan_id": identifier, "candidates": scan["candidates"]}
    return workspace_job({"kind": "site-scan", "query": query})


@router.get("/jobs/{job_id}")
def job_status(job_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not re.fullmatch(r"\d{8}T\d{6}_[0-9a-f]{8}", job_id):
        raise HTTPException(404, "Estimate job not found.")
    path = ROOT / "data/processed/workbench/jobs" / job_id / "job.json"
    if not path.resolve().is_relative_to((ROOT / "data/processed/workbench/jobs").resolve()):
        raise HTTPException(404, "Estimate job not found.")
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HTTPException(404, "Estimate job is unavailable. Retry your estimate.") from error
    if job.get("kind") not in {"site-scan", "site-transfer"}:
        raise HTTPException(404, "Estimate job not found.")
    return {"status": job["status"], "result_id": job["result_id"]}


@router.post("/estimate")
def estimate_location(body: LocationEstimate, response: Response):
    response.headers["Cache-Control"] = "no-store"
    scan = record(body.scan_id, "scan.json")
    if body.candidate >= len(scan["candidates"]):
        raise HTTPException(422, "Choose a location from the search results.")
    point = scan["candidates"][body.candidate]
    if body.report_id:
        report = record(body.report_id, "site_report.json")
        if not same_point(point, report.get("location", {})):
            raise HTTPException(422, "The estimate does not match the selected location.")
        return {"status": "succeeded", "result": result_for(body.report_id, report, body.inputs)}
    for identifier, report in records("site_report.json"):
        if same_point(point, report.get("location", {})) and report.get("status") == "research_transfer_exposure" and report.get("coverage", {}).get("status") in {"historical_spp_match", "regional_spp_comparison"}:
            return {"status": "succeeded", "result": result_for(identifier, report, body.inputs)}
    return workspace_job({"kind": "site-transfer", "scan": body.scan_id, "candidate": body.candidate,
                          "load_mw": body.inputs.load_mw, "conditional_share": body.inputs.flexibility_percent / 100,
                          "site_exposure": body.inputs.site_exposure, "years": body.inputs.contract_years,
                          "confirm_spp": False})
