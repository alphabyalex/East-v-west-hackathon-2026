"""Explain learned stress-proxy predictions without claiming physical causation.

Tree contributions are transformed through each existing sigmoid calibrator.
They explain its log odds, not additive percentage-point changes in probability.
Training-only comparison thresholds and held-out association counts are separate.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.neighbors import NearestNeighbors

from pipeline.common import fingerprint, write_json
from pipeline.train import chronological_split, predict_members

SIMILARITY_COLUMNS = ("temperature_c_lag_1h", "load_mw_lag_1h", "load_change_1h", "net_load_lag_1h", "wind_mw_lag_1h")
EXPLANATION_CODE_SHA256 = fingerprint(Path(__file__))


def feature_group(name):
    if name.startswith("temperature_x_load"):
        return "Temperature and demand together"
    if name.startswith("net_load"):
        return "Demand after wind and solar"
    if name.startswith("temperature"):
        return "Local temperature"
    if name.startswith("load_change"):
        return "Demand rising or falling"
    if name.startswith("load_"):
        return "SPP system demand"
    if name.startswith("wind"):
        return "SPP wind generation"
    if name.startswith("solar"):
        return "SPP solar generation"
    if "reserve" in name:
        return "Reserve availability"
    if "outage" in name:
        return "Generation outages"
    if "constraint" in name or "lmp" in name or "import" in name:
        return "Other grid conditions"
    return "Season and time of day"


def calibrated_contributions(bundle, query):
    names = bundle["feature_names"]
    values = query[names].to_numpy(dtype=float)
    contributions, probabilities = [], []
    for model, calibrator in bundle["members"]:
        raw = np.asarray(model.booster_.predict(values, pred_contrib=True))
        if raw.shape != (len(query), len(names) + 1) or list(calibrator.classes_) != [0, 1]:
            raise ValueError("Explanations require the fitted binary tree/sigmoid model.")
        adjusted = raw * float(calibrator.coef_[0, 0])
        adjusted[:, -1] += float(calibrator.intercept_[0])
        probability = calibrator.predict_proba(model.predict(values, raw_score=True).reshape(-1, 1))[:, 1]
        if not np.allclose(expit(adjusted.sum(axis=1)), probability, atol=1e-8):
            raise ValueError("Feature contributions do not reconstruct the calibrated prediction.")
        contributions.append(adjusted)
        probabilities.append(probability)
    return np.stack(contributions), np.column_stack(probabilities)


def pattern_associations(training, held_out):
    """Fixed candidate combinations; cutoffs use only the training window."""
    fields = {"heat": ("temperature_c_lag_1h", .9), "cold": ("temperature_c_lag_1h", .1),
              "load": ("load_mw_lag_1h", .9), "ramp": ("load_change_1h", .9),
              "net": ("net_load_lag_1h", .9), "wind": ("wind_mw_lag_1h", .1)}
    cutoffs = {key: float(training[column].quantile(q)) for key, (column, q) in fields.items()
               if column in training and training[column].notna().any()}
    definitions = [("High temperature with high demand", [("heat", ">="), ("load", ">=")]),
                   ("Cold temperature with high demand", [("cold", "<="), ("load", ">=")]),
                   ("High and rapidly rising demand", [("load", ">="), ("ramp", ">=")]),
                   ("High demand with low wind", [("load", ">="), ("wind", "<=")]),
                   ("High demand after wind and solar", [("net", ">=")])]
    rows = []
    for name, conditions in definitions:
        if any(key not in cutoffs for key, _ in conditions):
            continue
        mask = pd.Series(True, index=held_out.index)
        comparable = pd.Series(True, index=held_out.index)
        criteria = []
        for key, operator in conditions:
            column = fields[key][0]
            comparable &= held_out[column].notna()
            mask &= held_out[column].ge(cutoffs[key]) if operator == ">=" else held_out[column].le(cutoffs[key])
            criteria.append({"feature": column, "operator": operator, "value": cutoffs[key], "training_quantile": fields[key][1]})
        valid = held_out[comparable & held_out.target.isin([0, 1])]
        matched = held_out[mask & comparable & held_out.target.isin([0, 1])]
        rate = float(matched.target.mean()) if len(matched) else None
        baseline = float(valid.target.mean()) if len(valid) else None
        days = int(matched.timestamp_utc.dt.floor("D").nunique())
        supported = len(matched) >= 100 and days >= 20
        rows.append({"pattern": name, "criteria": criteria, "matched_area_hours": len(matched),
                     "distinct_days": days, "distinct_areas": int(matched.location_id.nunique()),
                     "stress_fraction": rate, "baseline_fraction": baseline,
                     "difference_percentage_points": (rate - baseline) * 100 if rate is not None and baseline is not None else None,
                     "support": "Observed association" if supported else "Limited historical support"})
    return rows


def similar_hours(training, selected, limit=8):
    columns = [name for name in SIMILARITY_COLUMNS if name in training and name in selected]
    reference = training.dropna(subset=columns).copy()
    if len(columns) < 3 or reference.empty:
        return [[] for _ in range(len(selected))]
    mean = reference[columns].mean()
    scale = reference[columns].std().replace(0, 1).fillna(1)
    model = NearestNeighbors(n_neighbors=min(128, len(reference)), n_jobs=1).fit((reference[columns] - mean) / scale)
    output = []
    for _, query in selected.iterrows():
        if query[columns].isna().any():
            output.append([])
            continue
        vector = pd.DataFrame([query[columns].to_numpy(dtype=float)], columns=columns)
        distances, indices = model.kneighbors((vector - mean) / scale)
        used, rows = set(), []
        for distance, index in zip(distances[0], indices[0]):
            rms = float(distance / np.sqrt(len(columns)))
            if rms > .75:
                break
            row = reference.iloc[index]
            # Neighboring hours do not become dozens of independent examples.
            key = (str(row.location_id), row.timestamp_utc.floor("D"))
            if key in used:
                continue
            used.add(key)
            rows.append({"area": str(row.location_id), "timestamp_utc": row.timestamp_utc.isoformat(),
                         "observed_stress_proxy": int(row.target), "distance_rms": rms,
                         "prior_temperature_c": float(row.temperature_c_lag_1h), "prior_spp_load_mw": float(row.load_mw_lag_1h)})
            if len(rows) == limit:
                break
        output.append(rows)
    return output


def explain_signals(bundle, training, held_out, query, model_version, *, limit=24):
    if query.empty:
        raise ValueError("No eligible hours to explain.")
    probability = predict_members(bundle, query).mean(axis=1)
    selected = query.assign(probability=probability).sort_values(["probability", "timestamp_utc"], ascending=[False, True]).head(limit).copy()
    contributions, members = calibrated_contributions(bundle, selected)
    groups = {}
    for index, feature in enumerate(bundle["feature_names"]):
        groups.setdefault(feature_group(feature), []).append(index)
    analogues = similar_hours(training, selected)
    examples = []
    for position, (_, row) in enumerate(selected.iterrows()):
        drivers = []
        for group, indices in groups.items():
            scores = contributions[:, position, indices].sum(axis=1)
            effect = float(scores.mean())
            if abs(effect) < 1e-8:
                continue
            drivers.append({"factor": group, "direction": "Raises model risk" if effect > 0 else "Lowers model risk",
                            "mean_log_odds_contribution": effect,
                            "member_direction_agreement": float((np.sign(scores) == np.sign(effect)).mean()),
                            "missing_input_count": int(sum(pd.isna(row[bundle["feature_names"][i]]) for i in indices)),
                            "inputs": {bundle["feature_names"][i]: float(row[bundle["feature_names"][i]]) if pd.notna(row[bundle["feature_names"][i]]) else None for i in indices}})
        drivers.sort(key=lambda item: abs(item["mean_log_odds_contribution"]), reverse=True)
        examples.append({"timestamp_utc": row.timestamp_utc.isoformat(), "probability": float(members[position].mean()),
                         "drivers": drivers, "similar_training_hours": analogues[position],
                         "prior_temperature_c": float(row.temperature_c_lag_1h) if pd.notna(row.temperature_c_lag_1h) else None,
                         "prior_spp_load_mw": float(row.load_mw_lag_1h), "confidence": "Low"})
    return {"model_version": model_version, "source_type": "model", "confidence": "Low",
            "explanation_code_sha256": EXPLANATION_CODE_SHA256,
            "patterns": pattern_associations(training, held_out), "hours": examples,
            "method": "Exact LightGBM tree contributions multiplied by each fitted sigmoid slope; intercept added to its baseline. Grouped calibrated log-odds contributions explain each member, not additive ensemble probability changes.",
            "ref": "https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.Booster.html#lightgbm.Booster.predict",
            "comparison_policy": "High/low means training 90th/10th percentile. Support label requires >=100 matched area-hours across >=20 distinct days; correlated areas/hours are not independent evidence.",
            "similarity_policy": "Training-only standardized temperature, SPP load, ramp, net load and wind; RMS distance <=0.75; at most one example per area/day. These are illustrative analogues, not independent trials or a confidence interval.",
            "limitations": ["Directions explain this trained proxy model; correlated predictors and interactions prevent a causal interpretation.",
                            "Pattern percentages are observed high-demand-proxy frequencies in the held-out window, not measured cutoff probabilities.",
                            "Missing reserves, outages and local constraints cannot be assigned learned warning effects by this model.",
                            "Historical examples use inputs ending before the target hour; real-time publication latency and future cutoff dates are unverified."]}


def explain_saved_run(run):
    frame = pd.read_parquet(run / "features.parquet")
    split = chronological_split(frame)
    card = json.loads((run / "model_card.json").read_text(encoding="utf-8"))
    report = explain_signals(joblib.load(run / "model.joblib"), split["train"], split["test"], split["test"], card["model_version"])
    report["input_hashes"] = {name: fingerprint(run / name) for name in ("model.joblib", "features.parquet", "model_card.json")}
    write_json(run / "warning_signs.json", report)
    return report


def signals_markdown(signals):
    lines = ["", "## Learned warning signs", "", "Confidence: Low. These are indicators of the modeled stress proxy, not physical cutoff rules.", "",
             "| Combination | Held-out area-hours | Distinct days | Stress frequency | Comparable baseline |",
             "|---|---:|---:|---:|---:|"]
    for row in signals["patterns"]:
        rate = "unknown" if row["stress_fraction"] is None else f"{row['stress_fraction']:.1%}"
        base = "unknown" if row["baseline_fraction"] is None else f"{row['baseline_fraction']:.1%}"
        lines.append(f"| {row['pattern']} | {row['matched_area_hours']} | {row['distinct_days']} | {rate} | {base} |")
    lines += ["", signals["comparison_policy"], "", "## Why selected historical hours received their scores", ""]
    for row in signals["hours"][:6]:
        factors = "; ".join(f"{item['factor']}: {item['direction'].lower()}" for item in row["drivers"][:4])
        lines.append(f"- {row['timestamp_utc']}: model proxy probability {row['probability']:.1%}. {factors}.")
    lines += ["", *["- " + value for value in signals["limitations"]], ""]
    return "\n".join(lines)
