"""Software fixtures for confidence; never used as product evidence."""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from pipeline.confidence import CONFIDENCE_POLICY, confidence_from_evidence, estimate_confidence, rescore_saved_artifacts
from pipeline.common import fingerprint, write_json
from pipeline.simulate import simulate_exposure


def test_agreement_without_precedent_is_zero_not_maximum_confidence():
    for spread in (0, .005875, .25, .5):
        result = confidence_from_evidence(spread, 0, [])
        assert result["score"] == 0
        assert result["level"] == "Low"
        assert "insufficient_same_location_precedent" in result["limitations"]


def test_historical_support_monotonically_increases_score():
    counts = [0, 1, 2, 5, 20, 50, 100, 200, 1000]
    scores = [confidence_from_evidence(.006, count, [])["score"] for count in counts]
    assert all(left < right for left, right in zip(scores, scores[1:]))
    assert scores[3] < .2
    assert scores[-1] < 1
    assert confidence_from_evidence(.2, 100, [])["score"] < scores[6]


@pytest.mark.parametrize("reason", ["does_not_beat_baseline", "less_than_one_year_of_heldout_history", "fewer_than_20_positive_test_hours"])
def test_validation_limits_cap_numeric_score_as_well_as_badge(reason):
    result = confidence_from_evidence(0, 10000, [reason])
    assert result["level"] == "Low"
    assert result["score"] == CONFIDENCE_POLICY["low_score_max"]


def test_badges_follow_supported_scores_without_forcing_variation():
    assert [confidence_from_evidence(0, n, [])["level"] for n in (5, 50, 200)] == ["Low", "Medium", "High"]


@pytest.mark.parametrize("spread,count", [(float("nan"), 2), (float("inf"), 2), (-.1, 2), (.6, 2), (True, 2), (.01, -1), (.01, 1.5), (.01, True)])
def test_invalid_evidence_rejected(spread, count):
    with pytest.raises(ValueError):
        confidence_from_evidence(spread, count, [])


def test_real_neighbor_counts_are_local_and_drive_different_scores():
    # Two locations share the same feature value and identical model agreement.
    # Other-location reference rows must not inflate the sparse area's support.
    locations = np.array(["dense"] * 200 + ["sparse"] * 5)
    times = pd.date_range("2023-01-01", periods=30, freq="14D", tz="UTC")
    frame = pd.DataFrame([{"timestamp_utc": time, "location_id": location, "x": 0., "target": 1}
                          for location in ("dense", "sparse", "unseen") for time in times])
    bundle = {"feature_names": ["x"], "density_medians": np.zeros(1), "density_scales": np.ones(1),
              "density_training": np.zeros((len(locations), 1)), "density_training_locations": locations,
              "report": {"test_by_location": {location: {"brier_skill_vs_train_prevalence": .2} for location in ("dense", "sparse")}}}
    with patch("pipeline.confidence.predict_members", side_effect=lambda _, rows: np.full((len(rows), 2), .01)):
        result = estimate_confidence(bundle, frame)
        reordered = estimate_confidence(bundle, frame.sample(frac=1, random_state=2026))
    assert result == reordered
    assert [result[location]["n_similar_historical_hours"] for location in ("dense", "sparse", "unseen")] == [200, 5, 0]
    assert result["dense"]["score"] > result["sparse"]["score"] > result["unseen"]["score"]
    assert result["dense"]["level"] == "High"
    assert result["sparse"]["level"] == result["unseen"]["level"] == "Low"


def test_simulator_rejects_old_agreement_only_confidence():
    times = pd.date_range("2023-01-01", "2024-01-03", freq="h", tz="UTC")
    predictions = pd.DataFrame({"timestamp_utc": times, "location_id": "test", "target": 0,
                                "probability": .01, "member_0": .01, "member_1": .01})
    stale = {"test": {"score": .988, "n_similar_historical_hours": 0}}
    with pytest.raises(ValueError, match="stale or invalid confidence"):
        simulate_exposure(predictions, stale, "test-only", simulations=1000, years=1)


def test_saved_artifact_rescore_preserves_exposure_and_records_its_limits(tmp_path):
    import json

    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    rows = []
    evidence = {}
    for location, count in (("test-zero", 0), ("test-sparse", 5)):
        evidence[location] = {"score": .99, "level": "Low", "mean_ensemble_probability_std": .005,
                              "n_similar_historical_hours": count, "limitations": ["less_than_one_year_of_heldout_history"]}
        for year in (1, 2):
            rows.append({"location_id": location, "year_offset": year, "model_version": "test-only",
                         "p50_hours": 10., "p90_hours": 20., "p99_hours": 30., "worst_contiguous_hours": 3.,
                         "confidence_level": "Low", "confidence_score": .99, "n_similar_historical_hours": count})
    original = pd.DataFrame(rows)
    original.to_parquet(source / "exposure_by_location.parquet", index=False)
    write_json(source / "model_card.json", {"model_version": "test-only", "confidence": evidence, "policy": {"label_method": "observed_event"}})
    write_json(source / "simulation_metadata.json", {"ref": "pipeline/simulate.py model_version=test-only", "policy": {"label_method": "observed_event"},
        "assumptions": ["Annual exposure confidence is capped Low until annual tail validation; its numeric score describes classifier agreement only."]})
    digest = fingerprint(source / "exposure_by_location.parquet")
    result = rescore_saved_artifacts(source, output)
    pd.testing.assert_frame_equal(original.drop(columns="confidence_score"), result.drop(columns="confidence_score"))
    assert fingerprint(source / "exposure_by_location.parquet") == digest
    assert result[result.location_id.eq("test-zero")].confidence_score.eq(0).all()
    assert result[result.location_id.eq("test-sparse")].confidence_score.between(0, .2).all()
    card = json.loads((output / "model_card.json").read_text())
    metadata = json.loads((output / "simulation_metadata.json").read_text())
    assert card["confidence_revision"] == metadata["confidence_revision"]
    assert card["confidence_revision"]["retrained"] is False
    assert card["confidence_revision"]["precedent_recomputed"] is False
    assert card["confidence_revision"]["source_artifact_sha256"]["exposure_by_location.parquet"] == digest
    assert all("classifier agreement only" not in text for text in metadata["assumptions"])
    with pytest.raises(ValueError, match="empty output"):
        rescore_saved_artifacts(source, output)
    # A mismatched handoff must not silently score the wrong location/model.
    original.loc[0, "confidence_score"] = .9
    original.to_parquet(source / "exposure_by_location.parquet", index=False)
    with pytest.raises(ValueError, match="Conflicting confidence_score"):
        rescore_saved_artifacts(source, tmp_path / "invalid")
