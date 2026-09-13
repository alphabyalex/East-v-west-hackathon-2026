"""HTTP reads immutable snapshots; authored fixtures never stand in for real evidence."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from api import grid_impact as impact
from api import main as server
from pipeline.carbon import BOUNDARY, shift_carbon
from pipeline.wind_signal import summarize_wind


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXLINE_GRID_IMPACT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "grid_impact_cache", impact.GridImpactSnapshotCache(max_entries=32))
    return tmp_path


@pytest.fixture
def client():
    with TestClient(server.app) as instance:
        yield instance


def datum(value):
    return {"value": value, "source_type": "assumption", "ref": "authored HTTP software fixture, not real grid data"}


def publish(bundle, *, hours=2, price=-1., with_shift=False, intensity=300.):
    timestamps = pd.date_range("2025-01-01", periods=hours, freq="h", tz="UTC")
    frame = pd.DataFrame({"timestamp_utc": timestamps, "location_id": "TEST_ZONE",
                          "system_wind_mw": 60., "system_load_mw": 100., "lmp_usd_mwh": price})
    origin = {key: datum(0)[key] for key in ("source_type", "ref")}
    summary = summarize_wind(frame,
        sources={key: origin for key in ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")},
        wind_scope="SPP_SYSTEM", load_scope="SPP_SYSTEM",
        flexible_load_mw=datum(100.), available_fraction=datum(.5))[0]
    shifted, coverage = None, None
    if with_shift:
        risk, makeup = (time.isoformat() for time in timestamps[:2])
        shifted = shift_carbon([
            {"timestamp_utc": risk, "boundary": BOUNDARY, "intensity_kg_co2_per_mwh": datum(100.)},
            {"timestamp_utc": makeup, "boundary": BOUNDARY, "intensity_kg_co2_per_mwh": datum(intensity)},
        ], [{"risk_hour": risk, "makeup_hour": makeup, "mwh": datum(10.)}],
            selection_source=origin,
            hourly_limits={risk: {"removable_mwh": datum(10.)}, makeup: {"makeup_capacity_mwh": datum(10.)}})
        coverage = {key: summary[key] for key in (*impact.COUNT_KEYS, "period_start_utc", "period_end_exclusive_utc")}
        coverage["schedule_scope"] = "complete_period"
    return impact.compile_grid_impact_snapshot("TEST_ZONE", bundle / "TEST_ZONE.snapshot.json",
        wind_summary=summary, carbon_shift=shifted, shift_coverage=coverage)


def assert_sources(result):
    for field in impact.UNITS:
        assert set(result[field]) == {"value", "source_type", "ref"}
        assert result[field]["source_type"] == "assumption"
        assert result[field]["ref"].startswith(impact.EVIDENCE_PREFIX)
        assert result[field]["ref"].removeprefix(impact.EVIDENCE_PREFIX) in result["evidence"]
    assert result["units"] == impact.UNITS
    assert result["basis"] == impact.BASIS


def test_complete_authored_snapshot_is_served_exactly_without_composition(bundle, client, monkeypatch):
    path = publish(bundle, hours=8760, with_shift=True)
    expected = impact.read_grid_impact_snapshot("TEST_ZONE", path, required=True)
    original = path.read_bytes()
    forbidden = Mock(side_effect=AssertionError("Heavy work must stay offline"))
    for name in ("compose_grid_impact", "get_location_grid_impact", "compile_grid_impact_snapshot", "wind_carbon", "_shift_total"):
        monkeypatch.setattr(impact, name, forbidden)
    monkeypatch.setattr("pipeline.wind_signal.summarize_wind", forbidden)
    monkeypatch.setattr("pipeline.carbon.fuel_mix_intensity", forbidden)
    validation = Mock(wraps=impact._validate_snapshot_result)
    monkeypatch.setattr(impact, "_validate_snapshot_result", validation)
    for _ in range(2):
        response = client.get("/api/grid-impact/TEST_ZONE", headers={"Origin": "http://127.0.0.1:5174"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"
        assert response.json() == expected
    assert validation.call_count == 1
    forbidden.assert_not_called()
    assert path.read_bytes() == original
    assert expected["wind_absorption_mwh_per_year"]["value"] == 438000.
    assert expected["carbon_shifted_tonnes_per_year"]["value"] == -2.
    assert_sources(expected)
    assert "authored HTTP software fixture" in json.dumps(expected["evidence"])


@pytest.mark.parametrize("case", ["no_inputs", "unknown_wind", "unknown_intensity", "partial"])
def test_incomplete_snapshot_preserves_nulls_and_coverage(bundle, client, case):
    if case == "no_inputs":
        path = impact.compile_grid_impact_snapshot("TEST_ZONE", bundle / "TEST_ZONE.snapshot.json")
    else:
        path = publish(bundle, price=float("nan") if case == "unknown_wind" else -1.,
                       with_shift=case == "unknown_intensity", intensity=None)
    response = client.get("/api/grid-impact/TEST_ZONE")
    assert response.status_code == 200
    result = response.json()
    assert result == impact.read_grid_impact_snapshot("TEST_ZONE", path, required=True)
    assert_sources(result)
    assert all(result[field]["value"] is None for field in impact.UNITS if field.endswith("per_year"))
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] is None
    if case in {"no_inputs", "unknown_wind"}:
        assert all(result[field]["value"] is None for field in impact.UNITS)
    else:
        assert result["wind_absorption_mwh_in_observed_hours"]["value"] == 100.
        assert result["carbon_absorbed_tonnes_in_observed_hours"]["value"] == 0.
    assert result["coverage"]["wind"]["status"] == ("unavailable" if case == "no_inputs" else "partial_period")


def test_missing_snapshot_is_404_without_manufacturing_an_unavailable_snapshot(bundle, client):
    response = client.get("/api/grid-impact/UNKNOWN_ZONE")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert list(bundle.iterdir()) == []


@pytest.mark.parametrize("case", ["json", "checksum", "location", "source_upgrade", "directory"])
def test_invalid_snapshot_is_503_never_a_numeric_fallback(bundle, client, case):
    path = publish(bundle)
    if case == "directory":
        path.unlink()
        path.mkdir()
    elif case == "json":
        path.write_text("not JSON", encoding="utf-8")
    else:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if case == "checksum":
            envelope["result_sha256"] = "0" * 64
        elif case == "location":
            envelope["location_id"] = "OTHER"
        else:
            envelope["result"]["wind_absorption_mwh_in_observed_hours"]["source_type"] = "data"
            envelope["result_sha256"] = impact._digest(envelope["result"])
        path.write_text(json.dumps(envelope), encoding="utf-8")
    response = client.get("/api/grid-impact/TEST_ZONE")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"detail"}


def test_deleted_warm_snapshot_is_not_served_from_cache(bundle, client):
    path = publish(bundle)
    assert client.get("/api/grid-impact/TEST_ZONE").status_code == 200
    path.unlink()
    assert client.get("/api/grid-impact/TEST_ZONE").status_code == 404


@pytest.mark.parametrize("identifier", ["%20LES", "LES%20", "LES%5Csecret", "LES.json", "A" * 129])
def test_invalid_identifiers_are_rejected_before_reading(bundle, client, monkeypatch, identifier):
    reader = Mock(side_effect=AssertionError("Invalid paths must not reach the reader"))
    monkeypatch.setattr(server, "read_live_grid_impact", reader)
    assert client.get(f"/api/grid-impact/{identifier}").status_code == 422
    reader.assert_not_called()


@pytest.mark.parametrize("identifier", ["../LES", "..\\LES", "/LES", "LES/OTHER"])
def test_direct_live_reader_rejects_paths(bundle, identifier):
    with pytest.raises(ValueError, match="identifier"):
        impact.read_live_grid_impact(identifier)


PUBLISHED_POINTS = {
    "AEPM_CSWS": 1400., "LES_LES": 38200., "OKGE_OKGE": 15250., "OPPD_OPPD": 24600.,
    "SPPNORTH_HUB": 29750., "SPPSOUTH_HUB": 35850., "SPS_SPS": 23400., "WFEC_WFEC": 8950.,
}


@pytest.fixture
def published_bundle(monkeypatch):
    # These selected artifacts ship with the endpoint; no ignored research cache
    # or prepared-input file is needed for this real snapshot regression.
    path = Path(__file__).resolve().parents[1] / "data/processed/grid_impact/live_v1"
    monkeypatch.setenv("FLUXLINE_GRID_IMPACT_DIR", str(path))
    monkeypatch.setattr(server, "grid_impact_cache", impact.GridImpactSnapshotCache(max_entries=32))
    return path


@pytest.mark.parametrize("point,expected_mwh", PUBLISHED_POINTS.items())
def test_published_real_point_scenario_preserves_its_assumptions(published_bundle, client, point, expected_mwh):
    response = client.get(f"/api/grid-impact/{point}")
    assert response.status_code == 200
    result = response.json()
    assert_sources(result)
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == expected_mwh
    assert result["carbon_absorbed_tonnes_in_observed_hours"]["value"] == 0.
    assert all(result[field]["value"] is None for field in impact.UNITS
               if field.endswith("per_year") or field.startswith("carbon_shifted"))
    assert result["coverage"]["wind"]["observed_hours"]["value"] == 8760
    assert result["coverage"]["wind"]["evaluable_hours"]["value"] == 8687
    assert result["coverage"]["wind"]["unknown_hours"]["value"] == 73
    context = result["evidence"][result["evidence_context"]["wind"].removeprefix(impact.EVIDENCE_PREFIX)]
    assert context["scenario_inputs"]["flexible_load_mw"]["value"] == 100.
    assert context["scenario_inputs"]["available_fraction"]["value"] == .5
    assert "mock://" not in json.dumps(result)


def test_published_catalog_ids_are_explicitly_unavailable_without_point_aliases(published_bundle, client):
    manifest = json.loads((published_bundle / "manifest.json").read_text(encoding="utf-8"))
    catalog = Path(__file__).resolve().parents[1] / manifest["exposure_catalog"]["path"]
    assert hashlib.sha256(catalog.read_bytes()).hexdigest() == manifest["exposure_catalog"]["sha256"]
    ids = set(pd.read_parquet(catalog, columns=["location_id"])["location_id"])
    assert len(ids) == 21
    assert len(manifest["files"]) == 29
    for name, entry in manifest["files"].items():
        assert hashlib.sha256((published_bundle / name).read_bytes()).hexdigest() == entry["sha256"]
    for location in ids:
        response = client.get(f"/api/grid-impact/{location}")
        assert response.status_code == 200
        result = response.json()
        assert result["location_id"] == location
        assert_sources(result)
        assert all(result[field]["value"] is None for field in impact.UNITS)
        assert all(part["status"] == "unavailable" for part in result["coverage"].values())
