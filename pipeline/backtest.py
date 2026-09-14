"""Compare saved demand-proxy models on identical, later observed hours.

Offline only: no fitting, calibration, network access, or artifact promotion.
This evaluates the declared high-demand proxy, never actual site interruptions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from pipeline.common import fingerprint, read_hourly, write_json
from pipeline.features import build_features
from pipeline.train import evaluate, predict_members


def _timestamp(value: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp != stamp.floor("h"):
        raise ValueError("Evaluation endpoints require hourly timestamps with explicit timezones.")
    return stamp.tz_convert("UTC")


def _target(card: dict) -> dict:
    policy = card["policy"]
    if (policy.get("target_name") != "high_demand_stress_proxy"
            or policy.get("target_source_type") != "assumption"
            or policy.get("label_method") != "observed_event" or policy.get("operator") != "SPP"):
        raise ValueError("This comparison supports only the explicitly declared SPP high-demand proxy.")
    target = policy["demand_proxy"]
    threshold = target["thresholds_mw"]
    if (set(threshold) != {"SPP_SYSTEM"} or type(threshold["SPP_SYSTEM"]) not in (int, float)
            or not np.isfinite(threshold["SPP_SYSTEM"]) or threshold["SPP_SYSTEM"] <= 0):
        raise ValueError("A finite positive frozen SPP_SYSTEM threshold is required.")
    return {key: target[key] for key in ("thresholds_mw", "quantile", "reference_start", "reference_end_exclusive")}


def paired_brier_interval(times: pd.Series, target: np.ndarray, baseline: np.ndarray,
                          candidate: np.ndarray, *, seed: int = 2026) -> dict:
    """Resample paired seven-day blocks; preserve shared hours and episode order."""
    delta = (candidate - target) ** 2 - (baseline - target) ** 2
    blocks = ((times - times.min()).dt.total_seconds() // (7 * 24 * 3600)).to_numpy()
    grouped = pd.DataFrame({"block": blocks, "delta": delta}).groupby("block").delta.agg(["sum", "count"])
    result = {"candidate_minus_baseline": float(delta.mean()), "n_blocks": len(grouped),
              "method": "Paired seven-day block bootstrap; descriptive retrospective proxy comparison.",
              "seed": seed, "replicates": 2000, "interval_95": None}
    if len(grouped) >= 8:
        sample = np.random.default_rng(seed).integers(0, len(grouped), size=(2000, len(grouped)))
        means = grouped["sum"].to_numpy()[sample].sum(axis=1) / grouped["count"].to_numpy()[sample].sum(axis=1)
        result["interval_95"] = np.quantile(means, [.025, .975]).tolist()
    return result


def compare_runs(run_dirs: list[Path], hourly_path: Path, start_utc: str, end_utc: str, out: Path) -> dict:
    """Score trusted local bundles with a frozen target on a common later window."""
    if len(run_dirs) < 2 or len({path.resolve() for path in run_dirs}) != len(run_dirs):
        raise ValueError("Provide at least two different saved runs.")
    if out.exists():
        raise ValueError("Comparison output already exists; choose a new directory.")
    start, end = _timestamp(start_utc), _timestamp(end_utc)
    if end <= start:
        raise ValueError("Evaluation end must follow start (end is exclusive).")
    cards = [json.loads((path / "model_card.json").read_text(encoding="utf-8")) for path in run_dirs]
    target_policy = _target(cards[0])
    for card in cards:
        if _target(card) != target_policy:
            raise ValueError("Runs must use the same frozen target, threshold, and reference period.")
        # Reject overlap with either fitting phase, even if a caller misnames a
        # training window as a test. The usual chronological embargo still applies.
        fitted_until = max(_timestamp(card["splits"][part]["end"]) for part in ("train", "calibration"))
        embargo = card["settings"]["embargo_hours"]
        if type(embargo) is not int or embargo < 1 or start <= fitted_until + pd.Timedelta(embargo, unit="h"):
            raise ValueError("Evaluation overlaps training/calibration or its embargo.")

    hourly = read_hourly(hourly_path)
    if set(hourly.location_id) != {"SPP_SYSTEM"} or {"target", "event_active"}.intersection(hourly):
        raise ValueError("Use unlabeled SPP_SYSTEM observations; existing labels must not be overwritten.")
    threshold = target_policy["thresholds_mw"]["SPP_SYSTEM"]
    hourly["target"] = hourly.load_mw.ge(threshold).astype(float).where(hourly.load_mw.notna())
    # Build lags before slicing the evaluation period, using only earlier hours.
    features, names = build_features(hourly, cards[0]["policy"])
    features = features[(features.timestamp_utc >= start) & (features.timestamp_utc < end)].reset_index(drop=True)
    if features.empty:
        raise ValueError("No observed evaluation hours remain after feature filtering.")
    observed = features.target.to_numpy()
    predictions = features[["timestamp_utc", "location_id", "target"]].copy()
    results = []
    for index, (run_dir, card) in enumerate(zip(run_dirs, cards)):
        bundle = joblib.load(run_dir / "model.joblib")  # Trusted local artifacts only.
        expected = bundle["feature_names"]
        if (bundle["report"] != card or not isinstance(expected, list) or not expected
                or any(not isinstance(name, str) for name in expected)
                or len(set(expected)) != len(expected) or not set(expected).issubset(names)
                or ("feature_names" in card and card["feature_names"] != expected)):
            raise ValueError("Saved model/card or input feature schema do not match.")
        if set(bundle["density_training_locations"]) != {"SPP_SYSTEM"}:
            raise ValueError("Comparison requires an SPP_SYSTEM-only training run.")
        # A richer input can evaluate an earlier model alongside a candidate
        # with added sensors. Each gets only its own recorded feature schema.
        probability = predict_members(bundle, features[[*expected, "timestamp_utc", "location_id", "target"]]).mean(axis=1)
        if not np.isfinite(probability).all() or not ((probability >= 0) & (probability <= 1)).all():
            raise ValueError("Saved model returned invalid probabilities.")
        predictions[f"run_{index}_probability"] = probability
        training = card["splits"]["train"]
        prevalence = training["positive_hours"] / training["rows"]
        results.append({"run_dir": str(run_dir), "model_version": card["model_version"],
                        "model_sha256": fingerprint(run_dir / "model.joblib"),
                        "model_card_sha256": fingerprint(run_dir / "model_card.json"),
                        "feature_names": expected,
                        "training_hours": training["rows"], "metrics": evaluate(observed, probability),
                        "training_prevalence_baseline": evaluate(observed, np.full(len(observed), prevalence))})
    paired = [paired_brier_interval(features.timestamp_utc, observed,
                                   predictions.run_0_probability.to_numpy(), predictions[f"run_{i}_probability"].to_numpy())
              for i in range(1, len(run_dirs))]
    expected_hours = int((end - start).total_seconds() / 3600)
    report = {"status": "retrospective_high_demand_proxy_comparison", "hourly_sha256": fingerprint(hourly_path),
              "hourly_path": str(hourly_path), "start_utc": start.isoformat(), "end_exclusive_utc": end.isoformat(),
              "expected_hours": expected_hours, "scored_hours": len(features), "unscored_hours": expected_hours - len(features),
              "frozen_target": target_policy, "runs": results, "paired_brier_changes_vs_first_run": paired,
              "limitations": ["High demand is an assumed stress proxy, not an observed emergency or site interruption.",
                              "Reanalysis weather is retrospective; historical publication availability is not established.",
                              "All models use the identical observed evaluation hours. No fitting or calibration occurs here.",
                              "Comparison does not promote a model or validate annual exposure tails."]}
    out.mkdir(parents=True)
    predictions.to_parquet(out / "predictions.parquet", index=False)
    report["predictions_sha256"] = fingerprint(out / "predictions.parquet")
    write_json(out / "comparison.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", action="append", type=Path, required=True)
    parser.add_argument("--hourly", type=Path, required=True)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-exclusive-utc", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = compare_runs(args.run_dir, args.hourly, args.start_utc, args.end_exclusive_utc, args.out)
    print(json.dumps({"scored_hours": report["scored_hours"], "runs": [
        {"model_version": run["model_version"], "brier_score": run["metrics"]["brier_score"]} for run in report["runs"]],
        "paired_brier_changes_vs_first_run": report["paired_brier_changes_vs_first_run"]}, indent=2))


if __name__ == "__main__":
    main()
