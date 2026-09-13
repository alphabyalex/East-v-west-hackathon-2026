"""Shared paths, input validation, and provenance for the offline ML pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ML_DIR = ROOT / "data" / "processed" / "ml"
OBSERVATIONS = (
    "load_mw", "available_reserves_mw", "required_reserves_mw",
    "wind_mw", "solar_mw", "temperature_c", "temperature_area_min_c", "temperature_area_max_c", "lmp_usd_mwh",
    "binding_constraint_count", "generation_outage_mw", "net_import_mw",
    "outage_outlook_mw", "gas_outage_outlook_mw", "coal_outage_outlook_mw", "wind_outage_outlook_mw",
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_hourly(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    required = {"timestamp_utc", "location_id", "load_mw"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Hourly input is missing columns: {sorted(required - set(frame.columns))}")
    if frame.empty:
        raise ValueError("Hourly input is empty.")
    # Require explicit timezone information; never silently treat local time as UTC.
    values = [pd.Timestamp(value) for value in frame["timestamp_utc"]]
    if any(pd.isna(value) or value.tzinfo is None for value in values):
        raise ValueError("Every timestamp_utc must include Z or an explicit UTC offset.")
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    if not frame["timestamp_utc"].eq(frame["timestamp_utc"].dt.floor("h")).all():
        raise ValueError("Input must use hourly interval-start timestamps.")
    if frame["location_id"].isna().any() or frame["location_id"].astype(str).str.strip().eq("").any():
        raise ValueError("location_id cannot be empty.")
    frame["location_id"] = frame["location_id"].astype(str)
    if frame.duplicated(["location_id", "timestamp_utc"]).any():
        raise ValueError("Duplicate location/hour rows: reconcile source revisions before training.")
    for column in set(OBSERVATIONS + ("event_active",)) & set(frame.columns):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if np.isinf(frame[column]).any():
            raise ValueError(f"{column} contains infinity.")
    if (frame["load_mw"].dropna() < 0).any():
        raise ValueError("load_mw cannot be negative.")
    return frame.sort_values(["location_id", "timestamp_utc"]).reset_index(drop=True)


def read_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("Policy must be a JSON object.")
    for key in ("operator", "data_ref", "label_ref", "label_method"):
        if not isinstance(policy.get(key), str) or not policy[key].strip():
            raise ValueError(f"Policy requires a nonempty {key}.")
    if policy["operator"] != "SPP":
        raise ValueError("This first version follows the repository's SPP scope.")
    methods = {"observed_event", "reserve_shortfall", "binding_constraint", "scarcity_price"}
    if policy["label_method"] not in methods:
        raise ValueError(f"label_method must be one of {sorted(methods)}")
    if policy["label_method"] == "scarcity_price":
        threshold = policy.get("price_threshold_usd_mwh")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not np.isfinite(threshold):
            raise ValueError("Scarcity labels require an explicit finite price_threshold_usd_mwh.")
    return policy
