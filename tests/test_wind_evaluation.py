"""Frozen later-year replay must expose absent calendar hours and never refit."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from pipeline import wind_signal as wind


SOURCES = {name: {"source_type": "assumption", "ref": f"synthetic {name} replay fixture"}
           for name in ("system_wind_mw", "system_load_mw")}
BASELINE = {"value": 0.5, "source_type": "assumption", "ref": "predeclared synthetic constant comparator"}


def frame_at(starts):
    times = pd.DatetimeIndex([stamp for start in starts
                             for stamp in pd.date_range(start, periods=120, freq="h", tz="UTC")])
    index = np.arange(len(times))
    hourly = pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM",
                           "system_wind_mw": 50. + 20. * np.sin(index * np.pi / 6),
                           "system_load_mw": 100. + 15. * np.cos(index * np.pi / 12)})
    labels = pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM",
                           "wind_curtailment_event": pd.array(index % 12 < 6, dtype="boolean"),
                           "observed_five_minute_samples": 12, "evaluable_five_minute_samples": 12})
    labels.attrs = {"method": "reported_system_wind_curtailment_any_category_v1", "system_scope": "SPP_SYSTEM",
                    "source": {"source_type": "assumption", "ref": "synthetic independent VER replay targets"}}
    return hourly, labels


@pytest.fixture(scope="module")
def bundle():
    hourly, labels = frame_at(["2024-01-01", "2024-09-01", "2024-10-01"])
    return wind.fit_wind_event_classifier(hourly, labels, sources=SOURCES)[0]


def evaluate(bundle, hourly=None, labels=None, **overrides):
    if hourly is None:
        hourly, labels = frame_at(["2024-12-31"])
    return wind.evaluate_wind_event_classifier(bundle, hourly, labels, **{
        "sources": SOURCES, "baseline_probability": BASELINE,
        "start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2025-01-02T00:00:00Z", **overrides})


def test_later_replay_never_fits_or_mutates_frozen_parameters(bundle, monkeypatch):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    def reject(*args, **kwargs):
        raise AssertionError("Independent replay must not fit an estimator")
    monkeypatch.setattr(LogisticRegression, "fit", reject)
    monkeypatch.setattr(StandardScaler, "fit", reject)
    before = deepcopy(bundle)
    report, rows = evaluate(bundle)
    assert bundle == before
    assert report["model_id"] == bundle["model_id"]
    assert report["coverage"]["eligible_hours"]["value"] == 24
    assert all(row["target_period_role"] == "outside_evaluated_period" for row in rows)
    assert all(row["forecast_asof_verified"] is False for row in rows)


def test_declared_calendar_grid_does_not_hide_absent_archive_hours(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    labels = labels.loc[labels.timestamp_utc < pd.Timestamp("2025-01-01T12:00Z")].copy()
    report, rows = evaluate(bundle, hourly, labels)
    coverage = report["coverage"]
    assert coverage["requested_hours"]["value"] == 24
    assert coverage["supplied_label_hours"]["value"] == 12
    assert coverage["missing_label_hours"]["value"] == 12
    assert coverage["eligible_hours"]["value"] == 12
    assert coverage["excluded_hours"]["value"] == 12
    assert len(rows) == 24
    assert all(row["observed_event"]["value"] is None and not row["evaluated"] for row in rows[12:])
    assert all(row["raw_probability"]["value"] is not None for row in rows[12:])
    assert all(row["observed_event"]["source_type"] == "assumption" for row in rows[12:])


def test_overlapping_missing_labels_and_lags_count_exclusion_once(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    stamp = pd.Timestamp("2025-01-01T01:00Z")
    labels = labels.loc[labels.timestamp_utc != stamp].copy()
    hourly = hourly.loc[hourly.timestamp_utc != stamp - pd.Timedelta(1, unit="h")].copy()
    report, rows = evaluate(bundle, hourly, labels)
    assert report["coverage"]["missing_label_hours"]["value"] == 1
    assert report["coverage"]["missing_lag_hours"]["value"] == 1
    assert report["coverage"]["excluded_hours"]["value"] == 1
    assert rows[1]["raw_probability"]["value"] is None


def test_incomplete_supplied_hour_remains_distinct_from_missing_row(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    chosen = labels.timestamp_utc == pd.Timestamp("2025-01-01T00:00Z")
    labels.loc[chosen, "wind_curtailment_event"] = pd.NA
    labels.loc[chosen, "evaluable_five_minute_samples"] = 11
    report, rows = evaluate(bundle, hourly, labels)
    assert report["coverage"]["unknown_supplied_target_hours"]["value"] == 1
    assert report["coverage"]["missing_label_hours"]["value"] == 0
    assert "incomplete supplied" in rows[0]["observed_event"]["ref"]


def test_all_probability_comparators_use_identical_observed_rows(bundle):
    report, rows = evaluate(bundle)
    chosen = [row for row in rows if row["evaluated"]]
    target = [row["observed_event"]["value"] for row in chosen]
    for metric_name, field in [("raw", "raw_probability"), ("calibrated", "calibrated_probability")]:
        probabilities = [row[field]["value"] for row in chosen]
        metrics = report["metrics"][metric_name]
        assert metrics["sample_count"]["value"] == len(chosen)
        assert metrics["brier_score"]["value"] == pytest.approx(brier_score_loss(target, probabilities))
        assert metrics["log_loss"]["value"] == pytest.approx(log_loss(target, probabilities))
        assert metrics["roc_auc"]["value"] == pytest.approx(roc_auc_score(target, probabilities))
        assert sum(item["sample_count"]["value"] for item in metrics["reliability"]) == len(chosen)
    baseline = report["metrics"]["supplied_constant_baseline"]
    assert baseline["sample_count"]["value"] == len(chosen)
    assert baseline["brier_score"]["value"] == 0.25
    assert report["baseline_probability"] == BASELINE


def test_outcome_changes_cannot_change_predictions_or_frozen_model(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    original_report, original_rows = evaluate(bundle, hourly, labels)
    labels.wind_curtailment_event = ~labels.wind_curtailment_event
    report, rows = evaluate(bundle, hourly, labels)
    assert [row["raw_probability"] for row in rows] == [row["raw_probability"] for row in original_rows]
    assert report["data_manifest"]["labels_sha256"] != original_report["data_manifest"]["labels_sha256"]
    assert report["metrics"]["raw"]["brier_score"] != original_report["metrics"]["raw"]["brier_score"]
    assert report["metrics"]["raw"]["brier_score"]["ref"] != original_report["metrics"]["raw"]["brier_score"]["ref"]
    assert report["data_manifest"]["evaluation_cohort_sha256"] in report["metrics"]["raw"]["brier_score"]["ref"]


def test_probability_refs_identify_the_hour_and_query_data(bundle):
    report, rows = evaluate(bundle)
    assert rows[0]["raw_probability"]["ref"] != rows[1]["raw_probability"]["ref"]
    assert rows[0]["timestamp_utc"] in rows[0]["raw_probability"]["ref"]
    assert report["data_manifest"]["hourly_observations_sha256"] in rows[0]["raw_probability"]["ref"]


def test_unavailable_metric_refs_identify_the_distinct_requested_windows(bundle):
    first, _ = evaluate(bundle, start_utc="2025-02-01T00:00Z", end_exclusive_utc="2025-02-02T00:00Z")
    second, _ = evaluate(bundle, start_utc="2025-03-01T00:00Z", end_exclusive_utc="2025-03-02T00:00Z")
    left, right = first["metrics"]["raw"]["brier_score"], second["metrics"]["raw"]["brier_score"]
    assert left["value"] is right["value"] is None
    assert left["ref"] != right["ref"]


def test_reordered_evaluation_is_byte_reproducible_and_keeps_assumptions(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    first = evaluate(bundle, hourly, labels)
    second = evaluate(bundle, hourly.iloc[::-1], labels.iloc[::-1], sources=dict(reversed(list(SOURCES.items()))))
    assert json.dumps(first) == json.dumps(second)
    for metrics in first[0]["metrics"].values():
        assert metrics["brier_score"]["source_type"] == "assumption"
    assert "research_only" in first[0]["status"]


@pytest.mark.parametrize("overrides", [
    {"start_utc": "2024-12-31T00:00Z"},
    {"start_utc": "2025-01-02T00:00Z"},
    {"start_utc": "2025-01-03T00:00Z"},
    {"start_utc": "2025-01-01"},
    {"start_utc": "2025-01-01T00:30Z"},
    {"end_exclusive_utc": "2026-01-03T00:00Z"},
])
def test_invalid_or_reused_experiment_windows_are_rejected(bundle, overrides):
    with pytest.raises(ValueError):
        evaluate(bundle, **overrides)


@pytest.mark.parametrize("value", [None, True, -0.1, 1.1, np.nan, np.inf])
def test_invalid_constant_baselines_do_not_become_plausible_comparisons(bundle, value):
    with pytest.raises(ValueError):
        evaluate(bundle, baseline_probability={**BASELINE, "value": value})


def test_baseline_cannot_hide_an_assumed_coefficient_under_data_provenance(bundle):
    ref = json.dumps({"method": "declared comparator", "inputs": [BASELINE]})
    with pytest.raises(ValueError, match="upgrade a nested"):
        evaluate(bundle, baseline_probability={**BASELINE, "source_type": "data", "ref": ref})


def test_frozen_legacy_manifest_replays_with_new_source_qualification(bundle):
    legacy = deepcopy(bundle)
    legacy["manifest"]["limitation"] = wind.WIND_CLASSIFIER_LEGACY_LIMITATION
    legacy["model_id"] = wind._wind_json_digest({key: value for key, value in legacy.items() if key != "model_id"})
    before = deepcopy(legacy)
    report, rows = evaluate(legacy)
    assert legacy == before
    assert report["model_id"] == legacy["model_id"]
    assert report["limitation"] == wind.WIND_CLASSIFIER_LIMITATION
    assert report["source_qualification"] == wind.GENMIX_SOURCE_QUALIFICATION
    assert "dispatch targets" in report["limitation"]
    assert all(row["source_qualification"] == wind.GENMIX_SOURCE_QUALIFICATION for row in rows)


def test_legacy_compatibility_does_not_accept_arbitrary_softened_limitations(bundle):
    edited = deepcopy(bundle)
    edited["manifest"]["limitation"] = "Verified actual metered generation with no timing uncertainty."
    edited["model_id"] = wind._wind_json_digest({key: value for key, value in edited.items() if key != "model_id"})
    with pytest.raises(ValueError, match="honesty framing"):
        evaluate(edited)


def test_zero_evaluable_window_reports_unavailable_not_zero_error(bundle):
    hourly, labels = frame_at(["2024-12-31"])
    report, rows = evaluate(bundle, hourly, labels,
                            start_utc="2025-02-01T00:00Z", end_exclusive_utc="2025-02-02T00:00Z")
    assert report["coverage"]["excluded_hours"]["value"] == 24
    assert report["coverage"]["missing_label_hours"]["value"] == 24
    assert all(not row["evaluated"] for row in rows)
    for metrics in report["metrics"].values():
        assert metrics["brier_score"]["value"] is None
        assert metrics["log_loss"]["value"] is None


def test_disabled_calibration_is_not_substituted_with_raw_probabilities():
    hourly, labels = frame_at(["2024-01-01", "2024-09-01", "2024-10-01"])
    bundle = wind.fit_wind_event_classifier(hourly, labels, sources=SOURCES, calibrate=False)[0]
    report, rows = evaluate(bundle)
    assert report["metrics"]["raw"]["sample_count"]["value"] == 24
    assert report["metrics"]["calibrated"]["sample_count"]["value"] == 0
    assert report["metrics"]["calibrated"]["brier_score"]["value"] is None
    assert all(row["calibrated_probability"]["value"] is None for row in rows)


def replay_publication_inputs(tmp_path, bundle, *, hourly=None, labels=None):
    if hourly is None:
        hourly, labels = frame_at(["2024-12-31"])
    hourly = hourly.copy()
    hourly.attrs["sources"] = deepcopy(SOURCES)
    inputs = tmp_path / "supplied"
    inputs.mkdir()
    paths = {"bundle": inputs / "bundle.json", "hourly": inputs / "hourly.parquet",
             "labels": inputs / "labels.parquet", "baseline": inputs / "baseline.json"}
    # Noncanonical JSON whitespace makes exact copying distinct from reencoding.
    paths["bundle"].write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    paths["baseline"].write_text(json.dumps(BASELINE, indent=1) + "\n", encoding="utf-8")
    hourly.to_parquet(paths["hourly"], index=False)
    labels.to_parquet(paths["labels"], index=False)
    arguments = {**{role + "_path": path for role, path in paths.items()},
                 "start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2025-01-02T00:00:00Z",
                 "output_dir": tmp_path / "published"}
    return arguments, hourly, labels


def test_publisher_freezes_exact_inputs_hashes_and_nested_prediction_provenance(bundle, tmp_path, monkeypatch):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    arguments, hourly, labels = replay_publication_inputs(tmp_path, bundle)
    original_inputs = {role: arguments[role + "_path"].read_bytes()
                       for role in ("bundle", "hourly", "labels", "baseline")}
    expected_report, expected_rows = evaluate(bundle, hourly, labels)
    evaluator = wind.evaluate_wind_event_classifier
    calls = []
    def recorded_evaluation(*args, **kwargs):
        calls.append((args, kwargs))
        return evaluator(*args, **kwargs)
    def reject(*args, **kwargs):
        raise AssertionError("Publishing frozen replay must never fit or fetch")
    monkeypatch.setattr(wind, "evaluate_wind_event_classifier", recorded_evaluation)
    monkeypatch.setattr(wind, "fit_wind_event_classifier", reject)
    monkeypatch.setattr(LogisticRegression, "fit", reject)
    monkeypatch.setattr(StandardScaler, "fit", reject)
    monkeypatch.setattr(wind.ingest, "fetch_public_evidence", reject)
    manifest = wind.publish_wind_replay(**arguments)
    output = arguments["output_dir"]
    assert len(calls) == 1
    assert calls[0][1]["sources"] == SOURCES
    assert calls[0][0][2].attrs == labels.attrs
    assert manifest == json.loads((output / "manifest.json").read_bytes())
    assert manifest["schema_version"] == "spp-wind-replay-publication-v1"
    assert manifest["status"] == "research_only_no_production_promotion"
    assert manifest["model_id"] == bundle["model_id"]
    assert manifest["start_utc"] == expected_report["start_utc"]
    assert manifest["end_exclusive_utc"] == expected_report["end_exclusive_utc"]
    assert manifest["source_file_sha256"] == hashlib.sha256(Path(wind.__file__).read_bytes()).hexdigest()
    assert set(manifest["runtime"]) == {"python", "numpy", "pandas", "scikit_learn", "pyarrow"}
    assert all(isinstance(version, str) and version for version in manifest["runtime"].values())
    assert manifest["inputs"] == {role: "inputs/" + arguments[role + "_path"].name for role in original_inputs}
    assert manifest["outputs"] == {"report": "report.json", "predictions": "predictions.parquet"}
    for role, original in original_inputs.items():
        assert (output / manifest["inputs"][role]).read_bytes() == original
        assert arguments[role + "_path"].read_bytes() == original
    relative_files = set(manifest["inputs"].values()) | set(manifest["outputs"].values())
    assert set(manifest["files"]) == relative_files
    for relative in relative_files:
        content = (output / relative).read_bytes()
        assert manifest["files"][relative] == {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    predictions = pd.read_parquet(output / "predictions.parquet")
    assert manifest["parquet"] == {"engine": "pyarrow", "compression": "zstd", "columns": predictions.columns.tolist()}
    assert json.loads((output / "report.json").read_bytes()) == expected_report
    assert predictions.to_dict("records") == expected_rows
    assert set(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()) == relative_files | {"manifest.json"}


def test_publisher_reads_each_input_once_and_copies_the_evaluated_bytes(bundle, tmp_path, monkeypatch):
    arguments, hourly, labels = replay_publication_inputs(tmp_path, bundle)
    paths = {arguments[role + "_path"] for role in ("bundle", "hourly", "labels", "baseline")}
    originals = {path: path.read_bytes() for path in paths}
    expected_report, expected_rows = evaluate(bundle, hourly, labels)
    reads = {path: 0 for path in paths}
    original_read = Path.read_bytes
    def replace_after_read(path):
        payload = original_read(path)
        if path in reads:
            reads[path] += 1
            path.write_bytes(b"concurrent input replacement after captured read")
        return payload
    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    manifest = wind.publish_wind_replay(**arguments)
    monkeypatch.setattr(Path, "read_bytes", original_read)
    assert set(reads.values()) == {1}
    for role, relative in manifest["inputs"].items():
        original = originals[arguments[role + "_path"]]
        assert (arguments["output_dir"] / relative).read_bytes() == original
        assert manifest["files"][relative]["sha256"] == hashlib.sha256(original).hexdigest()
    assert json.loads((arguments["output_dir"] / "report.json").read_bytes()) == expected_report
    assert pd.read_parquet(arguments["output_dir"] / "predictions.parquet").to_dict("records") == expected_rows


@pytest.mark.parametrize("case", ["partial_labels", "all_excluded", "disabled_calibration"])
def test_publisher_retains_partial_and_null_replay_outcomes(bundle, tmp_path, case):
    hourly, labels = frame_at(["2024-12-31"])
    if case == "partial_labels":
        labels = labels.loc[labels.timestamp_utc < pd.Timestamp("2025-01-01T12:00Z")].copy()
    elif case == "disabled_calibration":
        training, targets = frame_at(["2024-01-01", "2024-09-01", "2024-10-01"])
        bundle = wind.fit_wind_event_classifier(training, targets, sources=SOURCES, calibrate=False)[0]
    arguments, hourly, labels = replay_publication_inputs(tmp_path, bundle, hourly=hourly, labels=labels)
    if case == "all_excluded":
        arguments.update(start_utc="2025-02-01T00:00:00Z", end_exclusive_utc="2025-02-02T00:00:00Z")
    expected_report, expected_rows = evaluate(bundle, hourly, labels,
        start_utc=arguments["start_utc"], end_exclusive_utc=arguments["end_exclusive_utc"])
    wind.publish_wind_replay(**arguments)
    assert json.loads((arguments["output_dir"] / "report.json").read_bytes()) == expected_report
    actual = pd.read_parquet(arguments["output_dir"] / "predictions.parquet").to_dict("records")
    assert actual == expected_rows
    assert any(row["observed_event"]["value"] is None or row["calibrated_probability"]["value"] is None for row in actual)


@pytest.mark.parametrize("role", ["bundle", "hourly", "labels", "baseline"])
def test_publisher_missing_input_cannot_create_a_publication(bundle, tmp_path, role):
    arguments, _, _ = replay_publication_inputs(tmp_path, bundle)
    arguments[role + "_path"].unlink()
    with pytest.raises(FileNotFoundError):
        wind.publish_wind_replay(**arguments)
    assert not arguments["output_dir"].exists()


@pytest.mark.parametrize("case", ["bad_bundle", "bad_baseline", "duplicate_baseline", "nonfinite_baseline",
                                  "bad_parquet", "missing_sources", "missing_source", "extra_source",
                                  "missing_label_attrs", "invalid_window"])
def test_publisher_invalid_input_cannot_create_a_publication(bundle, tmp_path, case):
    arguments, hourly, labels = replay_publication_inputs(tmp_path, bundle)
    if case == "bad_bundle":
        arguments["bundle_path"].write_text('{"model_id":"unverified"}', encoding="utf-8")
    elif case == "bad_baseline":
        arguments["baseline_path"].write_text(json.dumps({**BASELINE, "value": True}), encoding="utf-8")
    elif case == "duplicate_baseline":
        arguments["baseline_path"].write_text(json.dumps(BASELINE)[:-1] + ', "value":0.7}', encoding="utf-8")
    elif case == "nonfinite_baseline":
        arguments["baseline_path"].write_text(json.dumps({**BASELINE, "value": float("nan")}), encoding="utf-8")
    elif case == "bad_parquet":
        arguments["hourly_path"].write_bytes(b"not a parquet table")
    elif case in {"missing_sources", "missing_source", "extra_source"}:
        if case == "missing_sources":
            hourly.attrs.clear()
        elif case == "missing_source":
            del hourly.attrs["sources"]["system_wind_mw"]
        else:
            hourly.attrs["sources"]["unmodeled_column"] = deepcopy(SOURCES["system_load_mw"])
        hourly.to_parquet(arguments["hourly_path"], index=False)
    elif case == "missing_label_attrs":
        labels.attrs.clear()
        labels.to_parquet(arguments["labels_path"], index=False)
    else:
        arguments["start_utc"] = "2024-12-31T00:00:00Z"
    with pytest.raises(ValueError):
        wind.publish_wind_replay(**arguments)
    assert not arguments["output_dir"].exists()


@pytest.mark.parametrize("existing", ["empty_directory", "occupied_directory", "file"])
def test_publisher_preserves_existing_destination(bundle, tmp_path, existing):
    arguments, _, _ = replay_publication_inputs(tmp_path, bundle)
    output = arguments["output_dir"]
    if existing == "file":
        output.write_bytes(b"owned by another publication")
    else:
        output.mkdir()
        if existing == "occupied_directory":
            (output / "report.json").write_bytes(b"existing report")
    before = output.read_bytes() if output.is_file() else {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(FileExistsError):
        wind.publish_wind_replay(**arguments)
    after = output.read_bytes() if output.is_file() else {path.name: path.read_bytes() for path in output.iterdir()}
    assert after == before


def test_publisher_cannot_overwrite_a_racing_destination(bundle, tmp_path, monkeypatch):
    arguments, _, _ = replay_publication_inputs(tmp_path, bundle)
    original = wind.evaluate_wind_event_classifier
    def competing_publication(*args, **kwargs):
        result = original(*args, **kwargs)
        arguments["output_dir"].mkdir()
        (arguments["output_dir"] / "owned.txt").write_bytes(b"racing publisher")
        return result
    monkeypatch.setattr(wind, "evaluate_wind_event_classifier", competing_publication)
    with pytest.raises(FileExistsError):
        wind.publish_wind_replay(**arguments)
    assert list(arguments["output_dir"].iterdir()) == [arguments["output_dir"] / "owned.txt"]
    assert (arguments["output_dir"] / "owned.txt").read_bytes() == b"racing publisher"


def test_failed_prediction_write_never_leaves_a_completed_manifest(bundle, tmp_path, monkeypatch):
    arguments, _, _ = replay_publication_inputs(tmp_path, bundle)
    def fail_write(*args, **kwargs):
        raise OSError("synthetic output write failure")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_write)
    with pytest.raises(OSError, match="synthetic output write failure"):
        wind.publish_wind_replay(**arguments)
    assert not (arguments["output_dir"] / "manifest.json").exists()


def test_manifest_is_published_last_and_a_link_failure_leaves_no_completion_record(bundle, tmp_path, monkeypatch):
    arguments, _, _ = replay_publication_inputs(tmp_path, bundle)
    def failed_manifest_link(source, destination):
        assert destination == arguments["output_dir"] / "manifest.json"
        manifest = json.loads(Path(source).read_bytes())
        for relative, metadata in manifest["files"].items():
            content = (arguments["output_dir"] / relative).read_bytes()
            assert metadata == {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        raise OSError("synthetic manifest publication failure")
    monkeypatch.setattr(wind.os, "link", failed_manifest_link)
    with pytest.raises(OSError, match="synthetic manifest publication failure"):
        wind.publish_wind_replay(**arguments)
    assert not (arguments["output_dir"] / "manifest.json").exists()
    assert not list(arguments["output_dir"].glob("*.tmp"))
    assert (arguments["output_dir"] / "report.json").exists()


def test_racing_manifest_cannot_be_overwritten(bundle, tmp_path, monkeypatch):
    arguments, _, _ = replay_publication_inputs(tmp_path, bundle)
    original = wind.os.link
    def competing_manifest(source, destination):
        destination.write_bytes(b"other publisher owns this manifest")
        original(source, destination)
    monkeypatch.setattr(wind.os, "link", competing_manifest)
    with pytest.raises(FileExistsError):
        wind.publish_wind_replay(**arguments)
    assert (arguments["output_dir"] / "manifest.json").read_bytes() == b"other publisher owns this manifest"
    assert not list(arguments["output_dir"].glob("*.tmp"))


def test_identical_replays_publish_identical_bytes_under_the_same_runtime(bundle, tmp_path):
    arguments, _, _ = replay_publication_inputs(tmp_path, bundle)
    first = wind.publish_wind_replay(**arguments)
    other_output = tmp_path / "second-publication"
    second = wind.publish_wind_replay(**{**arguments, "output_dir": other_output})
    assert first == second
    for relative in [*first["files"], "manifest.json"]:
        assert (arguments["output_dir"] / relative).read_bytes() == (other_output / relative).read_bytes()
