"""Shared confidence arithmetic for offline training and read-only API validation.

Keep this module independent of the ML runtime. The public producer names remain
re-exported by pipeline.confidence for saved provenance and existing callers.
"""
from math import isfinite
from numbers import Integral

CONFIDENCE_POLICY = {
    "version": 2,
    "agreement_score": "clip(1 - 2 * mean(ensemble probability standard deviation), 0, 1)",
    "historical_support_score": "n_similar_historical_hours / (n_similar_historical_hours + medium_precedent_min)",
    "confidence_score": "agreement_score * historical_support_score; capped at low_score_max when validation/coverage limitations apply",
    "support_scale_source": "Heuristic using the existing 20-hour minimum precedent policy; not fitted to test outcomes or a calibrated probability.",
    "similarity_radius_rms_standard_deviations": .5,
    "query_sample_limit": 256,
    "precedent_count_aggregation": "median count of same-location training hours within radius of sampled test hours",
    "high_score_min": .9, "medium_score_min": .7,
    "high_precedent_min": 100, "medium_precedent_min": 20,
    "low_score_max": .69,
    "minimum_heldout_span_days_for_above_low": 365,
    "minimum_positive_test_hours_for_above_low": 20,
    "meaning": "Ensemble agreement and historical support for the proxy classifier, not a probability of correctness or calibrated coverage of annual tails.",
}


def confidence_from_evidence(spread: float, precedent: int, limitations: list[str]) -> dict:
    """Score recorded evidence consistently for new runs and legacy artifacts."""
    if isinstance(spread, bool) or not isinstance(spread, (int, float)) or not isfinite(spread) or not 0 <= spread <= .5:
        raise ValueError("Ensemble probability spread must be finite and between 0 and 0.5.")
    if isinstance(precedent, bool) or not isinstance(precedent, Integral) or precedent < 0:
        raise ValueError("Historical precedent must be a nonnegative integer count.")
    if not isinstance(limitations, list) or any(not isinstance(reason, str) for reason in limitations):
        raise ValueError("Confidence limitations must be a list of reason strings.")
    policy = CONFIDENCE_POLICY
    agreement = float(min(max(1 - 2 * spread, 0), 1))
    support = float(precedent / (precedent + policy["medium_precedent_min"]))
    score = agreement * support
    reasons = list(dict.fromkeys(limitations))
    if precedent < policy["medium_precedent_min"] and "insufficient_same_location_precedent" not in reasons:
        reasons.append("insufficient_same_location_precedent")
    if reasons:
        score = min(score, policy["low_score_max"])
    level = ("High" if score >= policy["high_score_min"] and precedent >= policy["high_precedent_min"]
             else "Medium" if score >= policy["medium_score_min"] and precedent >= policy["medium_precedent_min"] else "Low")
    if reasons:
        level = "Low"
    return {"level": level, "score": float(score), "agreement_score": agreement, "historical_support_score": support,
            "n_similar_historical_hours": int(precedent), "mean_ensemble_probability_std": float(spread),
            "limitations": reasons, "policy_version": policy["version"],
            "source_ref": "pipeline/confidence.py:CONFIDENCE_POLICY"}

