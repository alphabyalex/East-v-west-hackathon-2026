"""Convert saved hourly model probabilities to expected system exposure hours.

This is a retrospective scoring summary, not a forecast of future dates. Annual
scenarios are produced separately by the existing seasonal simulation.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import fingerprint, write_json


def summarize_hours(predictions: pd.DataFrame, card: dict) -> dict:
    required = {"timestamp_utc", "location_id", "probability", "target"}
    members = [name for name in predictions if name.startswith("member_")]
    if not required.issubset(predictions) or len(members) < 2 or predictions.empty:
        raise ValueError("Hours estimates require saved test predictions, targets and at least two ensemble members.")
    frame = predictions.copy()
    times = [pd.Timestamp(value) for value in frame.timestamp_utc]
    if any(pd.isna(value) or value.tzinfo is None for value in times):
        raise ValueError("Prediction timestamps require an explicit timezone.")
    frame.timestamp_utc = pd.to_datetime(frame.timestamp_utc, utc=True)
    if not frame.timestamp_utc.eq(frame.timestamp_utc.dt.floor("h")).all():
        raise ValueError("Predictions must use hourly interval starts.")
    if frame.location_id.isna().any() or frame.location_id.astype(str).str.strip().eq("").any():
        raise ValueError("Prediction locations cannot be empty.")
    if frame.duplicated(["location_id", "timestamp_utc"]).any():
        raise ValueError("Duplicate location/hour predictions would double-count exposure.")
    values = frame[["probability", *members]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("Probabilities must be finite and within [0, 1]; missing predictions are not zero.")
    if not np.allclose(values[:, 0], values[:, 1:].mean(axis=1), rtol=1e-6, atol=1e-8):
        raise ValueError("Mean probability does not match the saved ensemble members.")
    if not frame.target.isin([0, 1]).all():
        raise ValueError("Held-out targets must be confirmed binary labels.")
    locations = {}
    for location, group in frame.groupby("location_id", sort=True):
        group = group.sort_values("timestamp_utc")
        first, last = group.timestamp_utc.iloc[0], group.timestamp_utc.iloc[-1]
        calendar_hours = int((last - first).total_seconds() / 3600) + 1
        member_hours = group[members].sum().to_numpy(dtype=float)
        periods = []
        for month, part in group.groupby(group.timestamp_utc.dt.strftime("%Y-%m")):
            periods.append({"month_utc": month, "scored_hours": len(part),
                            "expected_exposure_hours": float(part.probability.sum()),
                            "observed_target_hours": int(part.target.sum())})
        ranked = group.sort_values(["probability", "timestamp_utc"], ascending=[False, True]).head(24)
        reasons = []
        if (last - first).total_seconds() < 365 * 86400:
            reasons.append("At least one year of held-out reference history is required for annual simulation.")
        timeline = pd.DatetimeIndex(group.timestamp_utc)
        for month in range(1, 13):
            valid = any(timeline[i].month == month and timeline[i].hour == 0
                        and timeline[i + 167].month == month
                        and timeline[i + 167] - timeline[i] == pd.Timedelta(167, unit="h")
                        for i in range(max(0, len(timeline) - 167)))
            if not valid:
                reasons.append(f"No complete seven-day reference block in calendar month {month}.")
        locations[location] = {
            "start_utc": first.isoformat(), "end_exclusive_utc": (last + pd.Timedelta(1, unit="h")).isoformat(),
            "scored_hours": len(group), "calendar_hours": calendar_hours,
            "unscored_hours": calendar_hours - len(group),
            "expected_exposure_hours": float(group.probability.sum()),
            "observed_target_hours": int(group.target.sum()),
            "member_expected_hours_min": float(member_hours.min()),
            "member_expected_hours_max": float(member_hours.max()),
            "confidence": card["confidence"][location], "monthly": periods,
            "highest_scored_hours": [{"timestamp_utc": row.timestamp_utc.isoformat(),
                                      "probability": float(row.probability)} for row in ranked.itertuples()],
            "annual_simulation_ready": not reasons, "annual_blockers": reasons,
        }
    return {"status": "research_retrospective_expected_exposure", "model_version": card["model_version"],
            "source_type": "model", "ref": "Saved held-out hourly ensemble probabilities; sum(p * 1 hour).",
            "target_policy": card["policy"], "site_exposure_applied": False,
            "forecast_of_future_dates": False, "locations": locations,
            "limitations": [
                "Expected hours sum probabilities; they are not a count above an arbitrary probability threshold.",
                "Each target represents an hourly event/proxy indicator, not measured within-hour interruption duration.",
                "Only scored hours are included. Missing periods are neither zero-filled nor extrapolated to a year.",
                "Ensemble min/max describe variation in expected hours, not outcome quantiles or confidence intervals.",
                "Highest scored historical hours are not a schedule of future outages.",
                "Annual scenarios require separate seasonal simulation; site exposure remains a visible downstream assumption.",
            ]}


def prepare_hours(run_dir: Path) -> dict:
    card_path, prediction_path = run_dir / "model_card.json", run_dir / "test_predictions.parquet"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    result = summarize_hours(pd.read_parquet(prediction_path), card)
    result["input_hashes"] = {"model_card_sha256": fingerprint(card_path), "predictions_sha256": fingerprint(prediction_path)}
    write_json(run_dir / "hours_summary.json", result)
    rows = [{"location_id": location, **row} for location, info in result["locations"].items() for row in info["monthly"]]
    pd.DataFrame(rows).to_csv(run_dir / "monthly_expected_hours.csv", index=False)
    return result


def summarize_contract(trials: pd.DataFrame) -> dict:
    """Compute term quantiles from joint trials, never by adding yearly quantiles."""
    required = {"location_id", "simulation_id", "year_offset", "modeled_exposure_hours"}
    if not required.issubset(trials) or trials.empty:
        raise ValueError("Contract summary needs saved joint annual simulation trials.")
    if trials.duplicated(["location_id", "simulation_id", "year_offset"]).any():
        raise ValueError("Duplicate simulation year.")
    output = {}
    for location, group in trials.groupby("location_id", sort=True):
        table = group.pivot(index="simulation_id", columns="year_offset", values="modeled_exposure_hours")
        if not np.array_equal(table.columns.to_numpy(), np.arange(1, len(table.columns) + 1)) or len(table.columns) > 7:
            raise ValueError("Contract years must be contiguous from 1 through at most 7.")
        values = table.to_numpy(dtype=float)
        if len(table) < 1000 or not np.isfinite(values).all() or ((values < 0) | (values > 8760)).any():
            raise ValueError("Contract summary requires at least 1,000 complete, valid joint trials.")
        total = values.sum(axis=1)
        output[location] = {"term_years": len(table.columns), "simulations": len(table),
                            "expected_total_hours": float(total.mean()),
                            "p50_total_hours": float(np.quantile(total, .5)),
                            "p90_total_hours": float(np.quantile(total, .9)),
                            "p99_total_hours": float(np.quantile(total, .99))}
    return output
