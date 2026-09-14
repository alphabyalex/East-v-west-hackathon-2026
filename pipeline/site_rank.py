import logging
from pathlib import Path
import pandas as pd
import numpy as np

from pipeline.common import ROOT, write_json

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

ZONE_SETTLEMENT_POINTS = {
    "CSWS": "AEPM_CSWS", "LES": "LES_LES", "OKGE": "OKGE_OKGE",
    "OPPD": "OPPD_OPPD", "SPS": "SPS_SPS", "WFEC": "WFEC_WFEC",
}

def get_actual_wind_absorption(location_id: str) -> dict:
    """
    Attempts to read Alex's real precomputed wind and carbon metrics from the live snapshots.
    Falls back gracefully to the mock generator for missing or incomplete zones.
    """
    import json
    snapshot_dir = Path(__file__).resolve().parent.parent / "data/processed/grid_impact/live_v1"
    
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

def compute_zone_rankings(exposure_parquet_path: Path, output_json_path: Path):
    """
    Computes a composite score per SPP zone and saves the ranked results to JSON.
    """
    if not exposure_parquet_path.exists():
        raise FileNotFoundError(f"Exposure data parquet not found at {exposure_parquet_path}")
        
    df_exposure = pd.read_parquet(exposure_parquet_path)
    
    # Aggregate exposure across the years per location_id
    # We take the mean p50_hours over the term as our primary risk indicator
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
    # Risk Score: lower exposure hours = better (closer to 1.0)
    max_p50 = df_rank["avg_p50_risk_hours"].max()
    min_p50 = df_rank["avg_p50_risk_hours"].min()
    if max_p50 == min_p50:
        df_rank["score_risk"] = 1.0
    else:
        df_rank["score_risk"] = 1.0 - (df_rank["avg_p50_risk_hours"] - min_p50) / (max_p50 - min_p50)
        
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
    
    write_json(output_json_path, {
        "operator": "SPP",
        "composite_weight_formula": f"{WEIGHT_RISK:.2g}*S_risk + {WEIGHT_WIND:.2g}*S_wind + {WEIGHT_CARBON:.2g}*S_carbon",
        "description": "Composite sustainability and grid compatibility score per SPP balancing authority zone.",
        "rankings": rankings_list
    })
    log.info(f"Successfully computed rankings and saved to {output_json_path}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    compute_zone_rankings(
        ROOT / "data/processed/exposure_by_location.parquet",
        ROOT / "data/processed/national_stack/zone_rankings.json"
    )
