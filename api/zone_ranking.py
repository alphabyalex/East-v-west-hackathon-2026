from pathlib import Path
import json
import logging
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Self

from .schemas import ContractModel, NonEmpty, NonNegative, Source

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent

class ZoneRankingItem(ContractModel):
    location_id: NonEmpty
    avg_p50_risk_hours: NonNegative
    avg_p90_risk_hours: NonNegative
    avg_p99_risk_hours: NonNegative
    avg_worst_contiguous_hours: NonNegative
    wind_absorption_mwh_per_year: NonNegative
    carbon_absorbed_tonnes_per_year: NonNegative
    wind_source_ref: NonEmpty
    score_risk: NonNegative
    score_wind: NonNegative
    score_carbon: NonNegative
    composite_score: NonNegative
    rank: int

class ZoneRankingsResponse(ContractModel):
    operator: Literal["SPP"]
    composite_weight_formula: NonEmpty
    description: NonEmpty
    rankings: list[ZoneRankingItem]

class ZoneRankingsError(Exception):
    """Signifies missing or malformed rankings data."""
    pass

def load_zone_rankings() -> ZoneRankingsResponse:
    """
    Loads and parses the precomputed SPP zone rankings from the JSON manifest.
    """
    path = ROOT_DIR / "data/processed/national_stack/zone_rankings.json"
    if not path.exists():
        raise ZoneRankingsError("Zone rankings JSON manifest is missing; run 'python -m pipeline.site_rank' first.")
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ZoneRankingsResponse(**data)
    except Exception as error:
        raise ZoneRankingsError(f"Malformed zone rankings schema: {error}") from error
