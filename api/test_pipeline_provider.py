"""The precomputed reader boundary must distinguish missing data from broken data."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api import pipeline_provider
from api.estimate import build_estimate
from api.mock_provider import LocationNotFoundError
from api.pipeline_provider import PipelineDataError, get_pipeline_location
from api.schemas import EstimateRequest
from pipeline.confidence_policy import CONFIDENCE_POLICY, confidence_from_evidence


LOCATION = "SPP_SPS_HUB"
CONFIDENCE_EVIDENCE = confidence_from_evidence(0.1, 1000, [])


@pytest.fixture
def payload():
    return {
        "location_id": LOCATION,
        "by_year": [
            {
                "year_offset": year,
                "p50_hours": 100.0 * year,
                "p90_hours": 200.0 * year,
                "p99_hours": 300.0 * year,
                "worst_contiguous_hours": 20.0 * year,
            }
            for year in range(1, 8)
        ],
        "confidence": {
            "level": "Low",
            "score": CONFIDENCE_EVIDENCE["score"],
            "n_similar_historical_hours": 1000,
        },
        "model_version": "test_precomputed_reader_v1",
    }


def write_manifests(parquet, payload):
    """Authored metadata for disposable software-test artifacts, never real data."""
    policy = {
        "operator": "SPP", "label_method": "observed_event",
        "data_ref": "Software-test input provenance; not an SPP performance result",
        "label_ref": "Software-test label coverage; not an observed event archive",
    }
    card = {
        "model_version": payload["model_version"],
        "status": "research_only_pending_label_and_calibration_review",
        "policy": policy,
        "input_hashes": {"hourly_sha256": "a" * 64, "policy_sha256": "b" * 64},
        "confidence": {payload["location_id"]: CONFIDENCE_EVIDENCE},
        "confidence_policy": CONFIDENCE_POLICY,
        "settings": {"ensemble_members": 15},
        "splits": {"test": {"start": "2023-01-01T00:00:00Z", "end": "2023-12-31T23:00:00Z", "rows": 8760}},
        "test_by_location": {payload["location_id"]: {"n_hours": 8760}},
    }
    simulation = {
        "status": "experimental_unvalidated_annual_tails",
        "method": "seasonal_joint_probability_and_randomized_residual_block_bootstrap",
        "policy": policy, "source_type": "model",
        "ref": f"pipeline/simulate.py model_version={payload['model_version']}",
        "site_exposure_applied": False, "simulations": 2000,
        "years": len(payload["by_year"]), "seed": 2026, "block_hours": 168,
        "hours_per_year": 8760,
        "confidence_policy": CONFIDENCE_POLICY,
    }
    for name, document in (("model_card.json", card), ("simulation_metadata.json", simulation)):
        parquet.with_name(name).write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def install_reader(monkeypatch, tmp_path, payload):
    # Only the adapter's existence gate touches this file. The reader is a stub;
    # no parquet engine, training dependency, or external data pull is involved.
    parquet = tmp_path / "exposure_by_location.parquet"
    parquet.touch()
    write_manifests(parquet, payload)
    monkeypatch.setattr(pipeline_provider, "PARQUET_PATH", parquet)

    def install(reader, **exports):
        module = SimpleNamespace(get_location_estimate=reader, **exports)
        importer = Mock(return_value=module)
        monkeypatch.setattr(pipeline_provider, "import_module", importer)
        return importer, parquet

    return install


def assert_placeholder(location, reason):
    sources = [location.source, location.confidence.source]
    sources.extend(trigger.source for trigger in location.tariff.curtailment_triggers)
    assert sources
    for source in sources:
        assert source.source_type == "assumption"
        assert "placeholder" in source.ref.lower()
    assert "pipeline not wired yet" in location.source.ref
    assert "pipeline not wired yet" in location.confidence.source.ref
    assert reason in location.source.ref
    assert reason in location.confidence.source.ref
    assert [row.year_offset for row in location.by_year] == list(range(1, 8))


def test_imports_reader_and_preserves_precomputed_values_and_provenance(payload, install_reader):
    original = deepcopy(payload)
    reader = Mock(return_value=payload)
    importer, _ = install_reader(reader)

    location = get_pipeline_location(LOCATION)

    importer.assert_called_once_with("pipeline.simulate")
    reader.assert_called_once_with(LOCATION, path=pipeline_provider.PARQUET_PATH)
    assert payload == original
    for actual, expected in zip(location.by_year, payload["by_year"], strict=True):
        assert vars(actual) == expected
    assert location.source.source_type == "model"
    assert "test_precomputed_reader_v1" in location.source.ref
    assert "data/processed/exposure_by_location.parquet" in location.source.ref
    assert location.confidence.level == "Low"
    assert location.confidence.score == payload["confidence"]["score"]
    assert location.confidence.basis == "ensemble_agreement_and_historical_support"
    assert location.confidence.source.source_type == "model"
    assert "n_similar_historical_hours=1000" in location.confidence.source.ref
    assert "experimental_unvalidated_annual_tails" in location.source.ref
    assert "p99 of annual longest modeled episodes, not a guaranteed maximum" in location.source.ref
    assert "numeric score combines classifier ensemble agreement with same-location historical support" in location.confidence.source.ref
    assert "confidence_policy_version=2" in location.confidence.source.ref
    assert "not annual-tail coverage" in location.confidence.source.ref
    # A real exposure reader does not turn unpublished tariff extraction into fact.
    assert all(trigger.source.source_type == "assumption" for trigger in location.tariff.curtailment_triggers)


def test_site_factor_is_applied_once_downstream_of_the_reader(payload, install_reader):
    reader = Mock(return_value=payload)
    install_reader(reader)
    request = EstimateRequest(
        location_id=LOCATION, load_mw=100, term_years=2,
        flexibility_split=0.6, site_exposure=0.25, vpp_solar_homes=0,
    )

    result = build_estimate(request, get_pipeline_location)

    reader.assert_called_once_with(LOCATION, path=pipeline_provider.PARQUET_PATH)
    assert result.modeled_exposure.by_year[0].p50 == 25
    assert result.modeled_exposure.by_year[1].p50 == 50
    assert result.modeled_exposure.p50 == 37.5
    assert result.modeled_exposure.p90 == 75
    assert result.modeled_exposure.p99 == 112.5
    assert result.modeled_exposure.worst_contiguous_outage_hours == 10
    assert result.confidence.score == payload["confidence"]["score"]
    assert result.modeled_exposure.source.source_type == "model"
    assert result.economics.source.source_type == "assumption"


@pytest.mark.parametrize("missing_name", ["pipeline", "pipeline.simulate"])
def test_missing_reader_module_returns_explicit_placeholder(monkeypatch, missing_name):
    importer = Mock(side_effect=ModuleNotFoundError("reader absent", name=missing_name))
    monkeypatch.setattr(pipeline_provider, "import_module", importer)
    assert_placeholder(get_pipeline_location(LOCATION), "pipeline.simulate is absent")


@pytest.mark.parametrize("reader", [None, "unfinished", 3])
def test_absent_callable_returns_explicit_placeholder(install_reader, reader):
    install_reader(reader)
    assert_placeholder(get_pipeline_location(LOCATION), "get_location_estimate is absent")


def test_absent_reader_attribute_returns_explicit_placeholder(monkeypatch):
    monkeypatch.setattr(pipeline_provider, "import_module", Mock(return_value=SimpleNamespace()))
    assert_placeholder(get_pipeline_location(LOCATION), "get_location_estimate is absent")


def test_reader_import_with_absent_precomputed_file_is_explicit_placeholder(monkeypatch):
    monkeypatch.setattr(pipeline_provider, "import_module", Mock(side_effect=FileNotFoundError("parquet absent")))
    assert_placeholder(get_pipeline_location(LOCATION), "precomputed file is unavailable")


def test_default_http_dependency_calls_real_reader(payload, install_reader):
    from fastapi.testclient import TestClient
    from api.main import app
    reader = Mock(return_value=payload)
    install_reader(reader)
    request = {
        "location_id": LOCATION, "load_mw": 100, "term_years": 2,
        "flexibility_split": 0.6, "site_exposure": 0.25,
        "vpp_solar_homes": 0,
    }
    with TestClient(app) as client:
        response = client.post("/api/estimate", json=request, headers={"Origin": "http://127.0.0.1:5174"})
    assert response.status_code == 200
    reader.assert_called_once_with(LOCATION, path=pipeline_provider.PARQUET_PATH)
    assert response.json()["modeled_exposure"]["p50"] == 37.5
    assert response.headers["x-headroom-exposure-source"] == "pipeline"
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"


def test_missing_parquet_returns_placeholder_without_calling_reader(payload, install_reader):
    reader = Mock(return_value=payload)
    _, parquet = install_reader(reader)
    parquet.unlink()
    assert_placeholder(get_pipeline_location(LOCATION), "exposure_by_location.parquet is absent")
    reader.assert_not_called()


def test_parquet_disappearing_during_read_returns_explicit_placeholder(install_reader):
    reader = Mock(side_effect=FileNotFoundError("precomputed file moved"))
    install_reader(reader)
    assert_placeholder(get_pipeline_location(LOCATION), "precomputed file is unavailable")
    reader.assert_called_once_with(LOCATION, path=pipeline_provider.PARQUET_PATH)


def test_newly_available_reader_is_discovered_on_the_next_call(payload, install_reader):
    reader = Mock(return_value=payload)
    importer, _ = install_reader(reader)
    importer.side_effect = [
        ModuleNotFoundError("reader absent", name="pipeline.simulate"),
        SimpleNamespace(get_location_estimate=reader),
    ]
    assert_placeholder(get_pipeline_location(LOCATION), "pipeline.simulate is absent")
    assert get_pipeline_location(LOCATION).source.source_type == "model"
    reader.assert_called_once_with(LOCATION, path=pipeline_provider.PARQUET_PATH)


@pytest.mark.parametrize("error", [
    ModuleNotFoundError("missing parquet dependency", name="pyarrow"),
    ImportError("internal import failed"),
    RuntimeError("module initialization failed"),
])
def test_present_broken_import_is_an_error_not_a_placeholder(monkeypatch, error):
    monkeypatch.setattr(pipeline_provider, "import_module", Mock(side_effect=error))
    with pytest.raises(PipelineDataError) as caught:
        get_pipeline_location(LOCATION)
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("error", [
    RuntimeError("corrupt cached data"),
    PermissionError("cached file not readable"),
    ModuleNotFoundError("runtime parquet dependency missing", name="pyarrow"),
    KeyError("unexpected missing column"),
])
def test_present_broken_reader_is_an_error_not_a_placeholder(install_reader, error):
    install_reader(Mock(side_effect=error))
    with pytest.raises(PipelineDataError) as caught:
        get_pipeline_location(LOCATION)
    assert caught.value.__cause__ is error


def test_pipeline_location_not_found_maps_to_api_location_error(install_reader):
    class ReaderLocationNotFoundError(LookupError):
        pass

    error = ReaderLocationNotFoundError(LOCATION)
    install_reader(Mock(side_effect=error), LocationNotFoundError=ReaderLocationNotFoundError)
    with pytest.raises(LocationNotFoundError) as caught:
        get_pipeline_location(LOCATION)
    assert caught.value.args == (LOCATION,)
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("export", [None, "not an exception class", object])
def test_invalid_exported_error_type_does_not_disguise_reader_failure(install_reader, export):
    error = RuntimeError("reader failed")
    install_reader(Mock(side_effect=error), LocationNotFoundError=export)
    with pytest.raises(PipelineDataError) as caught:
        get_pipeline_location(LOCATION)
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("path,value", [
    (("location_id",), "SPP_DIFFERENT_LOCATION"),
    (("location_id",), "  "),
    (("model_version",), ""),
    (("model_version",), " \t"),
    (("model_version",), 123),
    (("by_year",), []),
    (("by_year", 0, "p50_hours"), -1),
    (("by_year", 0, "p50_hours"), float("nan")),
    (("by_year", 0, "p90_hours"), float("inf")),
    (("by_year", 0, "p99_hours"), float("-inf")),
    (("by_year", 0, "p90_hours"), 50),
    (("by_year", 0, "p99_hours"), 150),
    (("by_year", 0, "p99_hours"), 9000),
    (("by_year", 0, "worst_contiguous_hours"), -1),
    (("by_year", 0, "worst_contiguous_hours"), float("nan")),
    (("by_year", 0, "worst_contiguous_hours"), 9000),
    (("by_year", 0, "p50_hours"), "100"),
    (("by_year", 0, "year_offset"), 0),
    (("by_year", 0, "year_offset"), 8),
    (("by_year", 0, "year_offset"), 1.0),
    (("by_year", 0, "year_offset"), True),
    (("by_year", 1, "year_offset"), 1),
    (("by_year", 1, "year_offset"), 3),
    (("confidence", "score"), -0.1),
    (("confidence", "score"), 1.1),
    (("confidence", "score"), float("nan")),
    (("confidence", "score"), "0.8"),
    (("confidence", "level"), "certain"),
    (("confidence", "n_similar_historical_hours"), -1),
    (("confidence", "n_similar_historical_hours"), 1.5),
    (("confidence", "n_similar_historical_hours"), True),
    (("confidence", "n_similar_historical_hours"), "1000"),
    (("uncontracted_field",), "must not be silently discarded"),
])
def test_invalid_precomputed_fields_are_errors_not_placeholders(payload, install_reader, path, value):
    container = payload
    for key in path[:-1]:
        container = container[key]
    container[path[-1]] = value
    install_reader(Mock(return_value=payload))
    with pytest.raises(PipelineDataError, match="BUILD_PLAN"):
        get_pipeline_location(LOCATION)


@pytest.mark.parametrize("missing_field", ["location_id", "by_year", "confidence", "model_version"])
def test_missing_required_pipeline_fields_are_errors(payload, install_reader, missing_field):
    del payload[missing_field]
    install_reader(Mock(return_value=payload))
    with pytest.raises(PipelineDataError, match="BUILD_PLAN"):
        get_pipeline_location(LOCATION)


@pytest.mark.parametrize("malformed", [None, [], "not a precomputed record"])
def test_non_record_reader_output_is_an_error(install_reader, malformed):
    install_reader(Mock(return_value=malformed))
    with pytest.raises(PipelineDataError, match="BUILD_PLAN"):
        get_pipeline_location(LOCATION)


@pytest.mark.parametrize("name", ["model_card.json", "simulation_metadata.json"])
def test_absent_provenance_keeps_unverified_artifact_a_placeholder(payload, install_reader, name):
    reader = Mock(return_value=payload)
    _, parquet = install_reader(reader)
    sidecar = parquet.with_name(name)
    document = sidecar.read_text(encoding="utf-8")
    sidecar.unlink()

    assert_placeholder(get_pipeline_location(LOCATION), f"{name} absent")
    reader.assert_not_called()

    sidecar.write_text(document, encoding="utf-8")
    assert get_pipeline_location(LOCATION).source.source_type == "model"
    reader.assert_called_once_with(LOCATION, path=parquet)


@pytest.mark.parametrize("name,content", [
    ("model_card.json", "not json"),
    ("model_card.json", "[]"),
    ("simulation_metadata.json", "null"),
    ("simulation_metadata.json", "{"),
])
def test_corrupt_provenance_is_an_error_not_a_real_or_placeholder_result(payload, install_reader, name, content):
    _, parquet = install_reader(Mock(return_value=payload))
    parquet.with_name(name).write_text(content, encoding="utf-8")
    with pytest.raises(PipelineDataError, match="artifact provenance is invalid"):
        get_pipeline_location(LOCATION)


@pytest.mark.parametrize("name,path,value", [
    ("model_card.json", ("model_version",), "another_model_version"),
    ("model_card.json", ("status",), "validated_forecast"),
    ("model_card.json", ("policy", "operator"), "PJM"),
    ("model_card.json", ("policy", "label_method"), "load_above_mean"),
    ("model_card.json", ("policy", "label_ref"), ""),
    ("model_card.json", ("policy", "label_ref"), "placeholder, no reviewed labels"),
    ("model_card.json", ("policy", "data_ref"), "mock://invented-observations"),
    ("model_card.json", ("input_hashes",), {}),
    ("model_card.json", ("input_hashes", "hourly_sha256"), "not a hash"),
    ("model_card.json", ("confidence",), {}),
    ("model_card.json", ("confidence", LOCATION, "score"), 0.1),
    ("model_card.json", ("settings", "ensemble_members"), True),
    ("model_card.json", ("settings", "ensemble_members"), 1),
    ("simulation_metadata.json", ("policy", "label_method"), "binding_constraint"),
    ("simulation_metadata.json", ("status",), "validated"),
    ("simulation_metadata.json", ("method",), "invented_method"),
    ("simulation_metadata.json", ("source_type",), "assumption"),
    ("simulation_metadata.json", ("ref",), "pipeline/simulate.py model_version=another_model"),
    ("simulation_metadata.json", ("site_exposure_applied",), True),
    ("simulation_metadata.json", ("site_exposure_applied",), 0),
    ("simulation_metadata.json", ("simulations",), 999),
    ("simulation_metadata.json", ("simulations",), "2000"),
    ("simulation_metadata.json", ("years",), 6),
    ("simulation_metadata.json", ("seed",), None),
    ("simulation_metadata.json", ("seed",), True),
    ("simulation_metadata.json", ("block_hours",), 25),
    ("simulation_metadata.json", ("block_hours",), 192),
    ("simulation_metadata.json", ("hours_per_year",), 8784),
])
def test_inconsistent_or_unsupported_metadata_never_earns_model_provenance(payload, install_reader, name, path, value):
    _, parquet = install_reader(Mock(return_value=payload))
    sidecar = parquet.with_name(name)
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    container = document
    for key in path[:-1]:
        container = container[key]
    container[path[-1]] = value
    sidecar.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PipelineDataError, match="artifact provenance is invalid"):
        get_pipeline_location(LOCATION)


def test_annual_tail_confidence_cannot_be_upgraded_before_supported_metadata(payload, install_reader):
    payload["confidence"]["level"] = "High"
    install_reader(Mock(return_value=payload))
    with pytest.raises(PipelineDataError, match="capped Low"):
        get_pipeline_location(LOCATION)


@pytest.mark.parametrize("end,count,ready", [
    ("2023-12-31T23:00:00Z", 8760, True),
    ("2023-12-31T22:00:00Z", 8759, False),
    ("2023-12-31T23:00:00Z", 8759, False),
    ("2024-01-01T00:00:00Z", 8761, True),
])
def test_annual_reference_uses_inclusive_hourly_endpoints_and_local_count(payload, install_reader, end, count, ready):
    _, parquet = install_reader(Mock(return_value=payload))
    path = parquet.with_name("model_card.json")
    card = json.loads(path.read_text(encoding="utf-8"))
    card["splits"]["test"].update(end=end, rows=21 * count)
    card["test_by_location"][LOCATION]["n_hours"] = count
    path.write_text(json.dumps(card), encoding="utf-8")

    location = get_pipeline_location(LOCATION)
    if ready:
        assert location.source.source_type == "model"
        assert location.by_year[0].p50_hours == payload["by_year"][0]["p50_hours"]
        assert location.confidence.level == "Low"
    else:
        assert_placeholder(location, f"model_version={payload['model_version']}; annual reference not ready")
        assert f"local scored hours={count}" in location.source.ref


def test_published_72_day_reference_cannot_earn_annual_model_provenance(payload, install_reader):
    """Coverage recorded in the September 13 published multi-location model card."""
    payload["model_version"] = "spp_lgbm_20260913T055001_b63ee2577e"
    _, parquet = install_reader(Mock(return_value=payload))
    # install_reader writes the initial fixture before this test changes its version.
    write_manifests(parquet, payload)
    path = parquet.with_name("model_card.json")
    card = json.loads(path.read_text(encoding="utf-8"))
    card["splits"]["test"] = {
        "start": "2024-10-20 10:00:00+00:00", "end": "2024-12-31 23:00:00+00:00", "rows": 36057,
    }
    card["test_by_location"][LOCATION]["n_hours"] = 1717
    path.write_text(json.dumps(card), encoding="utf-8")

    location = get_pipeline_location(LOCATION)
    assert_placeholder(location, "model_version=spp_lgbm_20260913T055001_b63ee2577e; annual reference not ready")
    assert "held-out span=1742 hours" in location.source.ref
    assert "local scored hours=1717" in location.source.ref
    assert "missing seasons must not be substituted" in location.source.ref


@pytest.mark.parametrize("path", [
    ("splits",), ("splits", "test"), ("splits", "test", "start"), ("splits", "test", "end"),
    ("test_by_location",), ("test_by_location", LOCATION), ("test_by_location", LOCATION, "n_hours"),
])
def test_missing_annual_reference_is_explicitly_not_ready(payload, install_reader, path):
    _, parquet = install_reader(Mock(return_value=payload))
    card_path = parquet.with_name("model_card.json")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    container = card
    for key in path[:-1]:
        container = container[key]
    del container[path[-1]]
    card_path.write_text(json.dumps(card), encoding="utf-8")
    assert_placeholder(get_pipeline_location(LOCATION), f"model_version={payload['model_version']}; annual reference coverage missing")


@pytest.mark.parametrize("path,value", [
    (("splits",), []),
    (("splits", "test"), None),
    (("splits", "test", "start"), "not a timestamp"),
    (("splits", "test", "start"), "2023-01-01T00:00:00"),
    (("splits", "test", "start"), "2023-01-01T00:30:00Z"),
    (("splits", "test", "start"), None),
    (("splits", "test", "end"), "2022-12-31T00:00:00Z"),
    (("test_by_location",), []),
    (("test_by_location", LOCATION), None),
    (("test_by_location", LOCATION, "n_hours"), True),
    (("test_by_location", LOCATION, "n_hours"), "8760"),
    (("test_by_location", LOCATION, "n_hours"), -1),
    (("test_by_location", LOCATION, "n_hours"), 8761),
])
def test_corrupt_annual_reference_remains_an_error(payload, install_reader, path, value):
    _, parquet = install_reader(Mock(return_value=payload))
    card_path = parquet.with_name("model_card.json")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    container = card
    for key in path[:-1]:
        container = container[key]
    container[path[-1]] = value
    card_path.write_text(json.dumps(card), encoding="utf-8")
    with pytest.raises(PipelineDataError, match="artifact provenance is invalid"):
        get_pipeline_location(LOCATION)


def test_not_ready_annual_reference_returns_explicit_http_placeholder(payload, install_reader):
    from fastapi.testclient import TestClient
    from api.main import app

    _, parquet = install_reader(Mock(return_value=payload))
    path = parquet.with_name("model_card.json")
    card = json.loads(path.read_text(encoding="utf-8"))
    card["test_by_location"][LOCATION]["n_hours"] = 1717
    path.write_text(json.dumps(card), encoding="utf-8")
    with TestClient(app) as client:
        response = client.post("/api/estimate", json={
            "location_id": LOCATION, "load_mw": 100, "term_years": 2,
            "flexibility_split": 0.6, "site_exposure": 0.25,
        })
    assert response.status_code == 200
    assert response.headers["x-headroom-exposure-source"] == "placeholder"
    for key in ("modeled_exposure", "confidence"):
        source = response.json()[key]["source"]
        assert source["source_type"] == "assumption"
        assert f"model_version={payload['model_version']}; annual reference not ready" in source["ref"]


def test_invalid_metadata_returns_http_503(payload, install_reader):
    from fastapi.testclient import TestClient
    from api.main import app

    _, parquet = install_reader(Mock(return_value=payload))
    parquet.with_name("simulation_metadata.json").write_text("{}", encoding="utf-8")
    with TestClient(app) as client:
        response = client.post("/api/estimate", json={
            "location_id": LOCATION, "load_mw": 100, "term_years": 2,
            "flexibility_split": 0.6, "site_exposure": 0.25,
            "vpp_solar_homes": 0,
        })
    assert response.status_code == 503


def test_real_parquet_reader_round_trip_never_trains_simulates_or_fetches(payload, monkeypatch, tmp_path):
    """A real reader over disposable authored rows, not a real model result."""
    import pandas as pd
    import requests
    from fastapi.testclient import TestClient
    from pipeline import simulate, train
    from api.main import app

    parquet = tmp_path / "exposure_by_location.parquet"
    rows = [
        {
            **row, "location_id": LOCATION,
            "confidence_level": payload["confidence"]["level"],
            "confidence_score": payload["confidence"]["score"],
            "n_similar_historical_hours": payload["confidence"]["n_similar_historical_hours"],
            "model_version": payload["model_version"],
        }
        for row in payload["by_year"]
    ]
    pd.DataFrame(rows).to_parquet(parquet, index=False)
    write_manifests(parquet, payload)
    monkeypatch.setattr(pipeline_provider, "PARQUET_PATH", parquet)
    reader = Mock(wraps=simulate.get_location_estimate)
    monkeypatch.setattr(simulate, "get_location_estimate", reader)
    forbidden = Mock(side_effect=AssertionError("The estimate request must only read precomputed data"))
    monkeypatch.setattr(simulate, "simulate_exposure", forbidden)
    monkeypatch.setattr(train, "fit_ensemble", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)

    with TestClient(app) as client:
        response = client.post("/api/estimate", json={
            "location_id": LOCATION, "load_mw": 100, "term_years": 2,
            "flexibility_split": 0.6, "site_exposure": 0.25,
            "vpp_solar_homes": 0,
        })
        unavailable_location = client.post("/api/estimate", json={
            "location_id": "SPP_NOT_IN_TABLE", "load_mw": 100, "term_years": 2,
            "flexibility_split": 0.6, "site_exposure": 0.25,
            "vpp_solar_homes": 0,
        })
    assert response.status_code == 200
    assert response.json()["modeled_exposure"]["p50"] == 37.5
    assert response.json()["modeled_exposure"]["source"]["source_type"] == "model"
    assert response.json()["confidence"]["level"] == "Low"
    assert response.json()["confidence"]["score"] == payload["confidence"]["score"]
    assert "not annual-tail coverage" in response.json()["confidence"]["source"]["ref"]
    assert unavailable_location.status_code == 404
    reader.assert_any_call(LOCATION, path=parquet)
    forbidden.assert_not_called()


@pytest.mark.parametrize("name", ["model_card.json", "simulation_metadata.json"])
@pytest.mark.parametrize("policy", [None, {}, {"version": 1}, {**CONFIDENCE_POLICY, "medium_precedent_min": 1}])
def test_api_rejects_missing_stale_or_altered_confidence_policy(payload, install_reader, name, policy):
    _, parquet = install_reader(Mock(return_value=payload))
    path = parquet.with_name(name)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if policy is None:
        manifest.pop("confidence_policy")
    else:
        manifest["confidence_policy"] = policy
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineDataError, match="confidence policy"):
        get_pipeline_location(LOCATION)


@pytest.mark.parametrize("field,value", [
    ("score", .99), ("agreement_score", .99), ("historical_support_score", 1.0),
    ("mean_ensemble_probability_std", .01), ("n_similar_historical_hours", 0),
    ("n_similar_historical_hours", 1000.0), ("policy_version", 2.0),
    ("limitations", ["does_not_beat_baseline"]), ("level", "High"),
    ("source_ref", "unrelated producer"),
])
def test_api_rejects_inconsistent_or_coerced_confidence_evidence(payload, install_reader, field, value):
    _, parquet = install_reader(Mock(return_value=payload))
    path = parquet.with_name("model_card.json")
    card = json.loads(path.read_text(encoding="utf-8"))
    card["confidence"][LOCATION][field] = value
    path.write_text(json.dumps(card), encoding="utf-8")
    with pytest.raises(PipelineDataError, match="artifact provenance is invalid"):
        get_pipeline_location(LOCATION)


def test_api_rejects_original_bug_even_when_table_and_card_scores_match(payload, install_reader):
    from fastapi.testclient import TestClient
    from api.main import app

    _, parquet = install_reader(Mock(return_value=payload))
    path = parquet.with_name("model_card.json")
    card = json.loads(path.read_text(encoding="utf-8"))
    evidence = confidence_from_evidence(.006, 0, [])
    evidence["score"] = .988  # Old agreement-only score paired with zero support.
    card["confidence"][LOCATION] = evidence
    payload["confidence"].update(score=.988, n_similar_historical_hours=0)
    path.write_text(json.dumps(card), encoding="utf-8")
    with TestClient(app) as client:
        response = client.post("/api/estimate", json={
            "location_id": LOCATION, "load_mw": 100, "term_years": 2,
            "flexibility_split": .6, "site_exposure": .25,
        })
    assert response.status_code == 503
    assert "historical support" in response.json()["detail"]


@pytest.mark.parametrize("count,expected_score", [(0, 0), (5, .1976), (200, .69)])
def test_api_preserves_supported_scores_and_describes_the_evidence(payload, install_reader, count, expected_score):
    _, parquet = install_reader(Mock(return_value=payload))
    path = parquet.with_name("model_card.json")
    card = json.loads(path.read_text(encoding="utf-8"))
    evidence = confidence_from_evidence(.006, count, ["does_not_beat_baseline"])
    card["confidence"][LOCATION] = evidence
    payload["confidence"].update(score=evidence["score"], n_similar_historical_hours=count)
    path.write_text(json.dumps(card), encoding="utf-8")

    result = get_pipeline_location(LOCATION)
    assert result.confidence.score == pytest.approx(expected_score)
    assert result.confidence.level == "Low"
    assert result.confidence.basis == "ensemble_agreement_and_historical_support"
    assert "agreement_score=0.988;" in result.confidence.source.ref
    assert f"n_similar_historical_hours={count};" in result.confidence.source.ref
    assert "does_not_beat_baseline" in result.confidence.source.ref


@pytest.mark.parametrize("name", ["model_card.json", "simulation_metadata.json"])
@pytest.mark.parametrize("extra", ['"audit": {"value": 0, "value": 1}', '"audit": 1e9999'])
def test_ambiguous_metadata_is_rejected_at_reader_boundary(payload, install_reader, name, extra):
    _, parquet = install_reader(Mock(return_value=payload))
    path = parquet.with_name(name)
    contents = path.read_text(encoding="utf-8")
    path.write_text("{" + extra + "," + contents[1:], encoding="utf-8")
    with pytest.raises(PipelineDataError, match="artifact provenance is invalid"):
        get_pipeline_location(LOCATION)


def test_confidence_validation_does_not_import_the_training_stack(payload, install_reader):
    import subprocess
    import sys
    from pathlib import Path

    _, parquet = install_reader(Mock(return_value=payload))
    documents = [payload] + [json.loads(parquet.with_name(name).read_text(encoding="utf-8"))
                            for name in ("model_card.json", "simulation_metadata.json")]
    # A fresh interpreter catches accidental imports even when the suite already
    # imported all training dependencies for its offline pipeline tests.
    code = """
import sys, json
from importlib.abc import MetaPathFinder
class NoTraining(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'pipeline.confidence', 'pipeline.train'} or fullname.split('.')[0] in {'sklearn', 'lightgbm', 'xgboost', 'scipy', 'joblib'}:
            raise AssertionError('API imported training dependency: ' + fullname)
sys.meta_path.insert(0, NoTraining())
from api.pipeline_provider import PipelineEstimate, _validate_provenance
payload, card, simulation = json.load(sys.stdin)
_validate_provenance(PipelineEstimate.model_validate(payload), card, simulation)
"""
    result = subprocess.run([sys.executable, "-c", code], input=json.dumps(documents),
                            text=True, capture_output=True, timeout=30,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr
