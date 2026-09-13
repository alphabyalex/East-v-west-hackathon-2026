"""Explicit high-demand stress proxy, separate from measured grid emergencies.

This configurable assumption identifies high aggregate demand. It does not measure
local transmission headroom, reserve sufficiency, or actual load curtailments.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import fingerprint, read_hourly, write_json
from pipeline.features import build_features
from pipeline.train import chronological_split


def prepare_demand_proxy(hourly: Path, out: Path, policy_path: Path, *, reference_end: str,
                         quantile: float = .95) -> dict:
    if out.exists() or policy_path.exists():
        raise ValueError("Choose new output and policy paths to preserve earlier datasets.")
    if out.resolve() == policy_path.resolve() or hourly.resolve() in {out.resolve(), policy_path.resolve()}:
        raise ValueError("Input, output and policy must be separate files.")
    if isinstance(quantile, bool) or not np.isfinite(quantile) or not .5 < quantile < 1:
        raise ValueError("Demand reference quantile must be greater than 0.5 and less than 1.")
    end = pd.Timestamp(reference_end)
    if pd.isna(end) or end.tzinfo is None:
        raise ValueError("Reference end requires an explicit timezone.")
    frame = read_hourly(hourly)
    if "event_active" in frame or "target" in frame:
        raise ValueError("Existing labels cannot be replaced with a demand proxy.")
    reference = frame[frame.timestamp_utc < end]
    thresholds = {}
    for location, group in frame.groupby("location_id"):
        past = reference[reference.location_id.eq(location)].dropna(subset=["load_mw"])
        if len(past) < 8760 or (past.timestamp_utc.max() - past.timestamp_utc.min()).days < 365:
            raise ValueError("Use at least one year of known reference load across seasons for every location.")
        thresholds[location] = float(past.load_mw.quantile(quantile))
    frame["event_active"] = frame.load_mw.ge(frame.location_id.map(thresholds)).astype(float).where(frame.load_mw.notna())
    policy = {"operator": "SPP", "label_method": "observed_event",
              "data_ref": f"{hourly}; sha256={fingerprint(hourly)}",
              "label_ref": f"ASSUMPTION: high aggregate demand >= reference {quantile:.6g} quantile; {policy_path}",
              "target_name": "high_demand_stress_proxy", "target_source_type": "assumption",
              "target_description": "High aggregate demand proxy; not an emergency, network-overload measurement, or site interruption.",
              "demand_proxy": {"quantile": quantile, "thresholds_mw": thresholds,
                               "reference_end_exclusive": end.isoformat(),
                               "reference_start": str(reference.timestamp_utc.min()),
                               "method": "Fixed per-location empirical quantile, fit only within the training period."}}
    # The reference cannot include calibration/test data. Check using the exact
    # production feature filtering and chronological split, including missingness.
    labeled = frame.assign(target=frame.event_active)
    features, _ = build_features(labeled, policy)
    splits = chronological_split(features)
    train_end = splits["train"].timestamp_utc.max() + pd.Timedelta(1, unit="h")
    if end > train_end:
        raise ValueError("The demand threshold reference reaches beyond the training window.")
    policy["demand_proxy"]["split_counts"] = {name: {"hours": len(part), "positive_hours": int(part.target.sum())}
                                               for name, part in splits.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out, index=False)
    write_json(policy_path, policy)
    return policy
