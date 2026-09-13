"""Offline LightGBM ensemble with a separate chronological sigmoid calibration set.

Calibration policy is a draft requiring the human review specified in AGENTS.md.
The final chronological test set never fits a tree, calibrator, or preprocessing.
"""
from __future__ import annotations

from importlib.metadata import version

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score


def chronological_split(frame: pd.DataFrame, embargo_hours: int = 24) -> dict[str, pd.DataFrame]:
    if embargo_hours < 1:
        raise ValueError("At least one hour of embargo is required.")
    timestamps = pd.DatetimeIndex(frame["timestamp_utc"].unique()).sort_values()
    if len(timestamps) < 240:
        raise ValueError("Need at least 240 distinct labeled hours for train/calibration/test splitting.")
    cut1, cut2 = timestamps[int(len(timestamps) * .6)], timestamps[int(len(timestamps) * .8)]
    gap = pd.Timedelta(embargo_hours, unit="h")
    times = frame["timestamp_utc"]
    splits = {
        "train": frame[times < cut1],
        "calibration": frame[(times >= cut1 + gap) & (times < cut2)],
        "test": frame[times >= cut2 + gap],
    }
    for name, subset in splits.items():
        counts = subset["target"].value_counts()
        if len(subset) < 72 or counts.get(0, 0) < 5 or counts.get(1, 0) < 5:
            raise ValueError(
                f"{name} has {len(subset)} hours, {counts.get(1, 0)} positive and "
                f"{counts.get(0, 0)} negative labels. Need >=72 rows and >=5 of each class. "
                "Collect more history or review the target; do not fabricate or randomly move events."
            )
    return {name: subset.reset_index(drop=True) for name, subset in splits.items()}


def evaluate(y: np.ndarray, probability: np.ndarray) -> dict:
    result = {
        "brier_score": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, probability, labels=[0, 1])),
        "event_rate": float(np.mean(y)),
        "mean_prediction": float(np.mean(probability)),
        "n_hours": int(len(y)),
        "positive_hours": int(np.sum(y)),
        "roc_auc": float(roc_auc_score(y, probability)) if len(np.unique(y)) == 2 else None,
        "average_precision": float(average_precision_score(y, probability)) if np.sum(y) else None,
    }
    bins = np.minimum((probability * 10).astype(int), 9)
    result["reliability_bins"] = [
        {"n": int(np.sum(bins == index)),
         "mean_prediction": float(probability[bins == index].mean()),
         "observed_fraction": float(y[bins == index].mean())}
        for index in range(10) if np.any(bins == index)
    ]
    return result


def predict_members(bundle: dict, frame: pd.DataFrame) -> np.ndarray:
    features = frame[bundle["feature_names"]].to_numpy(dtype=float)
    return np.column_stack([
        calibrator.predict_proba(model.predict(features, raw_score=True).reshape(-1, 1))[:, 1]
        for model, calibrator in bundle["members"]
    ])


def fit_ensemble(frame: pd.DataFrame, feature_names: list[str], *, seed: int = 2026,
                 members: int = 15, trees: int = 100, embargo_hours: int = 24) -> tuple[dict, dict, pd.DataFrame]:
    if members < 2 or trees < 1:
        raise ValueError("Use at least two ensemble members and one tree.")
    splits = chronological_split(frame, embargo_hours)
    training, calibration, test = (splits[name] for name in ("train", "calibration", "test"))
    x_train = training[feature_names].to_numpy(dtype=float)
    x_cal = calibration[feature_names].to_numpy(dtype=float)
    y_train, y_cal = training.target.to_numpy(), calibration.target.to_numpy()
    day_groups = list(training.groupby(training.timestamp_utc.dt.floor("D")).indices.values())
    ensemble = []
    settings = dict(n_estimators=trees, num_leaves=7, max_depth=3, learning_rate=.05,
                    min_child_samples=30, reg_lambda=1.0, verbosity=-1, n_jobs=1,
                    deterministic=True, force_col_wise=True)
    for member in range(members):
        rng = np.random.default_rng(seed + member)
        for _ in range(20):
            sample = np.concatenate([day_groups[index] for index in rng.integers(0, len(day_groups), len(day_groups))])
            if len(np.unique(y_train[sample])) == 2:
                break
        else:
            raise ValueError("Day-block bootstrap repeatedly produced a single class. More independent events are needed.")
        model = LGBMClassifier(**settings, random_state=seed + member)
        model.fit(x_train[sample], y_train[sample])
        # Sigmoid calibration sees only the middle time window. No random CV.
        calibrator = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=seed)
        calibrator.fit(model.predict(x_cal, raw_score=True).reshape(-1, 1), y_cal)
        ensemble.append((model, calibrator))
    medians = training[feature_names].median().fillna(0).to_numpy()
    scales = training[feature_names].std().fillna(1).replace(0, 1).to_numpy()
    bundle = {
        "members": ensemble, "feature_names": feature_names,
        "density_medians": medians, "density_scales": scales,
        "density_training": np.nan_to_num((x_train - medians) / scales, nan=0.0),
        "density_training_locations": training.location_id.to_numpy(),
    }
    probabilities = predict_members(bundle, test)
    mean = probabilities.mean(axis=1)
    raw_mean = np.column_stack([model.predict_proba(test[feature_names].to_numpy())[:, 1]
                                for model, _ in ensemble]).mean(axis=1)
    prevalence = training.groupby("location_id").target.mean()
    baseline = test.location_id.map(prevalence).fillna(y_train.mean()).to_numpy()
    metrics = evaluate(test.target.to_numpy(), mean)
    baseline_metrics = evaluate(test.target.to_numpy(), baseline)
    skill = 1 - metrics["brier_score"] / baseline_metrics["brier_score"]
    report = {
        "status": "research_only_pending_label_and_calibration_review",
        "prediction_task": "Target-hour system event/proxy from previous observed hours; not site curtailment.",
        "settings": {**settings, "seed": seed, "ensemble_members": members,
                     "bootstrap_block_hours": 24, "embargo_hours": embargo_hours,
                     "split_fractions": [.6, .2, .2], "calibration": "sigmoid on separate middle time window"},
        "versions": {name: version(name) for name in ("numpy", "pandas", "scikit-learn", "lightgbm", "pyarrow")},
        "splits": {name: {"start": str(part.timestamp_utc.min()), "end": str(part.timestamp_utc.max()),
                          "rows": len(part), "positive_hours": int(part.target.sum())}
                   for name, part in splits.items()},
        "test": metrics, "uncalibrated_test": evaluate(test.target.to_numpy(), raw_mean),
        "baseline_test": baseline_metrics, "brier_skill_vs_train_prevalence": float(skill),
        "test_by_location": {}, "feature_names": feature_names,
        "mean_feature_importance": dict(zip(feature_names, np.mean(
            [model.feature_importances_ for model, _ in ensemble], axis=0).tolist())),
        "warnings": [],
    }
    if skill <= 0:
        report["warnings"].append("The calibrated model does not beat the training-prevalence baseline on held-out Brier score.")
    if (test.timestamp_utc.max() - test.timestamp_utc.min()).days < 365:
        report["warnings"].append("The held-out period covers less than a year; seasonal generalization is unverified.")
    predictions = test[["timestamp_utc", "location_id", "target"]].copy()
    predictions["probability"] = mean
    predictions["ensemble_std"] = probabilities.std(axis=1)
    for member in range(members):
        predictions[f"member_{member}"] = probabilities[:, member]
    for location, positions in test.groupby("location_id").indices.items():
        local_metrics = evaluate(test.target.to_numpy()[positions], mean[positions])
        local_base = evaluate(test.target.to_numpy()[positions], baseline[positions])
        local_metrics["brier_skill_vs_train_prevalence"] = (
            float(1 - local_metrics["brier_score"] / local_base["brier_score"])
            if local_base["brier_score"] > 0 else None)
        report["test_by_location"][location] = local_metrics
    bundle["report"] = report
    return bundle, report, predictions
