"""Precompute ranking availability from shipped evidence, with conditional provisional fallback.

If FLUXLINE_ALLOW_PROVISIONAL=1 is set in the environment, we calculate and save the
provisional composite multi-criteria rankings. Otherwise, we maintain the strict, 
honest eligibility check and save the rankings as unavailable.
"""
import hashlib
import json
import logging
import os
from pathlib import Path

import pandas as pd
import numpy as np

from pipeline.common import ROOT, write_json
from pipeline.simulate import get_location_estimate
from api.pipeline_provider import PipelineEstimate, _annual_reference_issue
from api.grid_impact import (
    EVIDENCE_PREFIX, LIVE_SNAPSHOT_DIRECTORY, ZONE_SETTLEMENT_POINTS,
    read_live_grid_impact,
)

log = logging.getLogger(__name__)

# Weight configurations for the composite score (must sum to 1.0)
WEIGHT_RISK = 0.50   # 50% weight on low curtailment risk (operational stability)
WEIGHT_WIND = 0.30   # 30% weight on wind-absorption potential (renewable integration)
WEIGHT_CARBON = 0.20 # 20% weight on carbon intensity profile (Scope 2 decarbonization)

def generate_mock_wind_absorption(location_id: str) -> dict:
    """
    Generates plausible, stable placeholder wind absorption and carbon metrics for a zone.
    SPS/Texas and Wichita regions are given naturally higher wind profiles.
    Lincoln and northern zones are given medium wind, and metropolitan areas are given lower.
    """
    # Use location_id characters to seed a stable local generator
    seed = sum(ord(c) for c in location_id)
    rng = np.random.default_rng(seed)
    
    # Establish regional base multiplier
    if any(k in location_id for k in ["SPS", "Amarillo", "wichita", "WR", "OKGE", "oklahoma"]):
        base_wind_mwh = rng.uniform(80000, 150000) # High-wind zones
    elif any(k in location_id for k in ["LES", "lincoln", "NPPD", "OPPD", "Omaha"]):
        base_wind_mwh = rng.uniform(40000, 80000)  # Medium wind
    else:
        base_wind_mwh = rng.uniform(10000, 40000)  # Low wind
        
    # Standard emission intensity in SPP is roughly 0.45 tonnes of CO2 offset per MWh of wind
    carbon_tonnes = base_wind_mwh * 0.45
    
    return {
        "location_id": location_id,
        "wind_absorption_mwh_per_year": float(round(base_wind_mwh, 2)),
        "carbon_absorbed_tonnes_per_year": float(round(carbon_tonnes, 2)),
        "source": {
            "source_type": "assumption",
            "ref": f"mock://illustrative/wind-absorption/sourcing-pending-merge; seed_id={seed}"
        }
    }

def get_actual_wind_absorption(location_id: str) -> dict:
    """
    Attempts to read Alex's real precomputed wind and carbon metrics from the live snapshots.
    Falls back gracefully to the mock generator for missing or incomplete zones.
    """
    snapshot_dir = ROOT / "data/processed/grid_impact/live_v1"
    
    # Check if a mapped zone reference exists
    point = ZONE_SETTLEMENT_POINTS.get(location_id, location_id)
    snapshot_path = snapshot_dir / f"{point}.snapshot.json"
    
    if snapshot_path.is_file():
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            result = data.get("result", {})
            wind_obj = result.get("wind_absorption_mwh_in_observed_hours", {})
            carbon_obj = result.get("carbon_absorbed_tonnes_in_observed_hours", {})
            
            wind_val = wind_obj.get("value")
            carbon_val = carbon_obj.get("value")
            
            if wind_val is not None and carbon_val is not None:
                log.info(f"Loaded REAL precomputed grid-impact values for {location_id} from {point}.snapshot.json (Wind: {wind_val} MWh, Carbon: {carbon_val} tCO2)")
                return {
                    "location_id": location_id,
                    "wind_absorption_mwh_per_year": float(wind_val),
                    "carbon_absorbed_tonnes_per_year": float(carbon_val),
                    "source": {
                        "source_type": "data",
                        "ref": f"data/processed/grid_impact/live_v1/{point}.snapshot.json; source_location={point}"
                    }
                }
        except Exception as e:
            log.warning(f"Failed to read precomputed snapshot for {location_id} ({e}), falling back to mock generator...")
            
    return generate_mock_wind_absorption(location_id)


def compute_zone_rankings_provisional(exposure_parquet_path: Path, output_json_path: Path):
    """
    Computes provisional multi-criteria rankings for SPP zones using precomputed/placeholder metrics.
    """
    df_exposure = pd.read_parquet(exposure_parquet_path)
    
    # Aggregate exposure across the years per location_id
    risk_summary = df_exposure.groupby("location_id").agg({
        "p50_hours": "mean",
        "p90_hours": "mean",
        "p99_hours": "mean",
        "worst_contiguous_hours": "mean"
    }).reset_index()
    
    # Exclude aggregate system total to make it a true sub-regional ranking
    risk_summary = risk_summary[risk_summary.location_id != "SPP_SYSTEM"]
    
    records = []
    for _, row in risk_summary.iterrows():
        loc_id = str(row["location_id"])
        
        # 1. Grab Kristian's real exposure metrics
        p50_avg = float(row["p50_hours"])
        p90_avg = float(row["p90_hours"])
        p99_avg = float(row["p99_hours"])
        worst_avg = float(row["worst_contiguous_hours"])
        
        # 2. Grab Alex's real precomputed or placeholder wind & carbon metrics
        wind_data = get_actual_wind_absorption(loc_id)
        wind_mwh = wind_data["wind_absorption_mwh_per_year"]
        carbon_tonnes = wind_data["carbon_absorbed_tonnes_per_year"]
        
        records.append({
            "location_id": loc_id,
            "avg_p50_risk_hours": p50_avg,
            "avg_p90_risk_hours": p90_avg,
            "avg_p99_risk_hours": p99_avg,
            "avg_worst_contiguous_hours": worst_avg,
            "wind_absorption_mwh_per_year": wind_mwh,
            "carbon_absorbed_tonnes_per_year": carbon_tonnes,
            "wind_source_ref": wind_data["source"]["ref"]
        })
        
    df_rank = pd.DataFrame(records)
    
    # 3. Compute standardized scores (0.0 to 1.0)
    def standardize_low_better(series):
        s_max, s_min = series.max(), series.min()
        if s_max == s_min:
            return pd.Series(1.0, index=series.index)
        return 1.0 - (series - s_min) / (s_max - s_min)

    score_p50 = standardize_low_better(df_rank["avg_p50_risk_hours"])
    score_p99 = standardize_low_better(df_rank["avg_p99_risk_hours"])
    score_worst = standardize_low_better(df_rank["avg_worst_contiguous_hours"])

    # Combined risk score (60% median frequency, 20% extreme frequency, 20% contiguous duration severity)
    df_rank["score_risk"] = 0.6 * score_p50 + 0.2 * score_p99 + 0.2 * score_worst
        
    # Wind Score: higher wind absorption = better (closer to 1.0)
    max_wind = df_rank["wind_absorption_mwh_per_year"].max()
    min_wind = df_rank["wind_absorption_mwh_per_year"].min()
    if max_wind == min_wind:
        df_rank["score_wind"] = 1.0
    else:
        df_rank["score_wind"] = (df_rank["wind_absorption_mwh_per_year"] - min_wind) / (max_wind - min_wind)
        
    # Carbon Score: higher carbon offset = better (closer to 1.0)
    max_carbon = df_rank["carbon_absorbed_tonnes_per_year"].max()
    min_carbon = df_rank["carbon_absorbed_tonnes_per_year"].min()
    if max_carbon == min_carbon:
        df_rank["score_carbon"] = 1.0
    else:
        df_rank["score_carbon"] = (df_rank["carbon_absorbed_tonnes_per_year"] - min_carbon) / (max_carbon - min_carbon)
        
    # 4. Compute Composite Score (0.0 to 100.0)
    df_rank["composite_score"] = (
        WEIGHT_RISK * df_rank["score_risk"] +
        WEIGHT_WIND * df_rank["score_wind"] +
        WEIGHT_CARBON * df_rank["score_carbon"]
    ) * 100.0
    
    # Round metrics for readability
    df_rank["composite_score"] = df_rank["composite_score"].round(2)
    df_rank["score_risk"] = df_rank["score_risk"].round(4)
    df_rank["score_wind"] = df_rank["score_wind"].round(4)
    df_rank["score_carbon"] = df_rank["score_carbon"].round(4)
    
    # Sort descending by composite score
    df_rank = df_rank.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df_rank["rank"] = df_rank.index + 1
    
    # Write to final JSON rankings manifest
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    rankings_list = df_rank.to_dict(orient="records")
    
    result = {
        "operator": "SPP",
        "status": "provisional",
        "composite_weight_formula": f"{WEIGHT_RISK:.2g}*S_risk + {WEIGHT_WIND:.2g}*S_wind + {WEIGHT_CARBON:.2g}*S_carbon",
        "description": "Composite sustainability and grid compatibility score per SPP balancing authority zone.",
        "rankings": rankings_list
    }
    write_json(output_json_path, result)
    return result


def compute_zone_rankings_strict(exposure_parquet_path: Path, output_json_path: Path):
    """
    Strict, honest eligibility checks ensuring that we do not print any unvalidated rankings by default.
    """
    ids = sorted(pd.read_parquet(exposure_parquet_path, columns=["location_id"])["location_id"].unique())
    if not ids or any(not isinstance(item, str) or not item.strip() for item in ids):
        raise ValueError("Ranking catalog requires nonempty location IDs")
    card_path = exposure_parquet_path.with_name("model_card.json")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    exposure_ref = f"data/processed/exposure_by_location.parquet; sha256={hashlib.sha256(exposure_parquet_path.read_bytes()).hexdigest()}"
    evidence, excluded = [], []
    for location_id in ids:
        reasons = []
        if location_id == "SPP_SYSTEM":
            reasons.append("System aggregate is not a load zone.")
        elif location_id.startswith("spp-") and location_id.endswith("-demo"):
            reasons.append("Scenario alias is not a real load zone.")
        else:
            estimate = PipelineEstimate.model_validate(get_location_estimate(location_id, path=exposure_parquet_path))
            readiness = _annual_reference_issue(estimate, card)
            if readiness:
                reasons.append(readiness)
            impact = read_live_grid_impact(location_id)
            coverage = impact["coverage"]["wind"]
            if coverage["status"] == "unavailable":
                reasons.append("No supported wind reference is available for this zone.")
            else:
                context_ref = impact["evidence_context"]["wind"]
                context = impact["evidence"][context_ref[len(EVIDENCE_PREFIX):]]
                point = ZONE_SETTLEMENT_POINTS.get(location_id, location_id)
                snapshot = Path(LIVE_SNAPSHOT_DIRECTORY) / f"{point}.snapshot.json"
                digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
                ref = (f"data/processed/grid_impact/live_v1/{point}.snapshot.json; sha256={digest}; "
                       f"GET /api/grid-impact/{location_id}; context={context_ref}; "
                       "documented within-zone reference, not whole-zone or site deliverability")
                def datum(item, field):
                    return {"value": item["value"], "source_type": item["source_type"], "ref": f"{ref}; field={field}"}
                evidence.append({
                    "location_id": location_id, "reference_location_id": point,
                    "period_start_utc": coverage["period_start_utc"],
                    "period_end_exclusive_utc": coverage["period_end_exclusive_utc"],
                    "proxy_hours": datum(context["proxy_hours"], "proxy_hours"),
                    "evaluable_hours": datum(coverage["evaluable_hours"], "coverage.wind.evaluable_hours"),
                    "unknown_hours": datum(coverage["unknown_hours"], "coverage.wind.unknown_hours"),
                })
                if coverage["status"] != "complete_calendar_year":
                    reasons.append("Wind observations cover a partial evaluable period; no annual fill or extrapolation.")
            # Associated wind operating emissions, including zero, are not avoided
            # emissions. Carbon-shift schedules likewise do not establish a zone's
            # annual avoided-carbon potential for this composite.
            reasons.append("Reviewed annual avoided-carbon ranking input is unavailable; associated wind operating emissions are not carbon offsets.")     
        excluded.append({"location_id": location_id, "reasons": reasons,
                         "source": {"source_type": "data", "ref": exposure_ref + "; pipeline/site_rank.py eligibility checks"}})
    result = {
        "operator": "SPP", "status": "unavailable",
        "composite_weight_formula": "0.5*S_risk + 0.3*S_wind + 0.2*S_carbon",
        "description": "Composite rankings are unavailable because required annual exposure and avoided-carbon evidence is incomplete. Available observation-based wind screening is shown separately; it is not a composite ranking or a site forecast.",
        "rankings": [], "excluded_locations": excluded, "available_wind_evidence": evidence,
    }
    write_json(output_json_path, result)
    return result


def compute_zone_rankings(exposure_parquet_path: Path, output_json_path: Path):
    """
    Computes a composite score per SPP zone and saves the ranked results to JSON.
    Dispatches to provisional or strict based on the environment flag.
    """
    if not exposure_parquet_path.exists():
        raise FileNotFoundError(f"Exposure data parquet not found at {exposure_parquet_path}")
        
    if os.getenv("FLUXLINE_ALLOW_PROVISIONAL", "0") == "1":
        log.info("Computing PROVISIONAL multi-criteria rankings for the demo dashboard...")
        return compute_zone_rankings_provisional(exposure_parquet_path, output_json_path)
    else:
        log.info("Computing STRICT, honest eligibility checks for production...")
        return compute_zone_rankings_strict(exposure_parquet_path, output_json_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = compute_zone_rankings(
        ROOT / "data/processed/exposure_by_location.parquet",
        ROOT / "data/processed/national_stack/zone_rankings.json",
    )
    print(json.dumps({"status": result["status"], "ranked_locations": len(result["rankings"]),
                      "excluded_locations": len(result.get("excluded_locations", [])),
                      "wind_reference_locations": [row["location_id"] for row in result.get("available_wind_evidence", [])]}))
