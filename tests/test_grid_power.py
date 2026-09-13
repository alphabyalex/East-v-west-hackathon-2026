"""Real published aggregates plus deliberate corruption; no invented demo data."""
import json
import shutil
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from api import grid_impact as grid, grid_power as power
from api.main import app


@pytest.mark.parametrize("zone,point,hours,rate", [
    ("LES", "LES_LES", 764, 7940.537), ("OKGE", "OKGE_OKGE", 305, 2061.7952),
    ("CSWS", "AEPM_CSWS", 28, 126.1577), ("OPPD", "OPPD_OPPD", 492, 4000.4187),
    ("SPS", "SPS_SPS", 468, 3047.9049), ("WFEC", "WFEC_WFEC", 179, 812.9409),
])
def test_real_prices_reconcile_without_hourly_reads(monkeypatch, zone, point, hours, rate):
    monkeypatch.delenv("FLUXLINE_GRID_IMPACT_DIR", raising=False)
    monkeypatch.setattr(grid.pd, "read_parquet", Mock(side_effect=AssertionError("No raw reads in HTTP")))
    monkeypatch.setattr(grid, "compose_grid_impact", Mock(side_effect=AssertionError("No composition in HTTP")))
    with TestClient(app) as client:
        response = client.get(f"/api/grid-impact/{zone}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    prices = result["cheap_power"]
    assert prices["source_location_id"] == point
    assert prices["proxy_hours"]["value"] == hours
    assert prices["unknown_hours"]["value"] == 73
    assert prices["usd_per_available_mw"]["value"] == pytest.approx(rate)
    assert prices["usd_per_available_mw"]["source_type"] == "assumption"
    assert power.INPUT_SHA in prices["input_source"]["ref"]
    assert sum(row["proxy_hours"]["value"] for row in prices["bins"]) == hours
    assert sum(row["usd_per_available_mw"]["value"] for row in prices["bins"]) == pytest.approx(rate)
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == hours * 100 * .5
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] is None
    assert result["carbon_absorbed_tonnes_in_observed_hours"]["value"] == 0


@pytest.mark.parametrize("zone", ["EDE", "GRDA", "INDN", "KACY", "KCPL", "MPS", "NPPD", "SECI", "SPRM", "WAUE", "WR", "SPP_SYSTEM", "spp-wichita-demo", "spp-oklahoma-city-demo", "spp-lincoln-demo"])
def test_unavailable_never_invents_price(monkeypatch, zone):
    monkeypatch.delenv("FLUXLINE_GRID_IMPACT_DIR", raising=False)
    with TestClient(app) as client:
        result = client.get(f"/api/grid-impact/{zone}").json()
    assert result["cheap_power"]["status"] == "unavailable"
    assert result["cheap_power"]["bins"] == []
    assert "usd_per_available_mw" not in result["cheap_power"]


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXLINE_GRID_IMPACT_DIR", str(tmp_path))
    for name in ("LES.snapshot.json", "LES_LES.snapshot.json", power.FILENAME):
        shutil.copyfile(grid.LIVE_SNAPSHOT_DIRECTORY / name, tmp_path / name)
    return tmp_path


@pytest.mark.parametrize("damage", ["missing", "stale", "checksum", "duplicate", "negative", "fake_zero_hour_value", "source", "total"])
def test_missing_stale_and_corrupt_price_evidence(bundle, damage):
    path = bundle / power.FILENAME
    if damage == "missing":
        path.unlink()
    else:
        document = json.loads(path.read_bytes())
        result = document["result"]["locations"]["LES_LES"]
        if damage == "stale": result["snapshot_sha256"] = "older snapshot"
        if damage == "duplicate": result["bins"][1]["hour_utc"]["value"] = 0
        if damage == "negative": result["bins"][0]["usd_per_available_mw"]["value"] = -1
        if damage == "fake_zero_hour_value":
            empty = next(row for row in result["bins"] if row["proxy_hours"]["value"] == 0)
            empty["usd_per_available_mw"]["value"] = 123
        if damage == "source": result["proxy_hours"]["source_type"] = "data"
        if damage == "total": result["usd_per_available_mw"]["value"] += 1
        if damage != "checksum": document["sha256"] = grid._digest(document["result"])
        else: document["sha256"] = "broken"
        path.write_text(json.dumps(document), encoding="utf-8")
    with TestClient(app) as client:
        response = client.get("/api/grid-impact/LES")
    if damage in {"missing", "stale"}:
        assert response.status_code == 200
        assert response.json()["cheap_power"]["status"] == "unavailable"
        assert response.json()["wind_absorption_mwh_in_observed_hours"]["value"] == 38200
    else:
        assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


def test_compiler_reproduces_committed_artifact_offline(tmp_path):
    if not power.INPUT.exists():
        pytest.skip("Optional raw hourly cache is not shipped; serving tests use committed aggregates")
    for name in json.loads((grid.LIVE_SNAPSHOT_DIRECTORY / power.FILENAME).read_bytes())["result"]["locations"]:
        shutil.copyfile(grid.LIVE_SNAPSHOT_DIRECTORY / f"{name}.snapshot.json", tmp_path / f"{name}.snapshot.json")
    power.compile_power(tmp_path)
    assert (tmp_path / power.FILENAME).read_bytes() == (grid.LIVE_SNAPSHOT_DIRECTORY / power.FILENAME).read_bytes()
    with pytest.raises(FileExistsError):
        power.compile_power(tmp_path)
