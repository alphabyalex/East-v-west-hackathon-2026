"""Ensemble agreement with explicitly documented coverage and skill limitations."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors

from pipeline.train import predict_members

CONFIDENCE_POLICY = {
    "agreement_score": "clip(1 - 2 * mean(ensemble probability standard deviation), 0, 1)",
    "similarity_radius_rms_standard_deviations": .5,
    "query_sample_limit": 256,
    "precedent_count_aggregation": "median count of same-location training hours within radius of sampled test hours",
    "high_score_min": .9, "medium_score_min": .7,
    "high_precedent_min": 100, "medium_precedent_min": 20,
    "minimum_heldout_span_days_for_above_low": 365,
    "minimum_positive_test_hours_for_above_low": 20,
    "meaning": "Ensemble agreement and historical support for the proxy classifier, not a probability of correctness or calibrated coverage of annual tails.",
}


def estimate_confidence(bundle: dict, frame: pd.DataFrame) -> dict:
    output = {}
    for location, group in frame.groupby("location_id", sort=True):
        sample = group.iloc[np.linspace(0, len(group) - 1, min(len(group), 256), dtype=int)]
        values = sample[bundle["feature_names"]].to_numpy(dtype=float)
        standardized = np.nan_to_num((values - bundle["density_medians"]) / bundle["density_scales"], nan=0.0)
        reference = bundle["density_training"][bundle["density_training_locations"] == location]
        if len(reference):
            neighbors = NearestNeighbors(radius=.5 * np.sqrt(values.shape[1]), n_jobs=1).fit(reference)
            counts = [len(indices) for indices in neighbors.radius_neighbors(standardized, return_distance=False)]
            precedent = int(np.median(counts))
        else:
            precedent = 0
        spread = float(predict_members(bundle, sample).std(axis=1).mean())
        score = float(np.clip(1 - 2 * spread, 0, 1))
        level = "High" if score >= .9 and precedent >= 100 else "Medium" if score >= .7 and precedent >= 20 else "Low"
        reasons = []
        local_metrics = bundle["report"]["test_by_location"][location]
        if local_metrics["brier_skill_vs_train_prevalence"] is None or local_metrics["brier_skill_vs_train_prevalence"] <= 0:
            reasons.append("does_not_beat_baseline")
        if (group.timestamp_utc.max() - group.timestamp_utc.min()).days < 365:
            reasons.append("less_than_one_year_of_heldout_history")
        if int(group.target.sum()) < 20:
            reasons.append("fewer_than_20_positive_test_hours")
        if reasons:
            level = "Low"
        output[location] = {"level": level, "score": score,
                            "n_similar_historical_hours": precedent,
                            "mean_ensemble_probability_std": spread,
                            "limitations": reasons, "source_ref": "pipeline/confidence.py:CONFIDENCE_POLICY"}
    return output
