"""The precomputed reader boundary must distinguish missing data from broken data."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api import pipeline_provider
from api.estimate import build_estimate
from api.mock_provider import LocationNotFoundError
from api.pipeline_provider import PipelineDataError, get_pipeline_location
from api.schemas import EstimateRequest


LOCATION = "SPP_SPS_HUB"


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
            "level": "High",
            "score": 0.8,
            "n_similar_historical_hours": 1000,
        },
        "model_version": "test_precomputed_reader_v1",
    }


@pytest.fixture
def install_reader(monkeypatch, tmp_path):
    # Only the adapter's existence gate touches this file. The reader is a stub;
    # no parquet engine, training dependency, or external data pull is involved.
    parquet = tmp_path / "exposure_by_location.parquet"
    parquet.touch()
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
    reader.assert_called_once_with(LOCATION)
    assert payload == original
    for actual, expected in zip(location.by_year, payload["by_year"], strict=True):
        assert vars(actual) == expected
    assert location.source.source_type == "model"
    assert "test_precomputed_reader_v1" in location.source.ref
    assert "data/processed/exposure_by_location.parquet" in location.source.ref
    assert location.confidence.level == "High"
    assert location.confidence.score == 0.8
    assert location.confidence.basis == "ensemble_disagreement"
    assert location.confidence.source.source_type == "model"
    assert "n_similar_historical_hours=1000" in location.confidence.source.ref
    # A real exposure reader does not turn unpublished tariff extraction into fact.
    assert all(trigger.source.source_type == "assumption" for trigger in location.tariff.curtailment_triggers)


def test_site_factor_is_applied_once_downstream_of_the_reader(payload, install_reader):
    reader = Mock(return_value=payload)
    install_reader(reader)
    request = EstimateRequest(
        location_id=LOCATION, load_mw=100, term_years=2,
        flexibility_split=0.6, site_exposure=0.25,
    )

    result = build_estimate(request, get_pipeline_location)

    reader.assert_called_once_with(LOCATION)
    assert result.modeled_exposure.by_year[0].p50 == 25
    assert result.modeled_exposure.by_year[1].p50 == 50
    assert result.modeled_exposure.p50 == 37.5
    assert result.modeled_exposure.p90 == 75
    assert result.modeled_exposure.p99 == 112.5
    assert result.modeled_exposure.worst_contiguous_outage_hours == 10
    assert result.confidence.score == 0.8
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
    }
    with TestClient(app) as client:
        response = client.post("/api/estimate", json=request, headers={"Origin": "http://127.0.0.1:5174"})
    assert response.status_code == 200
    reader.assert_called_once_with(LOCATION)
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
    reader.assert_called_once_with(LOCATION)


def test_newly_available_reader_is_discovered_on_the_next_call(payload, install_reader):
    reader = Mock(return_value=payload)
    importer, _ = install_reader(reader)
    importer.side_effect = [
        ModuleNotFoundError("reader absent", name="pipeline.simulate"),
        SimpleNamespace(get_location_estimate=reader),
    ]
    assert_placeholder(get_pipeline_location(LOCATION), "pipeline.simulate is absent")
    assert get_pipeline_location(LOCATION).source.source_type == "model"
    reader.assert_called_once_with(LOCATION)


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
    (("by_year", 0, "worst_contiguous_hours"), -1),
    (("by_year", 0, "worst_contiguous_hours"), float("nan")),
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
