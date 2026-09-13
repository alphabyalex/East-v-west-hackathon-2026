"""Offline publication commands must never turn missing artifacts into success."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pandas as pd
import pytest

from api import grid_impact
from pipeline.wind_signal import summarize_wind


def prepared_input(path, *, location="NODE", unavailable=False):
    origin = {"source_type": "assumption", "ref": "synthetic offline CLI test fixture"}
    frame = pd.DataFrame({
        "timestamp_utc": pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"),
        "location_id": location, "system_wind_mw": 60.,
        "system_load_mw": 100., "lmp_usd_mwh": -1.,
    })
    summary = None if unavailable else summarize_wind(
        frame, sources={key: origin for key in ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")},
        wind_scope="SPP_SYSTEM", load_scope="SPP_SYSTEM",
        flexible_load_mw={"value": 100., **origin},
        available_fraction={"value": .5, **origin},
    )[0]
    row = {"location_id": location, "wind_summary": summary,
           "carbon_shift": None, "shift_coverage": None}
    payload = {"schema_version": "grid-impact-inputs-v1", "locations": [row]}
    path.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    return row


def compile_args(input_path, output_path, location="NODE"):
    return ["compile", "--input", str(input_path), "--location-id", location,
            "--output", str(output_path)]


def check_args(path, location="NODE"):
    return ["check", "--snapshot", str(path), "--location-id", location]


def test_cli_compiles_exact_sourced_result_without_modifying_inputs(tmp_path, capsys):
    input_path, output_path = tmp_path / "prepared.json", tmp_path / "published.json"
    row = prepared_input(input_path)
    before = input_path.read_bytes()
    with patch("socket.create_connection", side_effect=AssertionError("offline means no network")):
        assert grid_impact.main(compile_args(input_path, output_path)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "compiled"
    assert summary["location_id"] == "NODE"
    assert input_path.read_bytes() == before
    result = grid_impact.read_grid_impact_snapshot("NODE", output_path)
    assert result == grid_impact.compose_grid_impact(**row)
    assert result["wind_absorption_mwh_in_observed_hours"]["value"] == 200
    assert result["wind_absorption_mwh_per_year"]["value"] is None
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] is None
    assert all(result[key]["source_type"] == "assumption" for key in grid_impact.UNITS)
    assert summary["result_sha256"] == json.loads(output_path.read_text())["result_sha256"]


def test_cli_check_is_read_only_and_never_recomposes(tmp_path, capsys):
    input_path, output_path = tmp_path / "prepared.json", tmp_path / "published.json"
    prepared_input(input_path)
    grid_impact.main(compile_args(input_path, output_path))
    capsys.readouterr()
    before = output_path.read_bytes()
    with patch.object(grid_impact, "compose_grid_impact", side_effect=AssertionError("no preparation on check")):
        assert grid_impact.main(check_args(output_path)) == 0
    assert output_path.read_bytes() == before
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "validated"
    assert summary["result_sha256"] == json.loads(before)["result_sha256"]


@pytest.mark.parametrize("failure", ["missing_file", "missing_location", "duplicate_location", "duplicate_json_key", "bad_summary"])
def test_cli_invalid_input_never_publishes_fallback(tmp_path, capsys, failure):
    input_path, output_path = tmp_path / "prepared.json", tmp_path / "new" / "published.json"
    prepared_input(input_path)
    if failure == "missing_file":
        input_path = tmp_path / "absent.json"
    elif failure == "missing_location":
        prepared_input(input_path, location="OTHER")
    elif failure == "duplicate_json_key":
        input_path.write_text('{"schema_version":"grid-impact-inputs-v1","locations":[],"locations":[]}', encoding="utf-8")
    else:
        payload = json.loads(input_path.read_text())
        if failure == "duplicate_location":
            payload["locations"].append(payload["locations"][0])
        else:
            payload["locations"][0]["wind_summary"]["proxy_hours"]["value"] = 100
        input_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        grid_impact.main(compile_args(input_path, output_path))
    assert error.value.code == 2
    assert not output_path.parent.exists()
    assert capsys.readouterr().err


def test_cli_existing_destination_is_preserved(tmp_path):
    input_path, output_path = tmp_path / "prepared.json", tmp_path / "published.json"
    prepared_input(input_path)
    output_path.write_bytes(b"existing teammate work")
    with pytest.raises(SystemExit) as error:
        grid_impact.main(compile_args(input_path, output_path))
    assert error.value.code == 2
    assert output_path.read_bytes() == b"existing teammate work"


@pytest.mark.parametrize("failure", ["missing", "wrong_location", "checksum", "malformed"])
def test_cli_check_returns_failure_for_missing_or_invalid_snapshot(tmp_path, failure):
    path = tmp_path / "snapshot.json"
    if failure != "missing":
        grid_impact.compile_grid_impact_snapshot("NODE", path)
        if failure == "checksum":
            payload = json.loads(path.read_text())
            payload["result_sha256"] = "0" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
        elif failure == "malformed":
            path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        grid_impact.main(check_args(path, "OTHER" if failure == "wrong_location" else "NODE"))
    assert error.value.code == 2


def test_cli_explicit_unavailable_record_is_valid_but_not_missing_fallback(tmp_path, capsys):
    input_path, output_path = tmp_path / "prepared.json", tmp_path / "published.json"
    prepared_input(input_path, unavailable=True)
    assert grid_impact.main(compile_args(input_path, output_path)) == 0
    assert grid_impact.main(check_args(output_path)) == 0
    result = grid_impact.read_grid_impact_snapshot("NODE", output_path)
    assert all(result[field]["value"] is None for field in grid_impact.UNITS)
    assert "unavailable" in json.dumps(result["coverage"])


def test_cli_check_does_not_report_success_when_snapshot_disappears_during_read(tmp_path):
    path = tmp_path / "snapshot.json"
    grid_impact.compile_grid_impact_snapshot("NODE", path)
    with patch.object(Path, "read_bytes", side_effect=FileNotFoundError("snapshot removed during read")):
        with pytest.raises(SystemExit) as error:
            grid_impact.main(check_args(path))
    assert error.value.code == 2


def test_required_snapshot_read_is_explicit_while_default_fallback_is_preserved(tmp_path):
    path = tmp_path / "absent.json"
    with pytest.raises(FileNotFoundError):
        grid_impact.read_grid_impact_snapshot("NODE", path, required=True)
    assert grid_impact.read_grid_impact_snapshot("NODE", path)["wind_absorption_mwh_per_year"]["value"] is None
    with pytest.raises(ValueError, match="required must be boolean"):
        grid_impact.read_grid_impact_snapshot("NODE", path, required="yes")


def test_python_module_entrypoint_exposes_only_offline_actions():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "api.grid_impact", "--help"], cwd=root,
                            capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "compile" in result.stdout and "check" in result.stdout
    assert "offline" in result.stdout.lower()
