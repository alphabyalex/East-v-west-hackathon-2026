"""Precompute ranking availability from shipped evidence, without synthetic scores.

The original composite requires annual exposure, annual wind potential and avoided
carbon. Current grid-impact snapshots report observed wind screening and associated
operational emissions, NOT avoided carbon. They cannot legitimately fill that
composite. Publish explicit exclusions and the supported wind observations instead
of random wind values, a fabricated emissions multiplier, or zero-filled ranks.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

from pipeline.common import ROOT, write_json
from pipeline.simulate import get_location_estimate
from api.pipeline_provider import PipelineEstimate, _annual_reference_issue
from api.grid_impact import (
    EVIDENCE_PREFIX, LIVE_SNAPSHOT_DIRECTORY, ZONE_SETTLEMENT_POINTS,
    read_live_grid_impact,
)


def compute_zone_rankings(exposure_parquet_path: Path, output_json_path: Path):
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


if __name__ == "__main__":
    result = compute_zone_rankings(
        ROOT / "data/processed/exposure_by_location.parquet",
        ROOT / "data/processed/national_stack/zone_rankings.json",
    )
    print(json.dumps({"status": result["status"], "ranked_locations": len(result["rankings"]),
                      "excluded_locations": len(result["excluded_locations"]),
                      "wind_reference_locations": [row["location_id"] for row in result["available_wind_evidence"]]}))
