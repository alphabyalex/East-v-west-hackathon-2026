"""Conservative confidence from ensemble agreement AND same-location precedent.

The score is a documented heuristic, not a probability of correctness. Agreement
alone can be nearly perfect when every member is extrapolating. Historical
support therefore multiplies agreement, and failed validation caps the score.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors

from pipeline.train import predict_members

from pipeline.confidence_policy import CONFIDENCE_POLICY, confidence_from_evidence


CONFIDENCE_COVERAGE_POLICY = {
    "ref": "pipeline/confidence.py:CONFIDENCE_COVERAGE_POLICY",
    "minimum_scored_hours_for_above_low": 24 * CONFIDENCE_POLICY["minimum_heldout_span_days_for_above_low"],
    "hour_identity": "Distinct location and timezone-aware hourly interval-start timestamp.",
    "span": "Inclusive interval starts: (last - first) in hours plus one.",
    "meaning": "Minimum classifier-validation coverage; not a test of annual-tail accuracy.",
}


def confidence_coverage(frame: pd.DataFrame) -> dict:
    """Count actual local scored hours; elapsed calendar time is not evidence."""
    if frame.empty or not {"timestamp_utc", "location_id", "target"}.issubset(frame):
        raise ValueError("Confidence requires nonempty scored hourly observations.")
    times = frame.timestamp_utc
    if not isinstance(times.dtype, pd.DatetimeTZDtype) or times.isna().any():
        raise ValueError("Confidence requires distinct timezone-aware location/hour observations.")
    utc_times = times.dt.tz_convert("UTC")
    if (not utc_times.eq(utc_times.dt.floor("h")).all()
            or frame.duplicated(["location_id", "timestamp_utc"]).any()):
        raise ValueError("Confidence requires distinct timezone-aware location/hour observations.")
    if (frame.location_id.isna().any() or frame.location_id.astype(str).str.strip().eq("").any()
            or not frame.target.isin([0, 1]).all()):
        raise ValueError("Confidence requires known locations and observed binary targets.")
    result = {}
    for location, group in frame.groupby("location_id", sort=True):
        span = int((group.timestamp_utc.max() - group.timestamp_utc.min()).total_seconds() / 3600) + 1
        result[location] = {"scored_hours": len(group), "span_hours": span,
                            "missing_hours_within_span": span - len(group),
                            "positive_hours": int(group.target.sum())}
    return result


def estimate_confidence(bundle: dict, frame: pd.DataFrame) -> dict:
    output = {}
    policy = CONFIDENCE_POLICY
    coverage = confidence_coverage(frame)
    for location, group in frame.groupby("location_id", sort=True):
        group = group.sort_values("timestamp_utc")
        sample = group.iloc[np.linspace(0, len(group) - 1, min(len(group), policy["query_sample_limit"]), dtype=int)]
        values = sample[bundle["feature_names"]].to_numpy(dtype=float)
        standardized = np.nan_to_num((values - bundle["density_medians"]) / bundle["density_scales"], nan=0.0)
        reference = bundle["density_training"][bundle["density_training_locations"] == location]
        if len(reference):
            neighbors = NearestNeighbors(radius=policy["similarity_radius_rms_standard_deviations"] * np.sqrt(values.shape[1]), n_jobs=1).fit(reference)
            counts = [len(indices) for indices in neighbors.radius_neighbors(standardized, return_distance=False)]
            precedent = int(np.median(counts))
        else:
            precedent = 0
        spread = float(predict_members(bundle, sample).std(axis=1).mean())
        reasons = []
        skill = bundle["report"]["test_by_location"].get(location, {}).get("brier_skill_vs_train_prevalence")
        if skill is None or not np.isfinite(skill) or skill <= 0:
            reasons.append("does_not_beat_baseline")
        minimum_hours = CONFIDENCE_COVERAGE_POLICY["minimum_scored_hours_for_above_low"]
        if coverage[location]["span_hours"] < minimum_hours:
            reasons.append("less_than_one_year_of_heldout_history")
        if coverage[location]["scored_hours"] < minimum_hours:
            reasons.append("fewer_than_one_year_of_scored_hours")
        if coverage[location]["positive_hours"] < policy["minimum_positive_test_hours_for_above_low"]:
            reasons.append("fewer_than_20_positive_test_hours")
        output[location] = confidence_from_evidence(spread, precedent, reasons)
    return output


def rescore_saved_artifacts(source_dir, output_dir):
    """Re-export recorded confidence evidence without inventing a missing run.

    This is explicitly not retraining or resimulation. Quantiles, model identity,
    original input hashes, and held-out coverage are preserved. The new manifests
    distinguish this migration from an end-to-end model run.
    """
    import json
    from pathlib import Path

    from pipeline.common import fingerprint, write_json
    from pipeline.simulate import get_location_estimate

    source_dir, output_dir = Path(source_dir), Path(output_dir)
    if source_dir.resolve() == output_dir.resolve() or (output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("Choose a separate empty output directory to preserve the source artifacts.")
    path = source_dir / "exposure_by_location.parquet"
    frame = pd.read_parquet(path)
    card = json.loads((source_dir / "model_card.json").read_text(encoding="utf-8"))
    simulation = json.loads((source_dir / "simulation_metadata.json").read_text(encoding="utf-8"))
    if frame.empty or set(frame.location_id) != set(card["confidence"]):
        raise ValueError("Exposure locations and saved confidence evidence do not match.")
    if simulation["ref"] != f"pipeline/simulate.py model_version={card['model_version']}" or simulation["policy"] != card["policy"]:
        raise ValueError("Saved model and simulation provenance do not match.")
    updated = {}
    for location in sorted(frame.location_id.unique()):
        saved = get_location_estimate(location, path=path)
        info = card["confidence"][location]
        if saved["model_version"] != card["model_version"] or any(saved["confidence"][key] != info[key] for key in ("score", "n_similar_historical_hours")):
            raise ValueError("Saved exposure does not match the model's confidence evidence.")
        updated[location] = confidence_from_evidence(info["mean_ensemble_probability_std"], info["n_similar_historical_hours"], info["limitations"])
    frame["confidence_score"] = frame.location_id.map({key: info["score"] for key, info in updated.items()})
    frame["confidence_level"] = "Low"  # Existing unvalidated annual-tail cap.
    revision = {"method": "rescore_saved_ensemble_spread_and_precedent_counts", "policy_version": CONFIDENCE_POLICY["version"],
        "source_artifact_sha256": {name: fingerprint(source_dir / name) for name in
                                  ("exposure_by_location.parquet", "model_card.json", "simulation_metadata.json")},
        "retrained": False, "resimulated": False, "precedent_recomputed": False,
        "limitation": "Original run inputs/model unavailable. Recorded same-location neighbor counts and ensemble spreads reused; exposure hours and original coverage limitations preserved."}
    card.update({"confidence": updated, "confidence_policy": CONFIDENCE_POLICY, "confidence_revision": revision})
    simulation.update({"confidence_policy": CONFIDENCE_POLICY, "confidence_revision": revision})
    simulation["assumptions"] = [text for text in simulation["assumptions"] if "numeric score describes classifier agreement only" not in text]
    simulation["assumptions"].append("Annual confidence remains Low. The numeric score combines classifier agreement with recorded same-location historical support and validation limits; it is not a probability of correctness.")
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_dir / path.name, index=False)
    write_json(output_dir / "model_card.json", card)
    write_json(output_dir / "simulation_metadata.json", simulation)
    return frame


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rescore saved confidence evidence without changing exposure hours or claiming to retrain.")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = rescore_saved_artifacts(args.source_dir, args.output_dir)
    print(result.confidence_level.value_counts().to_string())
    print(result[["confidence_score", "n_similar_historical_hours"]].describe().to_string())
