"""Replay source lifecycle regressions using copied modules and synthetic data."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import uuid

import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from pipeline import wind_signal as implementation

SOURCE = {"source_type": "assumption", "ref": "synthetic source lifecycle fixture; no fitted or measured values"}
SOURCES = {name: dict(SOURCE) for name in ("system_wind_mw", "system_load_mw")}


@pytest.fixture(autouse=True)
def no_fitting_or_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Source lifecycle test cannot fit or fetch")
    monkeypatch.setattr(LogisticRegression, "fit", reject)
    monkeypatch.setattr(StandardScaler, "fit", reject)
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def load_copy(path):
    name = "wind_source_lifecycle_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name)
    return module


@pytest.fixture
def wind(tmp_path, monkeypatch):
    path = tmp_path / "copied_module.py"
    path.write_bytes(Path(implementation.__file__).read_bytes())
    module = load_copy(path)
    def reject(*args, **kwargs):
        raise AssertionError("Source lifecycle test cannot fit or fetch")
    monkeypatch.setattr(module, "fit_wind_event_classifier", reject)
    monkeypatch.setattr(module.ingest, "fetch_public_evidence", reject)
    return module


@pytest.fixture
def arguments(tmp_path, wind):
    bundle = {
        "schema_version": wind.WIND_CLASSIFIER_SCHEMA,
        "features": list(wind.WIND_CLASSIFIER_FEATURES),
        "standardizer": {"mean": [0.] * 4, "scale": [1.] * 4},
        "base_model": {"coefficients": [0.] * 4, "intercept": 0.},
        "calibrator": None,
        "manifest": {
            "system_scope": "SPP_SYSTEM", "forecast_asof_verified": False,
            "status": "research_only_no_production_promotion",
            "target_method": "reported_system_wind_curtailment_any_category_v1",
            "split_bounds": {name: list(bounds) for name, bounds in wind.WIND_CLASSIFIER_SPLITS.items()},
            "limitation": wind.WIND_CLASSIFIER_LIMITATION,
            "calibration_minimum_per_class": 20, "calibration_requested": False,
            "calibration_status": "disabled_by_declared_policy",
            "training_cohort_sha256": hashlib.sha256(b"synthetic schema only; no fitted cohort").hexdigest(),
            "calibration_cohort_sha256": None,
            "fit_parameters": {"C": 1., "solver": "lbfgs", "class_weight": None,
                               "max_iter": 2000, "tol": 1e-8, "random_state": 2026},
            "policy_source": dict(SOURCE),
            "input_sources": {**deepcopy(SOURCES), "labels": dict(SOURCE)},
        },
    }
    bundle["model_id"] = hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    times = pd.date_range("2024-12-31", periods=48, freq="h", tz="UTC")
    hourly = pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM",
                           "system_wind_mw": 50., "system_load_mw": 100.})
    hourly.attrs["sources"] = deepcopy(SOURCES)
    labels = pd.DataFrame({"timestamp_utc": times[24:], "location_id": "SPP_SYSTEM",
                           "wind_curtailment_event": pd.array([False, True] * 12, dtype="boolean"),
                           "observed_five_minute_samples": 12, "evaluable_five_minute_samples": 12})
    labels.attrs = {"method": "reported_system_wind_curtailment_any_category_v1",
                    "system_scope": "SPP_SYSTEM", "source": dict(SOURCE)}
    paths = {role: tmp_path / filename for role, filename in {
        "bundle": "authored-model.json", "hourly": "observations.parquet",
        "labels": "targets.parquet", "baseline": "declared-comparator.json"}.items()}
    paths["bundle"].write_text(json.dumps(bundle), encoding="utf-8")
    paths["baseline"].write_text(json.dumps({"value": .5, **SOURCE}), encoding="utf-8")
    hourly.to_parquet(paths["hourly"], index=False)
    labels.to_parquet(paths["labels"], index=False)
    return {**{name + "_path": value for name, value in paths.items()},
            "start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2025-01-02T00:00:00Z",
            "output_dir": tmp_path / "new-publication"}


def test_source_edited_after_import_rejects_before_evaluation(wind, arguments, monkeypatch):
    path = Path(wind.__file__)
    path.write_bytes(path.read_bytes() + b"\n# Synthetic disk update after import.\n")
    calls = []
    original = wind.evaluate_wind_event_classifier
    def spy(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(wind, "evaluate_wind_event_classifier", spy)
    with pytest.raises(ValueError, match="changed after module import"):
        wind.publish_wind_replay(**arguments)
    assert calls == []
    assert not arguments["output_dir"].exists()


def test_source_identity_check_precedes_input_reads(wind, tmp_path):
    path = Path(wind.__file__)
    path.write_bytes(path.read_bytes() + b"\n# Synthetic pre-call update.\n")
    arguments = {role + "_path": tmp_path / (role + ".missing")
                 for role in ("bundle", "hourly", "labels", "baseline")}
    with pytest.raises(ValueError, match="changed after module import"):
        wind.publish_wind_replay(**arguments, start_utc="2025-01-01T00:00:00Z",
            end_exclusive_utc="2025-01-02T00:00:00Z", output_dir=tmp_path / "new-publication")
    assert not (tmp_path / "new-publication").exists()


def test_source_changed_during_evaluation_still_rejects(wind, arguments, monkeypatch):
    original = wind.evaluate_wind_event_classifier
    def update_during_evaluation(*args, **kwargs):
        result = original(*args, **kwargs)
        path = Path(wind.__file__)
        path.write_bytes(path.read_bytes() + b"\n# Synthetic during-call update.\n")
        return result
    monkeypatch.setattr(wind, "evaluate_wind_event_classifier", update_during_evaluation)
    with pytest.raises(ValueError, match="changed during evaluation"):
        wind.publish_wind_replay(**arguments)
    assert not arguments["output_dir"].exists()


def test_unchanged_import_preserves_inputs_hashes_and_reproducibility(wind, arguments, tmp_path):
    originals = {name: arguments[name + "_path"].read_bytes() for name in ("bundle", "hourly", "labels", "baseline")}
    manifest = wind.publish_wind_replay(**arguments)
    assert manifest["source_file_sha256"] == hashlib.sha256(Path(wind.__file__).read_bytes()).hexdigest()
    for role, payload in originals.items():
        assert arguments[role + "_path"].read_bytes() == payload
        assert (arguments["output_dir"] / manifest["inputs"][role]).read_bytes() == payload
    second = tmp_path / "second-publication"
    assert wind.publish_wind_replay(**{**arguments, "output_dir": second}) == manifest
    for name in [*manifest["files"], "manifest.json"]:
        assert (arguments["output_dir"] / name).read_bytes() == (second / name).read_bytes()


def test_fresh_import_of_edited_source_can_publish_that_revision(wind, arguments):
    path = Path(wind.__file__)
    old = b'SPP describes GenMix Self columns as end-of-dispatch MW targets.'
    new = b'SYNTHETIC NEW IMPORT: SPP describes GenMix Self columns as end-of-dispatch MW targets.'
    raw = path.read_bytes()
    assert raw.count(old) == 1
    path.write_bytes(raw.replace(old, new))
    fresh = load_copy(path)
    manifest = fresh.publish_wind_replay(**arguments)
    assert manifest["source_file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    report = json.loads((arguments["output_dir"] / "report.json").read_bytes())
    assert "SYNTHETIC NEW IMPORT" in report["source_qualification"]["text"]
