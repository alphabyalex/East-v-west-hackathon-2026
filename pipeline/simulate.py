"""Experimental seasonal residual-block simulation and read-only API handoff.

Resample paired held-out probability paths and randomized probability residuals
in seven-day blocks. This keeps observed within-block event clustering. It assumes
future seasonal conditions resemble the reference history; there is no growth or
climate forecast. Annual tail probabilities have not been independently validated.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import ROOT

EXPOSURE_PATH = ROOT / "data" / "processed" / "exposure_by_location.parquet"
REQUIRED_COLUMNS = {
    "location_id", "year_offset", "p50_hours", "p90_hours", "p99_hours",
    "worst_contiguous_hours", "confidence_level", "confidence_score",
    "n_similar_historical_hours", "model_version",
}


class LocationNotFoundError(LookupError):
    pass


def longest_run(values: np.ndarray) -> int:
    padded = np.r_[0, values.astype(int), 0]
    edges = np.diff(padded)
    lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
    return int(lengths.max()) if len(lengths) else 0


def simulate_exposure(predictions: pd.DataFrame, confidence: dict, model_version: str,
                      *, seed: int = 2026, simulations: int = 2000, years: int = 7,
                      block_hours: int = 168) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    from pipeline.confidence import CONFIDENCE_POLICY, confidence_from_evidence

    if simulations < 1000:
        raise ValueError("Use at least 1,000 simulations when reporting p99; it remains an uncertain tail estimate.")
    if not 1 <= years <= 7 or not 24 <= block_hours <= 168 or block_hours % 24:
        raise ValueError("Use 1..7 years and a block length of 24..168 hours in complete days.")
    member_columns = [column for column in predictions if column.startswith("member_")]
    if len(member_columns) < 2:
        raise ValueError("Simulation needs predictions from at least two calibrated ensemble members.")
    rows, trials = [], []
    rng = np.random.default_rng(seed)
    month_hours = [31 * 24, 28 * 24, 31 * 24, 30 * 24, 31 * 24, 30 * 24,
                   31 * 24, 31 * 24, 30 * 24, 31 * 24, 30 * 24, 31 * 24]
    for location, group in predictions.groupby("location_id", sort=True):
        group = group.sort_values("timestamp_utc").reset_index(drop=True)
        times = pd.DatetimeIndex(pd.to_datetime(group.timestamp_utc, utc=True))
        if times.has_duplicates:
            raise ValueError("Simulation reference contains duplicate hours.")
        if not times.equals(times.floor("h")):
            raise ValueError("Simulation reference must use hourly interval-start timestamps.")
        if (times[-1] - times[0]).total_seconds() < 365 * 86400:
            raise ValueError(f"{location}: simulation needs at least one year of held-out reference history covering all seasons.")
        info = confidence[location]
        try:
            expected_confidence = confidence_from_evidence(info["mean_ensemble_probability_std"],
                info["n_similar_historical_hours"], info["limitations"])
            if info != expected_confidence:
                raise ValueError("Confidence evidence or policy differs.")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{location}: stale or invalid confidence. Recompute confidence with the current policy before simulation.") from error
        probability = group[member_columns].to_numpy(dtype=float)
        mean = group.probability.to_numpy(dtype=float)
        target = group.target.to_numpy()
        if not np.isfinite(probability).all() or np.any((probability < 0) | (probability > 1)):
            raise ValueError("Member probabilities must be finite and between zero and one.")
        if not np.isfinite(mean).all() or np.any((mean < 0) | (mean > 1)) or not np.isin(target, [0, 1]).all():
            raise ValueError("Reference means and binary labels are invalid.")
        mean = np.clip(mean, 1e-9, 1 - 1e-9)
        random = rng.random(len(group))
        residual = np.where(target == 1, random * mean, mean + random * (1 - mean))
        starts_by_month = {}
        for month in range(1, 13):
            starts = [index for index in range(len(group) - block_hours + 1)
                      if times[index].month == month and times[index].hour == 0
                      and times[index + block_hours - 1].month == month
                      and times[index + block_hours - 1] - times[index] == pd.Timedelta(block_hours - 1, unit="h")]
            if not starts:
                raise ValueError(f"{location}: no complete {block_hours}-hour reference block in month {month}; do not invent missing seasons.")
            starts_by_month[month] = np.asarray(starts)
        annual = np.zeros((simulations, years), dtype=int)
        longest = np.zeros_like(annual)
        for trial in range(simulations):
            member = int(rng.integers(len(member_columns)))  # One epistemic draw for the whole contract.
            for year in range(years):
                pieces = []
                for month, hours in enumerate(month_hours, start=1):
                    remaining = hours
                    while remaining:
                        start = int(rng.choice(starts_by_month[month]))
                        length = min(block_hours, remaining)
                        pieces.append(residual[start:start + length] < probability[start:start + length, member])
                        remaining -= length
                events = np.concatenate(pieces)
                annual[trial, year] = events.sum()
                longest[trial, year] = longest_run(events)
                trials.append({"location_id": location, "simulation_id": trial,
                               "year_offset": year + 1, "modeled_exposure_hours": int(events.sum()),
                               "longest_episode_hours": int(longest[trial, year])})
        for year in range(years):
            quantiles = np.quantile(annual[:, year], [.5, .9, .99])
            rows.append({"location_id": location, "year_offset": year + 1,
                         "p50_hours": float(quantiles[0]), "p90_hours": float(quantiles[1]),
                         "p99_hours": float(quantiles[2]),
                         # The contract is ambiguous about this statistic: choose and document p99.
                         "worst_contiguous_hours": float(np.quantile(longest[:, year], .99)),
                         "confidence_level": "Low", "confidence_score": info["score"],
                         "n_similar_historical_hours": info["n_similar_historical_hours"],
                         "model_version": model_version})
    metadata = {
        "status": "experimental_unvalidated_annual_tails",
        "method": "seasonal_joint_probability_and_randomized_residual_block_bootstrap",
        "simulations": simulations, "years": years, "seed": seed, "block_hours": block_hours,
        "hours_per_year": 8760, "site_exposure_applied": False,
        "source_type": "model", "ref": f"pipeline/simulate.py model_version={model_version}",
        "confidence_policy": CONFIDENCE_POLICY,
        "assumptions": [
            "Stationary month-specific grid conditions and label definition; no load-growth or climate forecast.",
            "Seven-day blocks preserve within-block episodes; resampling can split or join episodes at block boundaries.",
            "One ensemble member per simulated contract; independently resampled seasonal blocks across years.",
            "365-day comparison years, with February fixed to 28 days.",
            "worst_contiguous_hours is the p99 of annual longest modeled episodes, not a guaranteed upper bound.",
            "Annual exposure confidence is capped Low until annual tail validation; its numeric score combines classifier agreement with same-location historical support and validation limits.",
            "Quantiles describe the selected system-event/proxy target, not an individual site's actual interruption.",
        ],
    }
    return pd.DataFrame(rows), pd.DataFrame(trials), metadata


def get_location_estimate(location_id: str, *, path: Path | None = None) -> dict:
    """Read precomputed output only. Never fetch, fit, or simulate in this function."""
    target_path = EXPOSURE_PATH if path is None else Path(path)
    if not target_path.exists():
        raise FileNotFoundError(f"Precomputed exposure is missing: {target_path}. Run the offline simulation first.")
    frame = pd.read_parquet(target_path)
    if not REQUIRED_COLUMNS.issubset(frame.columns):
        raise ValueError(f"Invalid exposure table; missing {sorted(REQUIRED_COLUMNS - set(frame.columns))}")
    group = frame[frame.location_id == location_id].sort_values("year_offset")
    if group.empty:
        raise LocationNotFoundError(location_id)
    years = group.year_offset.to_numpy()
    if not np.array_equal(years, np.arange(1, len(group) + 1)) or len(group) > 7:
        raise ValueError("Exposure years must be unique and contiguous from 1 through at most 7.")
    numeric = group[["p50_hours", "p90_hours", "p99_hours", "worst_contiguous_hours"]].to_numpy()
    if not np.isfinite(numeric).all() or (numeric < 0).any() or (numeric > 8784).any():
        raise ValueError("Invalid exposure hours.")
    if (np.diff(numeric[:, :3], axis=1) < 0).any():
        raise ValueError("Exposure quantiles are out of order.")
    if group.model_version.nunique() != 1:
        raise ValueError("Mixed model versions for the same location.")
    if group.model_version.isna().any() or not group.model_version.map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError("model_version must be a nonempty string.")
    for column in ("confidence_level", "confidence_score", "n_similar_historical_hours"):
        if group[column].nunique(dropna=False) != 1:
            raise ValueError(f"Conflicting {column} across annual rows.")
    first = group.iloc[0]
    score, count = float(first.confidence_score), float(first.n_similar_historical_hours)
    if first.confidence_level not in {"High", "Medium", "Low"} or not np.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Invalid confidence.")
    if not np.isfinite(count) or count < 0 or count != int(count):
        raise ValueError("Invalid historical precedent count.")
    return {"location_id": location_id,
            "by_year": [{"year_offset": int(row.year_offset), "p50_hours": float(row.p50_hours),
                         "p90_hours": float(row.p90_hours), "p99_hours": float(row.p99_hours),
                         "worst_contiguous_hours": float(row.worst_contiguous_hours)}
                        for row in group.itertuples()],
            "confidence": {"level": first.confidence_level, "score": score,
                           "n_similar_historical_hours": int(count)},
            "model_version": str(first.model_version)}
