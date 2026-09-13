"""Draft label definitions for human review; these are never site curtailment labels.

No automatic fallback or learned threshold: the input policy names the target.
Missing observations stay unknown, including missing event records.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LABEL_COLUMNS = {
    "observed_event": ("event_active",),
    "reserve_shortfall": ("available_reserves_mw", "required_reserves_mw"),
    "binding_constraint": ("binding_constraint_count",),
    "scarcity_price": ("lmp_usd_mwh",),
}


def label_hours(frame: pd.DataFrame, policy: dict) -> pd.DataFrame:
    method = policy["label_method"]
    columns = LABEL_COLUMNS[method]
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{method} needs {sorted(missing)}; do not substitute a different label silently.")
    result = frame.copy()
    known = result[list(columns)].notna().all(axis=1)
    if method == "observed_event":
        if not result.loc[known, "event_active"].isin([0, 1]).all():
            raise ValueError("event_active must be 0, 1, or missing. Zero means confirmed observation coverage.")
        event = result["event_active"].eq(1)
    elif method == "reserve_shortfall":
        if (result.loc[known, list(columns)] < 0).any().any():
            raise ValueError("Reserve quantities and requirements must be nonnegative MW.")
        event = result["available_reserves_mw"] < result["required_reserves_mw"]
    elif method == "binding_constraint":
        counts = result.loc[known, "binding_constraint_count"]
        if (counts < 0).any() or not counts.eq(np.floor(counts)).all():
            raise ValueError("binding_constraint_count must be a nonnegative integer.")
        event = result["binding_constraint_count"] > 0
    else:
        event = result["lmp_usd_mwh"] > policy["price_threshold_usd_mwh"]
    result["target"] = event.astype(float).where(known)
    result["label_source_ref"] = policy["label_ref"]
    return result
