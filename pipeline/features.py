"""Predict the target hour using observations ending before that hour.

Source columns defining a proxy label are excluded, including their lags.
No backward fill, future interpolation, or same-hour observations are used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import OBSERVATIONS
from pipeline.label import LABEL_COLUMNS

TEMPERATURE_FEATURE_POLICY = {
    "ref": "pipeline/features.py:TEMPERATURE_FEATURE_POLICY",
    "features": "1/24/168-hour lags and previous-24-hour mean/std for mean and regional min/max temperature; previous-day mean-temperature min/max and signed 24-hour change.",
    "cutoff": "All observed temperature features end before the target hour; no same-hour temperatures or future daily extrema.",
    "effect": "Learned by the classifier from independent event/proxy labels; no hardcoded hot/cold cutoff probability or monotonicity assumption.",
    "availability": "Reanalysis inputs support retrospective study; historical timestamps alone do not verify real-time availability.",
}


def build_features(labeled: pd.DataFrame, policy: dict, *, require_target: bool = True) -> tuple[pd.DataFrame, list[str]]:
    excluded = set(LABEL_COLUMNS[policy["label_method"]])
    signals = [column for column in OBSERVATIONS if column in labeled and column not in excluded]
    pieces = []
    feature_names = []
    for location, group in labeled.groupby("location_id", sort=True):
        group = group.set_index("timestamp_utc").sort_index()
        timeline = pd.date_range(group.index.min(), group.index.max(), freq="h")
        group = group.reindex(timeline)
        features = pd.DataFrame(index=timeline)
        # Calendar variables are known before the target hour begins.
        features["hour_sin"] = np.sin(2 * np.pi * timeline.hour / 24)
        features["hour_cos"] = np.cos(2 * np.pi * timeline.hour / 24)
        features["year_sin"] = np.sin(2 * np.pi * timeline.dayofyear / 365.25)
        features["year_cos"] = np.cos(2 * np.pi * timeline.dayofyear / 365.25)
        features["weekend"] = (timeline.dayofweek >= 5).astype(int)
        for signal in signals:
            series = group[signal]
            for lag in (1, 24, 168):
                features[f"{signal}_lag_{lag}h"] = series.shift(lag)
            history = series.shift(1)
            features[f"{signal}_mean_24h"] = history.rolling(24, min_periods=24).mean()
            features[f"{signal}_std_24h"] = history.rolling(24, min_periods=24).std()
        if "temperature_c" in signals:
            past_temperature = group["temperature_c"].shift(1)
            features["temperature_c_min_24h"] = past_temperature.rolling(24, min_periods=24).min()
            features["temperature_c_max_24h"] = past_temperature.rolling(24, min_periods=24).max()
            features["temperature_c_change_24h"] = past_temperature - group["temperature_c"].shift(25)
            if "load_mw" in signals:
                features["temperature_x_load_lag_1h"] = past_temperature * group["load_mw"].shift(1)
                features["temperature_squared_lag_1h"] = past_temperature ** 2
        if "load_mw" in signals:
            features["load_change_1h"] = group.load_mw.shift(1) - group.load_mw.shift(2)
            features["load_change_24h"] = group.load_mw.shift(1) - group.load_mw.shift(25)
        if {"load_mw", "wind_mw", "solar_mw"}.issubset(signals):
            net_load = group.load_mw - group.wind_mw - group.solar_mw
            features["net_load_lag_1h"] = net_load.shift(1)
            features["net_load_change_1h"] = net_load.shift(1) - net_load.shift(2)
        if {"available_reserves_mw", "required_reserves_mw"}.issubset(signals):
            features["reserve_margin_lag_1h"] = (group.available_reserves_mw - group.required_reserves_mw).shift(1)
        feature_names = list(features.columns)
        features["target"] = group["target"] if "target" in group else np.nan
        features["location_id"] = location
        features["timestamp_utc"] = timeline
        # Other sensors may be missing; LightGBM handles missing predictors.
        # Core load history and the label must be observed to score this hour.
        features = features.dropna(subset=(["target"] if require_target else []) + ["load_mw_lag_1h", "load_mw_mean_24h"])
        pieces.append(features)
    result = pd.concat(pieces, ignore_index=True).sort_values(["timestamp_utc", "location_id"])
    if result.empty:
        raise ValueError("No usable labeled hours after checking observed labels and 24 hours of load history.")
    if require_target:
        result["target"] = result["target"].astype(int)
    return result.reset_index(drop=True), feature_names
